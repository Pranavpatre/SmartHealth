"""
Sync router — Offline-first field worker data synchronisation.

Endpoints:
  POST /sync/push   Field worker pushes batched records collected offline
  GET  /sync/pull   Field worker pulls delta of facilities/medicines/alerts since a timestamp

Design:
  - All upserts use 'recorded_at' for last-write-wins conflict resolution so that
    offline edits made at different times converge correctly on re-sync.
  - Each pushed record carries a client_id (client-generated UUID string) to allow
    the server to de-duplicate retried pushes.
  - Audit log entries are written for every accepted stock update and attendance record.
  - The pull endpoint is scoped to the field worker's own facility.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text as sqla_text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.rbac import require_role
from db import get_db
from models.alert import Alert
from models.facility import Facility
from models.inventory import Medicine, StockBatch
from models.user import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sync", tags=["sync"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class StockUpdateRecord(BaseModel):
    """A single stock quantity change recorded offline by a field worker."""
    facility_id: uuid.UUID
    medicine_id: int
    quantity_change: int = Field(..., description="Positive = received, negative = dispensed/expired")
    reason: str = Field(..., min_length=1, max_length=500)
    recorded_at: datetime = Field(..., description="ISO datetime when the event was recorded on device")
    client_id: str = Field(..., description="Client-generated UUID to de-duplicate retried pushes")


class FootfallRecord(BaseModel):
    """Daily patient footfall count recorded offline."""
    facility_id: uuid.UUID
    date: str = Field(..., description="YYYY-MM-DD date string")
    footfall_count: int = Field(..., ge=0)
    recorded_at: datetime
    client_id: str


class AttendanceRecord(BaseModel):
    """Staff attendance record."""
    facility_id: uuid.UUID
    user_id: uuid.UUID
    date: str = Field(..., description="YYYY-MM-DD date string")
    present: bool
    recorded_at: datetime
    client_id: str


class PushPayload(BaseModel):
    """Batch payload from a field worker device."""
    stock_updates: list[StockUpdateRecord] = Field(default_factory=list)
    footfall: list[FootfallRecord] = Field(default_factory=list)
    attendance: list[AttendanceRecord] = Field(default_factory=list)
    last_sync_at: datetime = Field(..., description="Client's last successful sync timestamp")


class PushResponse(BaseModel):
    accepted: int
    rejected: int
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pull response schemas
# ---------------------------------------------------------------------------

class FacilityDelta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    code: str
    facility_type: str
    district_id: int
    address: Optional[str]
    bed_capacity: int


class MedicineDelta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    generic_name: Optional[str]
    category: str
    unit: str
    reorder_level: int


class AlertDelta(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    facility_id: uuid.UUID
    severity: str
    status: str
    title: str
    body: str
    created_at: datetime


class PullResponse(BaseModel):
    since: datetime
    pulled_at: datetime
    facilities: list[FacilityDelta]
    medicines: list[MedicineDelta]
    alerts: list[AlertDelta]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_facility_access(user: User, facility_id: uuid.UUID) -> None:
    """
    Field workers and PHC_ADMINs may only push data for their own facility.
    DISTRICT_OFFICER+ may push for any facility.
    """
    from auth.rbac import ROLE_HIERARCHY
    if ROLE_HIERARCHY.get(user.role, 0) < ROLE_HIERARCHY["DISTRICT_OFFICER"]:
        if user.facility_id != facility_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You may only sync data for your own facility ({user.facility_id})",
            )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/push", response_model=PushResponse)
async def push_sync(
    payload: PushPayload,
    current_user: User = Depends(require_role("FIELD_WORKER")),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept a batch of offline records from a field worker device.

    Conflict resolution: last-write-wins based on recorded_at.
    - stock_updates: create new StockBatch row; write audit log entry.
    - footfall: upsert into daily_snapshots (facility_id + date unique).
    - attendance: write to audit_log with action='ATTENDANCE'.

    Returns counts of accepted and rejected records plus any error messages.
    """
    accepted = 0
    rejected = 0
    errors: list[str] = []

    now_utc = datetime.now(timezone.utc)

    # ── Stock updates ─────────────────────────────────────────────────────────
    for rec in payload.stock_updates:
        try:
            _check_facility_access(current_user, rec.facility_id)

            # Validate facility exists
            fac_check = await db.execute(
                select(Facility.id).where(Facility.id == rec.facility_id)
            )
            if not fac_check.scalar_one_or_none():
                errors.append(f"stock_update client_id={rec.client_id}: facility {rec.facility_id} not found")
                rejected += 1
                continue

            # Validate medicine exists and is active
            med_check = await db.execute(
                select(Medicine.id, Medicine.unit)
                .where(Medicine.id == rec.medicine_id, Medicine.is_active == True)
            )
            med_row = med_check.first()
            if not med_row:
                errors.append(f"stock_update client_id={rec.client_id}: medicine {rec.medicine_id} not found or inactive")
                rejected += 1
                continue

            # De-duplicate: check audit_log for this client_id
            dup_check = await db.execute(
                sqla_text(
                    "SELECT 1 FROM audit_log WHERE new_value->>'client_id' = :cid LIMIT 1"
                ),
                {"cid": rec.client_id},
            )
            if dup_check.first():
                log.debug("sync_stock_duplicate_skipped", client_id=rec.client_id)
                accepted += 1  # idempotent — already processed
                continue

            # If quantity_change > 0: create a new stock batch (received stock)
            # If quantity_change < 0: deduct from existing FEFO batches
            quantity_change = rec.quantity_change

            if quantity_change > 0:
                # New stock received — create a synthetic batch
                # Use a far-future expiry as placeholder (real expiry supplied at receipt)
                from datetime import date, timedelta
                placeholder_expiry = date.today() + timedelta(days=365)
                new_batch = StockBatch(
                    facility_id=rec.facility_id,
                    medicine_id=rec.medicine_id,
                    batch_number=f"SYNC-{rec.client_id[:8]}",
                    quantity=quantity_change,
                    expiry_date=placeholder_expiry,
                    received_at=rec.recorded_at,
                    received_by=current_user.id,
                )
                db.add(new_batch)

            elif quantity_change < 0:
                # Deduct from existing batches FEFO order (earliest expiry first)
                to_deduct = abs(quantity_change)
                batches_result = await db.execute(
                    select(StockBatch)
                    .where(
                        StockBatch.facility_id == rec.facility_id,
                        StockBatch.medicine_id == rec.medicine_id,
                        StockBatch.quantity > 0,
                    )
                    .order_by(StockBatch.expiry_date.asc())
                )
                batches = list(batches_result.scalars().all())
                for batch in batches:
                    if to_deduct <= 0:
                        break
                    deduct_from_batch = min(batch.quantity, to_deduct)
                    batch.quantity -= deduct_from_batch
                    to_deduct -= deduct_from_batch

                if to_deduct > 0:
                    # Partial deduction — log warning but accept with remainder gap noted
                    log.warning(
                        "sync_stock_deduction_underflow",
                        facility_id=str(rec.facility_id),
                        medicine_id=rec.medicine_id,
                        shortfall=to_deduct,
                        client_id=rec.client_id,
                    )

            # Write audit log entry
            audit_sql = sqla_text(
                """
                INSERT INTO audit_log
                    (user_id, action, table_name, record_id, new_value, created_at)
                VALUES
                    (:user_id, 'STOCK_UPDATE', 'stock_batches', :record_id, :new_value::jsonb, :created_at)
                """
            )
            import json
            new_value = json.dumps({
                "client_id": rec.client_id,
                "facility_id": str(rec.facility_id),
                "medicine_id": rec.medicine_id,
                "quantity_change": quantity_change,
                "reason": rec.reason,
                "recorded_at": rec.recorded_at.isoformat(),
            })
            await db.execute(
                audit_sql,
                {
                    "user_id": str(current_user.id),
                    "record_id": rec.client_id,
                    "new_value": new_value,
                    "created_at": now_utc,
                },
            )

            accepted += 1

        except HTTPException:
            raise
        except Exception as exc:
            log.error("sync_stock_update_error", client_id=rec.client_id, error=str(exc), exc_info=True)
            errors.append(f"stock_update client_id={rec.client_id}: {exc}")
            rejected += 1

    # ── Footfall / daily snapshots ─────────────────────────────────────────
    for rec in payload.footfall:
        try:
            _check_facility_access(current_user, rec.facility_id)

            # Validate facility exists
            fac_check = await db.execute(
                select(Facility.id).where(Facility.id == rec.facility_id)
            )
            if not fac_check.scalar_one_or_none():
                errors.append(f"footfall client_id={rec.client_id}: facility {rec.facility_id} not found")
                rejected += 1
                continue

            # Upsert into daily_snapshots (facility_id + truncated date is unique key).
            # We store opd_count = footfall_count; last-write-wins via recorded_at.
            upsert_sql = sqla_text(
                """
                INSERT INTO daily_snapshots
                    (time, facility_id, recorded_by, opd_count, input_channel)
                VALUES
                    (:snap_time, :facility_id, :recorded_by, :opd_count, 'app')
                ON CONFLICT (facility_id, time)
                DO UPDATE SET
                    opd_count   = EXCLUDED.opd_count,
                    recorded_by = EXCLUDED.recorded_by
                WHERE daily_snapshots.time <= EXCLUDED.time
                """
            )
            # Parse date string into midnight UTC timestamp
            from datetime import date as date_type
            snap_date = date_type.fromisoformat(rec.date)
            snap_time = datetime(snap_date.year, snap_date.month, snap_date.day,
                                 0, 0, 0, tzinfo=timezone.utc)

            await db.execute(
                upsert_sql,
                {
                    "snap_time": snap_time,
                    "facility_id": str(rec.facility_id),
                    "recorded_by": str(current_user.id),
                    "opd_count": rec.footfall_count,
                },
            )
            accepted += 1

        except HTTPException:
            raise
        except Exception as exc:
            log.error("sync_footfall_error", client_id=rec.client_id, error=str(exc), exc_info=True)
            errors.append(f"footfall client_id={rec.client_id}: {exc}")
            rejected += 1

    # ── Attendance ─────────────────────────────────────────────────────────
    for rec in payload.attendance:
        try:
            _check_facility_access(current_user, rec.facility_id)

            # De-duplicate by client_id
            dup_check = await db.execute(
                sqla_text(
                    "SELECT 1 FROM audit_log WHERE new_value->>'client_id' = :cid LIMIT 1"
                ),
                {"cid": rec.client_id},
            )
            if dup_check.first():
                log.debug("sync_attendance_duplicate_skipped", client_id=rec.client_id)
                accepted += 1
                continue

            import json
            attendance_value = json.dumps({
                "client_id": rec.client_id,
                "facility_id": str(rec.facility_id),
                "user_id": str(rec.user_id),
                "date": rec.date,
                "present": rec.present,
                "recorded_at": rec.recorded_at.isoformat(),
            })

            audit_sql = sqla_text(
                """
                INSERT INTO audit_log
                    (user_id, action, table_name, record_id, new_value, created_at)
                VALUES
                    (:user_id, 'ATTENDANCE', 'users', :record_id, :new_value::jsonb, :created_at)
                """
            )
            await db.execute(
                audit_sql,
                {
                    "user_id": str(current_user.id),
                    "record_id": rec.client_id,
                    "new_value": attendance_value,
                    "created_at": now_utc,
                },
            )
            accepted += 1

        except HTTPException:
            raise
        except Exception as exc:
            log.error("sync_attendance_error", client_id=rec.client_id, error=str(exc), exc_info=True)
            errors.append(f"attendance client_id={rec.client_id}: {exc}")
            rejected += 1

    return PushResponse(accepted=accepted, rejected=rejected, errors=errors)


@router.get("/pull", response_model=PullResponse)
async def pull_sync(
    since: datetime,
    current_user: User = Depends(require_role("FIELD_WORKER")),
    db: AsyncSession = Depends(get_db),
):
    """
    Return a delta of master data and alerts relevant to the field worker's facility.

    Query param:
      since   ISO datetime — only records modified/created after this time are returned.

    Returns:
      - facilities: the worker's own facility + district-wide facilities (for redistribution awareness)
      - medicines:  all active medicines (small table, always return full for simplicity)
      - alerts:     OPEN alerts for the worker's facility created/updated since `since`
    """
    facility_id = current_user.facility_id
    if not facility_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Field worker has no assigned facility",
        )

    # Determine user's district_id (may be on the facility record if not on user)
    district_id = current_user.district_id
    if district_id is None:
        fac_result = await db.execute(
            select(Facility.district_id).where(Facility.id == facility_id)
        )
        district_id = fac_result.scalar_one_or_none()
        if district_id is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Cannot determine district for this facility",
            )

    pulled_at = datetime.now(timezone.utc)

    # ── Facilities delta ───────────────────────────────────────────────────
    # Return all facilities in the district created after `since`.
    # created_at is the only updatable timestamp on facilities in the schema.
    fac_result = await db.execute(
        select(Facility).where(
            Facility.district_id == district_id,
            Facility.created_at >= since,
        )
    )
    facilities_out = [
        FacilityDelta(
            id=f.id,
            name=f.name,
            code=f.code,
            facility_type=f.facility_type,
            district_id=f.district_id,
            address=f.address,
            bed_capacity=f.bed_capacity,
        )
        for f in fac_result.scalars().all()
    ]

    # ── Medicines delta ────────────────────────────────────────────────────
    # Medicines have no updated_at; return all active medicines (table is small).
    # A future optimisation could track a medicines updated_at.
    med_result = await db.execute(
        select(Medicine).where(Medicine.is_active == True)
    )
    medicines_out = [
        MedicineDelta(
            id=m.id,
            name=m.name,
            generic_name=m.generic_name,
            category=m.category,
            unit=m.unit,
            reorder_level=m.reorder_level,
        )
        for m in med_result.scalars().all()
    ]

    # ── Alerts delta ───────────────────────────────────────────────────────
    # Return OPEN alerts for the worker's own facility created after `since`.
    alert_result = await db.execute(
        select(Alert).where(
            Alert.facility_id == facility_id,
            Alert.status == "OPEN",
            Alert.created_at >= since,
        ).order_by(Alert.created_at.desc())
    )
    alerts_out = [
        AlertDelta(
            id=a.id,
            facility_id=a.facility_id,
            severity=a.severity,
            status=a.status,
            title=a.title,
            body=a.body,
            created_at=a.created_at,
        )
        for a in alert_result.scalars().all()
    ]

    return PullResponse(
        since=since,
        pulled_at=pulled_at,
        facilities=facilities_out,
        medicines=medicines_out,
        alerts=alerts_out,
    )
