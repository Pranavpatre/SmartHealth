"""
Redistribution router — Resource redistribution plans for district health facilities.

Endpoints:
  GET  /redistribution/plans               list plans (district-scoped, paginated, filterable by status)
  POST /redistribution/plans               run solver, persist + return new plan
  GET  /redistribution/plans/{plan_id}     full plan detail with line items + names
  POST /redistribution/plans/{plan_id}/approve   approve plan, queue notifications, broadcast WS
  POST /redistribution/plans/{plan_id}/defer     defer plan with reason
"""

from __future__ import annotations

import logging
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.rbac import require_role
from celery_app import celery_app
from db import get_db
from models.alert import Alert
from models.facility import Facility
from models.inventory import Medicine, StockBatch
from models.prediction import AIPrediction
from models.redistribution import RedistributionItem, RedistributionPlan
from models.user import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/redistribution", tags=["redistribution"])

# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class LineItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    medicine_id: Optional[int]
    medicine_name: Optional[str] = None
    test_id: Optional[int]
    from_facility: uuid.UUID
    from_facility_name: Optional[str] = None
    to_facility: uuid.UUID
    to_facility_name: Optional[str] = None
    quantity: int
    distance_km: Optional[Decimal]
    estimated_cost: Optional[Decimal]
    estimated_saving: Optional[Decimal]
    status: str
    trigger_prediction: Optional[uuid.UUID]


class RedistributionPlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    district_id: int
    generated_at: datetime
    approved_by: Optional[uuid.UUID]
    approved_at: Optional[datetime]
    status: str
    total_savings: Optional[Decimal]
    notes: Optional[str]
    items: list[LineItemResponse] = Field(default_factory=list)


class DeferRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=1000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _traffic_light(score: Optional[Decimal]) -> str:
    """Convert overall health score to GREEN / YELLOW / RED."""
    if score is None:
        return "RED"
    f = float(score)
    if f >= 70:
        return "GREEN"
    if f >= 45:
        return "YELLOW"
    return "RED"


async def _build_plan_response(
    plan: RedistributionPlan,
    items: list[RedistributionItem],
    db: AsyncSession,
) -> RedistributionPlanResponse:
    """Enrich a plan with facility names and medicine names for response.

    ``items`` is passed in explicitly (already loaded within the async session)
    rather than read from ``plan.items`` — touching the lazy relationship on an
    AsyncSession triggers ``MissingGreenlet``.
    """
    # Collect IDs to look up
    facility_ids: set[uuid.UUID] = set()
    medicine_ids: set[int] = set()
    for item in items:
        facility_ids.add(item.from_facility)
        facility_ids.add(item.to_facility)
        if item.medicine_id is not None:
            medicine_ids.add(item.medicine_id)

    # Batch-load facilities
    fac_map: dict[uuid.UUID, str] = {}
    if facility_ids:
        result = await db.execute(
            select(Facility.id, Facility.name).where(Facility.id.in_(facility_ids))
        )
        fac_map = {row.id: row.name for row in result}

    # Batch-load medicines
    med_map: dict[int, str] = {}
    if medicine_ids:
        result = await db.execute(
            select(Medicine.id, Medicine.name).where(Medicine.id.in_(medicine_ids))
        )
        med_map = {row.id: row.name for row in result}

    items_out: list[LineItemResponse] = []
    for item in items:
        items_out.append(
            LineItemResponse(
                id=item.id,
                plan_id=item.plan_id,
                medicine_id=item.medicine_id,
                medicine_name=med_map.get(item.medicine_id) if item.medicine_id else None,
                test_id=item.test_id,
                from_facility=item.from_facility,
                from_facility_name=fac_map.get(item.from_facility),
                to_facility=item.to_facility,
                to_facility_name=fac_map.get(item.to_facility),
                quantity=item.quantity,
                distance_km=item.distance_km,
                estimated_cost=item.estimated_cost,
                estimated_saving=item.estimated_saving,
                status=item.status,
                trigger_prediction=item.trigger_prediction,
            )
        )

    return RedistributionPlanResponse(
        id=plan.id,
        district_id=plan.district_id,
        generated_at=plan.generated_at,
        approved_by=plan.approved_by,
        approved_at=plan.approved_at,
        status=plan.status,
        total_savings=plan.total_savings,
        notes=plan.notes,
        items=items_out,
    )


async def _load_plan_or_404(
    plan_id: uuid.UUID,
    district_id: int,
    db: AsyncSession,
) -> tuple[RedistributionPlan, list[RedistributionItem]]:
    result = await db.execute(
        select(RedistributionPlan).where(
            RedistributionPlan.id == plan_id,
            RedistributionPlan.district_id == district_id,
        )
    )
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Redistribution plan {plan_id} not found",
        )
    # Load items explicitly (avoid touching the lazy relationship on AsyncSession)
    items_result = await db.execute(
        select(RedistributionItem).where(RedistributionItem.plan_id == plan_id)
    )
    items = list(items_result.scalars().all())
    return plan, items


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/plans", response_model=dict)
async def list_plans(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    status_filter: Optional[str] = None,
    current_user: User = Depends(require_role("DISTRICT_OFFICER")),
    db: AsyncSession = Depends(get_db),
):
    """List redistribution plans for the current user's district, paginated."""
    district_id = current_user.district_id
    if not district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no associated district",
        )

    stmt = select(RedistributionPlan).where(
        RedistributionPlan.district_id == district_id
    )
    if status_filter:
        stmt = stmt.where(RedistributionPlan.status == status_filter.upper())

    stmt = stmt.order_by(RedistributionPlan.generated_at.desc())

    # Count
    from sqlalchemy import func as sqlfunc
    count_stmt = select(sqlfunc.count()).select_from(stmt.subquery())
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * page_size
    stmt = stmt.offset(offset).limit(page_size)
    plans_result = await db.execute(stmt)
    plans = list(plans_result.scalars().all())

    # Load items for each plan
    plan_responses = []
    for plan in plans:
        items_result = await db.execute(
            select(RedistributionItem).where(RedistributionItem.plan_id == plan.id)
        )
        items = list(items_result.scalars().all())
        plan_responses.append(await _build_plan_response(plan, items, db))

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "plans": [p.model_dump() for p in plan_responses],
    }


@router.post("/plans", response_model=RedistributionPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    request: Request,
    current_user: User = Depends(require_role("DISTRICT_OFFICER")),
    db: AsyncSession = Depends(get_db),
):
    """
    Run RedistributionSolver for the current user's district:
    1. Query all facilities + current stock + latest STOCKOUT predictions
    2. Build FacilityStock objects
    3. Call solver.solve()
    4. Persist RedistributionPlan + RedistributionItem rows
    5. Return full plan with line items
    """
    district_id = current_user.district_id
    if not district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no associated district",
        )

    # Import solver at call time — path is relative to the ML models directory
    try:
        sys.path.insert(0, "/app/ml-models/redistribution")
        from solver import FacilityStock, RedistributionSolver  # type: ignore[import]
    except ImportError as exc:
        log.error("redistribution_solver_import_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redistribution solver unavailable",
        ) from exc

    # 1. Load all facilities in district
    fac_result = await db.execute(
        select(Facility).where(Facility.district_id == district_id)
    )
    facilities: list[Facility] = list(fac_result.scalars().all())
    if not facilities:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No facilities found for this district",
        )
    fac_ids = [f.id for f in facilities]
    fac_by_id: dict[uuid.UUID, Facility] = {f.id: f for f in facilities}

    # 2. Load current aggregate stock per (facility, medicine)
    from sqlalchemy import text as sqla_text

    stock_sql = sqla_text(
        """
        SELECT
            sb.facility_id,
            sb.medicine_id,
            SUM(sb.quantity)    AS total_quantity
        FROM stock_batches sb
        WHERE sb.facility_id = ANY(:fac_ids)
          AND sb.quantity > 0
        GROUP BY sb.facility_id, sb.medicine_id
        """
    )
    stock_result = await db.execute(
        stock_sql, {"fac_ids": [str(fid) for fid in fac_ids]}
    )
    stock_rows = stock_result.mappings().all()

    # 3. Load medicine metadata (name, reorder_level)
    med_ids: set[int] = {row["medicine_id"] for row in stock_rows}
    med_map: dict[int, Medicine] = {}
    if med_ids:
        med_result = await db.execute(
            select(Medicine).where(Medicine.id.in_(med_ids), Medicine.is_active == True)
        )
        med_map = {m.id: m for m in med_result.scalars().all()}

    # 4. Load latest STOCKOUT predictions per (facility, medicine)
    pred_sql = sqla_text(
        """
        SELECT DISTINCT ON (facility_id, medicine_id)
            id, facility_id, medicine_id, predicted_value
        FROM ai_predictions
        WHERE facility_id = ANY(:fac_ids)
          AND prediction_type = 'STOCKOUT'
          AND medicine_id IS NOT NULL
        ORDER BY facility_id, medicine_id, predicted_at DESC
        """
    )
    pred_result = await db.execute(
        pred_sql, {"fac_ids": [str(fid) for fid in fac_ids]}
    )
    pred_rows = pred_result.mappings().all()
    # Map (facility_id, medicine_id) -> (days_until_stockout, prediction_id)
    pred_map: dict[tuple[str, int], tuple[int, uuid.UUID]] = {}
    for pr in pred_rows:
        days = int(pr["predicted_value"]) if pr["predicted_value"] is not None else 999
        pred_map[(str(pr["facility_id"]), pr["medicine_id"])] = (days, pr["id"])

    # 5. Build FacilityStock objects
    facility_stocks: list[FacilityStock] = []
    for row in stock_rows:
        fid = row["facility_id"]
        mid = row["medicine_id"]
        fac = fac_by_id.get(uuid.UUID(str(fid)))
        med = med_map.get(mid)
        if not fac or not med:
            continue

        # Extract lat/lng from PostGIS point — fallback to 0.0 if no geometry
        lat, lng = 0.0, 0.0
        if fac.location is not None:
            try:
                from geoalchemy2.shape import to_shape
                pt = to_shape(fac.location)
                lat, lng = pt.y, pt.x
            except Exception:
                pass

        days_until_stockout, _ = pred_map.get((str(fid), mid), (999, None))

        facility_stocks.append(
            FacilityStock(
                facility_id=str(fid),
                facility_name=fac.name,
                medicine_id=mid,
                medicine_name=med.name,
                current_stock=int(row["total_quantity"]),
                reorder_level=med.reorder_level,
                days_until_stockout=days_until_stockout,
                lat=lat,
                lng=lng,
            )
        )

    if not facility_stocks:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No valid facility-stock combinations found to optimise",
        )

    # 6. Run solver (only propose transfers within the configured radius)
    from config import get_settings
    solver = RedistributionSolver(max_distance_km=get_settings().redistribution_max_km)
    plan_result = solver.solve(facility_stocks)

    # 7. Persist plan
    total_savings = Decimal(str(plan_result.total_saving_inr))
    db_plan = RedistributionPlan(
        district_id=district_id,
        status="PENDING",
        total_savings=total_savings,
        notes=f"solver_status={plan_result.solver_status}; "
              f"facilities_helped={plan_result.facilities_helped}; "
              f"total_units_moved={plan_result.total_units_moved}",
    )
    db.add(db_plan)
    await db.flush()  # get db_plan.id before creating items

    # 8. Persist line items
    for transfer in plan_result.transfers:
        # Look up trigger prediction id
        trigger_pred_id: Optional[uuid.UUID] = None
        key = (transfer.to_facility_id, transfer.medicine_id)
        pred_entry = pred_map.get(key)
        if pred_entry:
            trigger_pred_id = pred_entry[1]

        item = RedistributionItem(
            plan_id=db_plan.id,
            medicine_id=transfer.medicine_id,
            from_facility=uuid.UUID(transfer.from_facility_id),
            to_facility=uuid.UUID(transfer.to_facility_id),
            quantity=transfer.quantity,
            distance_km=Decimal(str(transfer.distance_km)),
            estimated_cost=None,
            estimated_saving=Decimal(str(transfer.estimated_saving_inr)),
            status="PENDING",
            trigger_prediction=trigger_pred_id,
        )
        db.add(item)

    await db.flush()

    # Reload items for response
    items_result = await db.execute(
        select(RedistributionItem).where(RedistributionItem.plan_id == db_plan.id)
    )
    items = list(items_result.scalars().all())

    return await _build_plan_response(db_plan, items, db)


@router.get("/plans/{plan_id}", response_model=RedistributionPlanResponse)
async def get_plan(
    plan_id: uuid.UUID,
    current_user: User = Depends(require_role("DISTRICT_OFFICER")),
    db: AsyncSession = Depends(get_db),
):
    """Return a single redistribution plan with all line items, facility names, and medicine names."""
    district_id = current_user.district_id
    if not district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no associated district",
        )
    plan, items = await _load_plan_or_404(plan_id, district_id, db)
    return await _build_plan_response(plan, items, db)


@router.post("/plans/{plan_id}/approve", response_model=RedistributionPlanResponse)
async def approve_plan(
    plan_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(require_role("DISTRICT_OFFICER")),
    db: AsyncSession = Depends(get_db),
):
    """
    Approve a redistribution plan:
    1. Sets plan status=APPROVED, approved_by, approved_at
    2. Sets all line items status=APPROVED
    3. Queues Celery notification task
    4. Broadcasts WebSocket event
    5. Resolves linked stockout alerts
    """
    district_id = current_user.district_id
    if not district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no associated district",
        )

    plan, _items = await _load_plan_or_404(plan_id, district_id, db)

    if plan.status not in ("PENDING", "DEFERRED"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plan is in status '{plan.status}' and cannot be approved",
        )

    now = datetime.now(timezone.utc)
    plan.status = "APPROVED"
    plan.approved_by = current_user.id
    plan.approved_at = now

    # Approve all line items
    await db.execute(
        update(RedistributionItem)
        .where(RedistributionItem.plan_id == plan_id)
        .values(status="APPROVED")
    )

    await db.flush()

    # Reload items after status update
    items_result = await db.execute(
        select(RedistributionItem).where(RedistributionItem.plan_id == plan_id)
    )
    items = list(items_result.scalars().all())

    # Resolve OPEN alerts linked to predictions that triggered these transfers
    trigger_pred_ids = [
        item.trigger_prediction
        for item in items
        if item.trigger_prediction is not None
    ]
    if trigger_pred_ids:
        await db.execute(
            update(Alert)
            .where(
                Alert.prediction_id.in_(trigger_pred_ids),
                Alert.status == "OPEN",
            )
            .values(
                status="RESOLVED",
                resolved_at=now,
            )
        )

    # Queue Celery notification task (fire-and-forget)
    try:
        celery_app.send_task(
            "tasks.notification_tasks.send_transfer_notifications",
            kwargs={"plan_id": str(plan_id)},
            queue="notifications",
        )
    except Exception as exc:
        # Non-fatal: log and continue — plan is already approved in DB
        log.error("celery_task_enqueue_failed", task="send_transfer_notifications", error=str(exc))

    # Broadcast WebSocket event
    try:
        ws_manager = request.app.state.ws_manager
        await ws_manager.broadcast({
            "type": "plan_approved",
            "plan_id": str(plan_id),
            "district_id": district_id,
            "approved_by": str(current_user.id),
        })
    except Exception as exc:
        log.warning("ws_broadcast_failed", event="plan_approved", error=str(exc))

    return await _build_plan_response(plan, items, db)


@router.post("/plans/{plan_id}/defer", response_model=RedistributionPlanResponse)
async def defer_plan(
    plan_id: uuid.UUID,
    body: DeferRequest,
    current_user: User = Depends(require_role("DISTRICT_OFFICER")),
    db: AsyncSession = Depends(get_db),
):
    """Defer a redistribution plan with a mandatory reason."""
    district_id = current_user.district_id
    if not district_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no associated district",
        )

    plan, items = await _load_plan_or_404(plan_id, district_id, db)

    if plan.status not in ("PENDING",):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Plan is in status '{plan.status}' and cannot be deferred",
        )

    plan.status = "DEFERRED"
    # Store deferred_reason in notes field (no dedicated column in schema)
    existing_notes = plan.notes or ""
    plan.notes = f"deferred_reason={body.reason}" + (f"; {existing_notes}" if existing_notes else "")

    await db.flush()
    return await _build_plan_response(plan, items, db)
