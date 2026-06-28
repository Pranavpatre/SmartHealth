"""
facilities.py — FastAPI router for facility management.

Endpoints
---------
GET   /facilities                           — list facilities (scoped to user's district)
GET   /facilities/{facility_id}            — full detail with stock, scores, alerts
GET   /facilities/{facility_id}/stock      — current FEFO inventory per medicine
PATCH /facilities/{facility_id}/stock/{medicine_id}  — adjust stock quantity

Schema notes (from 001_core.sql / ORM models):
  Facility: id, district_id, code, name, facility_type, location, address,
            bed_capacity, created_at
  StockBatch: id, facility_id, medicine_id, batch_number, quantity,
              expiry_date, received_at, received_by
  Medicine:   id, name, generic_name, category, unit, reorder_level,
              lead_time_days, is_active
  FacilityHealthScore: time, facility_id, medicine_score, doctor_score,
                       bed_score, wait_time_score, diagnostics_score,
                       overall_score, status
  daily_snapshots (TimescaleDB): time, facility_id, opd_count, ipd_count,
                                 emergency_count, beds_occupied, …
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select, text as sa_text, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.rbac import require_role
from db import get_db
from models.alert import Alert
from models.facility import District, Facility
from models.health_score import FacilityHealthScore
from models.inventory import Medicine, StockBatch
from models.prediction import AIPrediction

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/facilities")

# ── Role guards ───────────────────────────────────────────────────────────────
_field_plus = require_role("FIELD_WORKER", "PHC_ADMIN", "DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN")
_phc_plus = require_role("PHC_ADMIN", "DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN")
_district_plus = require_role("DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN")


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class FacilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    district_id: int
    code: str
    name: str
    facility_type: str
    address: str | None
    bed_capacity: int
    created_at: datetime
    # Coordinates (extracted from the PostGIS geometry) for the dashboard map
    lat: float | None = None
    lng: float | None = None
    # Enrichment
    district_name: str | None = None
    latest_health_score: Decimal | None = None
    health_score_status: str | None = None   # GREEN | YELLOW | RED
    active_alert_count: int = 0
    # Aliases consumed by the dashboard frontend (FacilityMap / cards)
    health_score: float | None = None
    traffic_light: str | None = None
    active_alerts: int = 0


class HealthScoreBreakdown(BaseModel):
    time: datetime
    overall_score: Decimal | None
    status: str | None
    medicine_score: Decimal | None
    doctor_score: Decimal | None
    bed_score: Decimal | None
    wait_time_score: Decimal | None
    diagnostics_score: Decimal | None


class FootfallSnapshot(BaseModel):
    date: datetime
    opd_count: int
    ipd_count: int
    emergency_count: int
    beds_occupied: int


class AlertSummary(BaseModel):
    id: uuid.UUID
    severity: str
    status: str
    title: str
    created_at: datetime


class FacilityDetailResponse(FacilityResponse):
    """Extended detail: stock summary, footfall trend, health score, alerts."""

    total_medicine_types: int = 0
    total_stock_units: int = 0
    expiring_soon_count: int = 0          # batches expiring within 30 days
    health_score_breakdown: HealthScoreBreakdown | None = None
    footfall_7d: list[FootfallSnapshot] = Field(default_factory=list)
    recent_alerts: list[AlertSummary] = Field(default_factory=list)


class BatchDetail(BaseModel):
    batch_id: uuid.UUID
    batch_number: str | None
    quantity: int
    expiry_date: date
    received_at: datetime


class MedicineStockEntry(BaseModel):
    medicine_id: int
    medicine_name: str
    generic_name: str | None
    unit: str
    category: str
    reorder_level: int
    lead_time_days: int
    total_stock: int
    days_of_stock: float        # total_stock / avg_daily_consumption (fallback 1.0)
    expiring_soon_count: int    # batches expiring < 30 days
    below_reorder: bool
    batches: list[BatchDetail]  # FEFO order (earliest expiry first)


class StockUpdateRequest(BaseModel):
    adjustment: int = Field(..., description="Units to add (positive) or remove (negative)")
    reason: str = Field(..., min_length=3, max_length=500, description="Reason for adjustment")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_facility_or_404(facility_id: uuid.UUID, db: AsyncSession) -> Facility:
    """Load a Facility by PK or raise 404."""
    result = await db.execute(select(Facility).where(Facility.id == facility_id))
    facility = result.scalar_one_or_none()
    if facility is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Facility {facility_id} not found.",
        )
    return facility


async def _get_latest_health_score(
    facility_id: uuid.UUID, db: AsyncSession
) -> FacilityHealthScore | None:
    """Return the most recent health-score row for a facility."""
    stmt = (
        select(FacilityHealthScore)
        .where(FacilityHealthScore.facility_id == facility_id)
        .order_by(FacilityHealthScore.time.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _get_active_alert_count(facility_id: uuid.UUID, db: AsyncSession) -> int:
    stmt = select(func.count(Alert.id)).where(
        Alert.facility_id == facility_id,
        Alert.status == "OPEN",
    )
    return (await db.execute(stmt)).scalar_one() or 0


# ─────────────────────────────────────────────────────────────────────────────
# GET /facilities
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=list[FacilityResponse],
    summary="List facilities",
    status_code=status.HTTP_200_OK,
)
async def list_facilities(
    district_id: int | None = Query(None, description="Filter by district ID"),
    facility_type: str | None = Query(
        None, description="PHC | CHC | SUB_CENTRE | DISTRICT_HOSPITAL"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> list[FacilityResponse]:
    """
    List facilities scoped to the current user's district.

    SUPERADMIN and STATE_ADMIN may pass an explicit district_id to cross-scope.
    Includes latest health_score and active alert count per facility.
    """
    stmt = (
        select(
            Facility,
            District.name.label("district_name"),
            func.ST_Y(Facility.location).label("lat"),
            func.ST_X(Facility.location).label("lng"),
        )
        .join(District, District.id == Facility.district_id)
    )

    # ── Scope to user's district unless privileged ────────────────────────
    if current_user.role not in ("STATE_ADMIN", "SUPERADMIN"):
        if current_user.district_id is not None:
            stmt = stmt.where(Facility.district_id == current_user.district_id)
        elif current_user.facility_id is not None:
            stmt = stmt.where(Facility.id == current_user.facility_id)

    if district_id is not None:
        stmt = stmt.where(Facility.district_id == district_id)

    if facility_type is not None:
        valid_types = {"PHC", "CHC", "SUB_CENTRE", "DISTRICT_HOSPITAL"}
        if facility_type.upper() not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid facility_type. Must be one of: {', '.join(sorted(valid_types))}",
            )
        stmt = stmt.where(Facility.facility_type == facility_type.upper())

    offset = (page - 1) * page_size
    stmt = stmt.order_by(Facility.name).offset(offset).limit(page_size)
    rows = (await db.execute(stmt)).all()

    results: list[FacilityResponse] = []
    for facility, district_name, lat, lng in rows:
        hs = await _get_latest_health_score(facility.id, db)
        alert_count = await _get_active_alert_count(facility.id, db)
        score = float(hs.overall_score) if hs and hs.overall_score is not None else None
        status_light = hs.status if hs else None
        results.append(
            FacilityResponse(
                id=facility.id,
                district_id=facility.district_id,
                code=facility.code,
                name=facility.name,
                facility_type=facility.facility_type,
                address=facility.address,
                bed_capacity=facility.bed_capacity,
                created_at=facility.created_at,
                lat=float(lat) if lat is not None else None,
                lng=float(lng) if lng is not None else None,
                district_name=district_name,
                latest_health_score=hs.overall_score if hs else None,
                health_score_status=status_light,
                active_alert_count=alert_count,
                health_score=score,
                traffic_light=status_light,
                active_alerts=alert_count,
            )
        )

    log.info(
        "facilities_listed",
        user_id=str(current_user.id),
        count=len(results),
        district_id=district_id,
        facility_type=facility_type,
    )
    return results


# ─────────────────────────────────────────────────────────────────────────────
# GET /facilities/{facility_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{facility_id}",
    response_model=FacilityDetailResponse,
    summary="Facility detail",
    status_code=status.HTTP_200_OK,
)
async def get_facility(
    facility_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> FacilityDetailResponse:
    """
    Return full facility detail:
      - Facility info + district name
      - Current stock summary (total medicine types, total units, expiring-soon count)
      - Latest health score with component breakdown
      - 7-day historical footfall from daily_snapshots
      - 5 most recent open alerts
    """
    facility = await _get_facility_or_404(facility_id, db)

    # ── District name ─────────────────────────────────────────────────────
    district_row = (
        await db.execute(select(District.name).where(District.id == facility.district_id))
    ).scalar_one_or_none()

    # ── Stock summary: total medicine types, total stock units, expiring soon ─
    now_date = date.today()
    expiry_threshold = now_date + timedelta(days=30)

    stock_summary_stmt = select(
        func.count(func.distinct(StockBatch.medicine_id)).label("medicine_types"),
        func.coalesce(func.sum(StockBatch.quantity), 0).label("total_units"),
        func.count(StockBatch.id).filter(
            StockBatch.expiry_date < expiry_threshold,
            StockBatch.quantity > 0,
        ).label("expiring_soon"),
    ).where(
        StockBatch.facility_id == facility_id,
        StockBatch.quantity > 0,
        StockBatch.expiry_date >= now_date,
    )
    stock_row = (await db.execute(stock_summary_stmt)).one()
    total_medicine_types: int = stock_row.medicine_types or 0
    total_stock_units: int = int(stock_row.total_units or 0)
    expiring_soon_count: int = stock_row.expiring_soon or 0

    # ── Latest health score ───────────────────────────────────────────────
    hs = await _get_latest_health_score(facility_id, db)
    hs_breakdown: HealthScoreBreakdown | None = None
    if hs is not None:
        hs_breakdown = HealthScoreBreakdown(
            time=hs.time,
            overall_score=hs.overall_score,
            status=hs.status,
            medicine_score=hs.medicine_score,
            doctor_score=hs.doctor_score,
            bed_score=hs.bed_score,
            wait_time_score=hs.wait_time_score,
            diagnostics_score=hs.diagnostics_score,
        )

    # ── 7-day footfall from daily_snapshots ───────────────────────────────
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    footfall_rows = (
        await db.execute(
            sa_text(
                """
                SELECT time, opd_count, ipd_count, emergency_count, beds_occupied
                FROM daily_snapshots
                WHERE facility_id = :fid AND time >= :since
                ORDER BY time DESC
                """
            ),
            {"fid": str(facility_id), "since": seven_days_ago},
        )
    ).fetchall()

    footfall_7d = [
        FootfallSnapshot(
            date=row.time,
            opd_count=row.opd_count,
            ipd_count=row.ipd_count,
            emergency_count=row.emergency_count,
            beds_occupied=row.beds_occupied,
        )
        for row in footfall_rows
    ]

    # ── Recent 5 open alerts ──────────────────────────────────────────────
    alert_stmt = (
        select(Alert)
        .where(Alert.facility_id == facility_id)
        .order_by(Alert.created_at.desc())
        .limit(5)
    )
    recent_alert_rows = (await db.execute(alert_stmt)).scalars().all()
    recent_alerts = [
        AlertSummary(
            id=a.id,
            severity=a.severity,
            status=a.status,
            title=a.title,
            created_at=a.created_at,
        )
        for a in recent_alert_rows
    ]

    # ── Active alert count ────────────────────────────────────────────────
    active_alert_count = await _get_active_alert_count(facility_id, db)

    log.info(
        "facility_detail_fetched",
        facility_id=str(facility_id),
        user_id=str(current_user.id),
    )

    return FacilityDetailResponse(
        id=facility.id,
        district_id=facility.district_id,
        code=facility.code,
        name=facility.name,
        facility_type=facility.facility_type,
        address=facility.address,
        bed_capacity=facility.bed_capacity,
        created_at=facility.created_at,
        district_name=district_row,
        latest_health_score=hs.overall_score if hs else None,
        health_score_status=hs.status if hs else None,
        active_alert_count=active_alert_count,
        total_medicine_types=total_medicine_types,
        total_stock_units=total_stock_units,
        expiring_soon_count=expiring_soon_count,
        health_score_breakdown=hs_breakdown,
        footfall_7d=footfall_7d,
        recent_alerts=recent_alerts,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /facilities/{facility_id}/stock
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{facility_id}/stock",
    response_model=list[MedicineStockEntry],
    summary="Current FEFO inventory",
    status_code=status.HTTP_200_OK,
)
async def get_facility_stock(
    facility_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> list[MedicineStockEntry]:
    """
    Return current inventory for a facility.

    For each medicine:
      - total_stock   : sum of all non-expired, non-zero batches
      - reorder_level : from medicines table
      - days_of_stock : total_stock / avg_daily_consumption (fallback 1.0 if unknown)
      - expiring_soon_count: batches with expiry_date < today + 30 days
      - batches       : all active batches in FEFO order (earliest expiry first)

    Avg daily consumption is estimated from daily_snapshots OPD throughput
    as a heuristic proxy; falls back to 1.0 when insufficient data exists.
    """
    await _get_facility_or_404(facility_id, db)

    today = date.today()
    expiry_threshold = today + timedelta(days=30)

    # ── Fetch all active batches for this facility ─────────────────────────
    batch_stmt = (
        select(StockBatch, Medicine)
        .join(Medicine, Medicine.id == StockBatch.medicine_id)
        .where(
            StockBatch.facility_id == facility_id,
            StockBatch.quantity > 0,
            StockBatch.expiry_date >= today,
            Medicine.is_active == True,  # noqa: E712
        )
        .order_by(StockBatch.medicine_id, StockBatch.expiry_date.asc())  # FEFO
    )
    batch_rows = (await db.execute(batch_stmt)).all()

    # ── Group by medicine ─────────────────────────────────────────────────
    medicine_map: dict[int, dict[str, Any]] = {}
    for batch, medicine in batch_rows:
        mid = medicine.id
        if mid not in medicine_map:
            medicine_map[mid] = {
                "medicine": medicine,
                "batches": [],
                "total_stock": 0,
                "expiring_soon_count": 0,
            }
        medicine_map[mid]["batches"].append(batch)
        medicine_map[mid]["total_stock"] += batch.quantity
        if batch.expiry_date < expiry_threshold:
            medicine_map[mid]["expiring_soon_count"] += 1

    # ── Compute avg daily consumption from OPD trend (last 30 days) ───────
    # Heuristic: use a proportional share of OPD count per active medicine type.
    # This is a lightweight proxy; production systems should use a
    # dedicated dispensing/consumption table when available.
    avg_opd_row = await db.execute(
        sa_text(
            """
            SELECT AVG(opd_count) AS avg_opd
            FROM daily_snapshots
            WHERE facility_id = :fid
              AND time >= NOW() - INTERVAL '30 days'
            """
        ),
        {"fid": str(facility_id)},
    )
    avg_opd_val = avg_opd_row.scalar_one_or_none()
    avg_opd: float = float(avg_opd_val) if avg_opd_val else 0.0

    num_medicines = len(medicine_map) or 1
    # Conservative proxy: assume each medicine accounts for an equal share of OPD
    # with a dispensing factor of 0.5 (not every OPD visit results in a dispense).
    opd_derived_consumption = (avg_opd * 0.5) / num_medicines if avg_opd > 0 else 0.0

    # ── Build response entries ────────────────────────────────────────────
    entries: list[MedicineStockEntry] = []
    for mid, data in medicine_map.items():
        medicine: Medicine = data["medicine"]
        total_stock: int = data["total_stock"]
        expiring_soon: int = data["expiring_soon_count"]

        avg_daily = opd_derived_consumption if opd_derived_consumption > 0 else 1.0
        days_of_stock = round(total_stock / avg_daily, 1)

        batch_details = [
            BatchDetail(
                batch_id=b.id,
                batch_number=b.batch_number,
                quantity=b.quantity,
                expiry_date=b.expiry_date,
                received_at=b.received_at,
            )
            for b in data["batches"]
        ]

        entries.append(
            MedicineStockEntry(
                medicine_id=medicine.id,
                medicine_name=medicine.name,
                generic_name=medicine.generic_name,
                unit=medicine.unit,
                category=medicine.category,
                reorder_level=medicine.reorder_level,
                lead_time_days=medicine.lead_time_days,
                total_stock=total_stock,
                days_of_stock=days_of_stock,
                expiring_soon_count=expiring_soon,
                below_reorder=total_stock < medicine.reorder_level,
                batches=batch_details,
            )
        )

    # Sort by urgency: below-reorder first, then by days_of_stock ascending
    entries.sort(key=lambda e: (not e.below_reorder, e.days_of_stock))

    log.info(
        "facility_stock_fetched",
        facility_id=str(facility_id),
        user_id=str(current_user.id),
        medicine_count=len(entries),
    )
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /facilities/{facility_id}/stock/{medicine_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.patch(
    "/{facility_id}/stock/{medicine_id}",
    response_model=MedicineStockEntry,
    summary="Adjust stock quantity",
    status_code=status.HTTP_200_OK,
)
async def update_stock(
    facility_id: uuid.UUID,
    medicine_id: int,
    body: StockUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_phc_plus),
) -> MedicineStockEntry:
    """
    Apply a positive or negative stock adjustment for a given medicine at a facility.

    Strategy: apply the adjustment to the batch with the earliest expiry date
    (FEFO). If the adjustment exceeds a single batch, it cascades to subsequent
    batches. Positive adjustments create a new batch row.

    Writes old/new values to audit_log.
    Requires PHC_ADMIN or above.
    """
    logger = log.bind(
        facility_id=str(facility_id),
        medicine_id=medicine_id,
        user_id=str(current_user.id),
    )

    await _get_facility_or_404(facility_id, db)

    # Verify medicine exists
    med_result = await db.execute(
        select(Medicine).where(Medicine.id == medicine_id, Medicine.is_active == True)  # noqa: E712
    )
    medicine = med_result.scalar_one_or_none()
    if medicine is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Medicine {medicine_id} not found or inactive.",
        )

    today = date.today()

    # ── Fetch active batches in FEFO order ────────────────────────────────
    batches_stmt = (
        select(StockBatch)
        .where(
            StockBatch.facility_id == facility_id,
            StockBatch.medicine_id == medicine_id,
            StockBatch.quantity > 0,
            StockBatch.expiry_date >= today,
        )
        .order_by(StockBatch.expiry_date.asc())
    )
    batch_rows = (await db.execute(batches_stmt)).scalars().all()
    old_total = sum(b.quantity for b in batch_rows)

    adjustment = body.adjustment

    if adjustment == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Adjustment value must be non-zero.",
        )

    # ── Apply negative adjustment (FEFO depletion) ────────────────────────
    if adjustment < 0:
        remaining = abs(adjustment)
        if remaining > old_total:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Cannot remove {abs(adjustment)} units; only {old_total} units available."
                ),
            )
        for batch in batch_rows:
            if remaining <= 0:
                break
            if batch.quantity <= remaining:
                remaining -= batch.quantity
                await db.execute(
                    update(StockBatch).where(StockBatch.id == batch.id).values(quantity=0)
                )
            else:
                await db.execute(
                    update(StockBatch)
                    .where(StockBatch.id == batch.id)
                    .values(quantity=batch.quantity - remaining)
                )
                remaining = 0

    # ── Apply positive adjustment (new batch) ─────────────────────────────
    else:
        # Default expiry: lead_time_days + 365 days from today as a conservative estimate
        default_expiry = today + timedelta(days=medicine.lead_time_days + 365)
        new_batch = StockBatch(
            facility_id=facility_id,
            medicine_id=medicine_id,
            quantity=adjustment,
            expiry_date=default_expiry,
            received_by=current_user.id,
        )
        db.add(new_batch)

    await db.flush()

    # ── Compute new total for audit ───────────────────────────────────────
    new_total_row = await db.execute(
        select(func.coalesce(func.sum(StockBatch.quantity), 0)).where(
            StockBatch.facility_id == facility_id,
            StockBatch.medicine_id == medicine_id,
            StockBatch.quantity > 0,
            StockBatch.expiry_date >= today,
        )
    )
    new_total: int = int(new_total_row.scalar_one() or 0)

    # ── Write to audit_log ────────────────────────────────────────────────
    try:
        ip_addr = request.client.host if request.client else None
        await db.execute(
            sa_text(
                """
                INSERT INTO audit_log
                    (user_id, action, table_name, record_id, old_value, new_value, ip_address)
                VALUES
                    (:user_id, :action, :table_name, :record_id,
                     :old_value::jsonb, :new_value::jsonb, :ip_address::inet)
                """
            ),
            {
                "user_id": str(current_user.id),
                "action": "ADJUST_STOCK",
                "table_name": "stock_batches",
                "record_id": f"{facility_id}:{medicine_id}",
                "old_value": f'{{"total_quantity": {old_total}}}',
                "new_value": (
                    f'{{"total_quantity": {new_total}, '
                    f'"adjustment": {adjustment}, '
                    f'"reason": "{body.reason}"}}'
                ),
                "ip_address": ip_addr,
            },
        )
    except Exception as audit_exc:
        logger.warning("audit_log_write_failed", error=str(audit_exc))

    logger.info(
        "stock_adjusted",
        old_total=old_total,
        new_total=new_total,
        adjustment=adjustment,
        reason=body.reason,
    )

    # ── Reload and return updated stock entry ──────────────────────────────
    updated_batches_stmt = (
        select(StockBatch)
        .where(
            StockBatch.facility_id == facility_id,
            StockBatch.medicine_id == medicine_id,
            StockBatch.quantity > 0,
            StockBatch.expiry_date >= today,
        )
        .order_by(StockBatch.expiry_date.asc())
    )
    updated_batches = (await db.execute(updated_batches_stmt)).scalars().all()
    expiry_threshold = today + timedelta(days=30)
    expiring_soon = sum(1 for b in updated_batches if b.expiry_date < expiry_threshold)

    batch_details = [
        BatchDetail(
            batch_id=b.id,
            batch_number=b.batch_number,
            quantity=b.quantity,
            expiry_date=b.expiry_date,
            received_at=b.received_at,
        )
        for b in updated_batches
    ]

    return MedicineStockEntry(
        medicine_id=medicine.id,
        medicine_name=medicine.name,
        generic_name=medicine.generic_name,
        unit=medicine.unit,
        category=medicine.category,
        reorder_level=medicine.reorder_level,
        lead_time_days=medicine.lead_time_days,
        total_stock=new_total,
        days_of_stock=round(new_total / 1.0, 1),  # conservative fallback
        expiring_soon_count=expiring_soon,
        below_reorder=new_total < medicine.reorder_level,
        batches=batch_details,
    )
