"""
alerts.py — FastAPI router for alert management.

Endpoints
---------
GET    /alerts                      — paginated, filterable alert list
GET    /alerts/{alert_id}           — single alert detail
PATCH  /alerts/{alert_id}/acknowledge — acknowledge an open alert

Access control: all endpoints require DISTRICT_OFFICER or above.

Schema notes (from 001_core.sql / ORM models):
  Alert.severity  → alert_severity ENUM: INFO | WARNING | CRITICAL
  Alert.status    → alert_status  ENUM: OPEN | ACKNOWLEDGED | RESOLVED | SNOOZED
  Alert has no medicine_id column; medicine context lives on the linked AIPrediction.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.rbac import require_role
from db import get_db
from models.alert import Alert
from models.facility import Facility
from models.prediction import AIPrediction
from models.inventory import Medicine

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/alerts")

# ── Role guard reused across all endpoints ────────────────────────────────────
_district_plus = require_role("DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN")


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic response schemas
# ─────────────────────────────────────────────────────────────────────────────

class AlertResponse(BaseModel):
    """Flat projection of an alert row, enriched with human-readable names."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    facility_id: uuid.UUID
    prediction_id: uuid.UUID | None
    severity: str          # INFO | WARNING | CRITICAL
    status: str            # OPEN | ACKNOWLEDGED | RESOLVED | SNOOZED
    title: str
    body: str
    created_at: datetime
    acknowledged_by: uuid.UUID | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    # Enrichment fields — None when join data is unavailable
    facility_name: str | None = None
    medicine_name: str | None = None


class AlertListResponse(BaseModel):
    """Paginated wrapper for alert lists."""

    items: list[AlertResponse]
    total: int
    page: int
    page_size: int
    pages: int


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_alert_response(
    alert: Alert,
    facility_name: str | None,
    medicine_name: str | None,
) -> AlertResponse:
    """Assemble an AlertResponse from ORM row + joined name fields."""
    return AlertResponse(
        id=alert.id,
        facility_id=alert.facility_id,
        prediction_id=alert.prediction_id,
        severity=alert.severity,
        status=alert.status,
        title=alert.title,
        body=alert.body,
        created_at=alert.created_at,
        acknowledged_by=alert.acknowledged_by,
        acknowledged_at=alert.acknowledged_at,
        resolved_at=alert.resolved_at,
        facility_name=facility_name,
        medicine_name=medicine_name,
    )


async def _fetch_alert_or_404(
    alert_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[Alert, str | None, str | None]:
    """
    Return (alert, facility_name, medicine_name) or raise 404.
    Performs a single query with LEFT JOINs to ai_predictions and medicines.
    """
    stmt = (
        select(
            Alert,
            Facility.name.label("facility_name"),
            Medicine.name.label("medicine_name"),
        )
        .join(Facility, Facility.id == Alert.facility_id)
        .outerjoin(AIPrediction, AIPrediction.id == Alert.prediction_id)
        .outerjoin(Medicine, Medicine.id == AIPrediction.medicine_id)
        .where(Alert.id == alert_id)
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert {alert_id} not found.",
        )
    alert, facility_name, medicine_name = row
    return alert, facility_name, medicine_name


# ─────────────────────────────────────────────────────────────────────────────
# GET /alerts
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=AlertListResponse,
    summary="List alerts (paginated)",
    status_code=status.HTTP_200_OK,
)
async def list_alerts(
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    alert_status: str | None = Query(
        None,
        alias="status",
        description="Filter by status: OPEN | ACKNOWLEDGED | RESOLVED | SNOOZED",
    ),
    facility_id: uuid.UUID | None = Query(None, description="Filter by facility UUID"),
    severity: str | None = Query(
        None, description="Filter by severity: INFO | WARNING | CRITICAL"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_district_plus),
) -> AlertListResponse:
    """
    Return a paginated, filtered list of alerts sorted by created_at DESC.

    Requires DISTRICT_OFFICER, STATE_ADMIN, or SUPERADMIN.
    """
    logger = log.bind(user_id=str(current_user.id), endpoint="list_alerts")

    # ── Build base query ──────────────────────────────────────────────────
    base_stmt = (
        select(
            Alert,
            Facility.name.label("facility_name"),
            Medicine.name.label("medicine_name"),
        )
        .join(Facility, Facility.id == Alert.facility_id)
        .outerjoin(AIPrediction, AIPrediction.id == Alert.prediction_id)
        .outerjoin(Medicine, Medicine.id == AIPrediction.medicine_id)
    )

    # ── Apply filters ─────────────────────────────────────────────────────
    if alert_status is not None:
        valid_statuses = {"OPEN", "ACKNOWLEDGED", "RESOLVED", "SNOOZED"}
        if alert_status.upper() not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status '{alert_status}'. Must be one of: {', '.join(sorted(valid_statuses))}",
            )
        base_stmt = base_stmt.where(Alert.status == alert_status.upper())

    if facility_id is not None:
        base_stmt = base_stmt.where(Alert.facility_id == facility_id)

    if severity is not None:
        valid_severities = {"INFO", "WARNING", "CRITICAL"}
        if severity.upper() not in valid_severities:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(valid_severities))}",
            )
        base_stmt = base_stmt.where(Alert.severity == severity.upper())

    # ── Scope to current user's district (non-SUPERADMIN) ─────────────────
    if current_user.role not in ("STATE_ADMIN", "SUPERADMIN") and current_user.district_id is not None:
        base_stmt = base_stmt.where(Facility.district_id == current_user.district_id)

    # ── Count total matching rows ─────────────────────────────────────────
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total: int = (await db.execute(count_stmt)).scalar_one()

    # ── Fetch paginated results ───────────────────────────────────────────
    offset = (page - 1) * page_size
    data_stmt = base_stmt.order_by(Alert.created_at.desc()).offset(offset).limit(page_size)
    rows = (await db.execute(data_stmt)).all()

    items = [_build_alert_response(alert, fac_name, med_name) for alert, fac_name, med_name in rows]
    pages = max(1, (total + page_size - 1) // page_size)

    logger.info(
        "alerts_listed",
        total=total,
        page=page,
        page_size=page_size,
        filters={"status": alert_status, "facility_id": str(facility_id), "severity": severity},
    )

    return AlertListResponse(items=items, total=total, page=page, page_size=page_size, pages=pages)


# ─────────────────────────────────────────────────────────────────────────────
# GET /alerts/{alert_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Get alert by ID",
    status_code=status.HTTP_200_OK,
)
async def get_alert(
    alert_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_district_plus),
) -> AlertResponse:
    """
    Return full detail for a single alert.

    Raises 404 if the alert does not exist.
    Requires DISTRICT_OFFICER, STATE_ADMIN, or SUPERADMIN.
    """
    alert, facility_name, medicine_name = await _fetch_alert_or_404(alert_id, db)
    log.info("alert_fetched", alert_id=str(alert_id), user_id=str(current_user.id))
    return _build_alert_response(alert, facility_name, medicine_name)


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /alerts/{alert_id}/acknowledge
# ─────────────────────────────────────────────────────────────────────────────

@router.patch(
    "/{alert_id}/acknowledge",
    response_model=AlertResponse,
    summary="Acknowledge an alert",
    status_code=status.HTTP_200_OK,
)
async def acknowledge_alert(
    alert_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_district_plus),
) -> AlertResponse:
    """
    Transition an alert to ACKNOWLEDGED status.

    Sets:
      - status = ACKNOWLEDGED
      - acknowledged_by = current_user.id
      - acknowledged_at = now (UTC)

    Writes an entry to audit_log and broadcasts the update to all connected
    WebSocket clients via the app-level WebSocketManager.

    Raises 404 if alert not found.
    Raises 409 if alert is already ACKNOWLEDGED or RESOLVED.
    Requires DISTRICT_OFFICER, STATE_ADMIN, or SUPERADMIN.
    """
    logger = log.bind(alert_id=str(alert_id), user_id=str(current_user.id))

    # ── Fetch current state (with names for response) ─────────────────────
    alert, facility_name, medicine_name = await _fetch_alert_or_404(alert_id, db)

    if alert.status in ("ACKNOWLEDGED", "RESOLVED"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Alert is already {alert.status}. Cannot acknowledge.",
        )

    now_utc = datetime.now(timezone.utc)
    old_status = alert.status

    # ── Update the alert row ──────────────────────────────────────────────
    await db.execute(
        update(Alert)
        .where(Alert.id == alert_id)
        .values(
            status="ACKNOWLEDGED",
            acknowledged_by=current_user.id,
            acknowledged_at=now_utc,
        )
    )

    # ── Write to audit_log ────────────────────────────────────────────────
    try:
        from sqlalchemy import text as sa_text

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
                "action": "ACKNOWLEDGE_ALERT",
                "table_name": "alerts",
                "record_id": str(alert_id),
                "old_value": f'{{"status": "{old_status}"}}',
                "new_value": f'{{"status": "ACKNOWLEDGED", "acknowledged_by": "{current_user.id}"}}',
                "ip_address": ip_addr,
            },
        )
    except Exception as audit_exc:
        # Audit failure must not roll back the acknowledgement
        logger.warning("audit_log_write_failed", error=str(audit_exc))

    # ── Flush and refresh so the response reflects updated values ─────────
    await db.flush()

    refreshed_stmt = select(Alert).where(Alert.id == alert_id)
    refreshed_alert = (await db.execute(refreshed_stmt)).scalar_one()

    # ── Broadcast WebSocket event ─────────────────────────────────────────
    ws_manager = getattr(request.app.state, "ws_manager", None)
    if ws_manager is not None:
        try:
            await ws_manager.broadcast(
                {
                    "event": "alert_acknowledged",
                    "alert_id": str(alert_id),
                    "facility_id": str(refreshed_alert.facility_id),
                    "acknowledged_by": str(current_user.id),
                    "acknowledged_at": now_utc.isoformat(),
                }
            )
        except Exception as ws_exc:
            logger.warning("websocket_broadcast_failed", error=str(ws_exc))

    logger.info(
        "alert_acknowledged",
        old_status=old_status,
        acknowledged_at=now_utc.isoformat(),
    )

    return _build_alert_response(refreshed_alert, facility_name, medicine_name)
