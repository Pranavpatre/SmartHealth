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
    body: str | None = None
    created_at: datetime


class StockSummaryItem(BaseModel):
    """Compact per-medicine stock line for the facility detail view."""

    medicine_id: int
    medicine_name: str
    total_stock: int
    reorder_level: int
    days_of_stock: float


class FacilityDetailResponse(FacilityResponse):
    """Extended detail: stock summary, footfall trend, health score, alerts."""

    total_medicine_types: int = 0
    total_stock_units: int = 0
    expiring_soon_count: int = 0          # batches expiring within 30 days
    health_score_breakdown: HealthScoreBreakdown | None = None
    footfall_7d: list[FootfallSnapshot] = Field(default_factory=list)
    recent_alerts: list[AlertSummary] = Field(default_factory=list)
    stock_summary: list[StockSummaryItem] = Field(default_factory=list)
    # Real district OPD footfall from HMIS (data.gov.in), when available.
    real_district_opd_annual: int | None = None
    real_district_opd_period: str | None = None
    # Real district HMIS metrics (data.gov.in): IPD head count + medicine stock-out.
    real_district_ipd_annual: int | None = None
    real_district_ipd_monthly_avg: float | None = None
    real_district_stockout_rate: float | None = None
    real_district_fully_immunized_annual: int | None = None
    real_district_institutional_deliveries_annual: int | None = None
    real_district_hmis_period: str | None = None


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
    page_size: int = Query(50, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> list[FacilityResponse]:
    """
    List facilities scoped to the current user's district.

    SUPERADMIN and STATE_ADMIN may pass an explicit district_id to cross-scope.
    Includes latest health_score and active alert count per facility.
    """
    # Single set-based query (was an N+1: one latest-score + one alert-count
    # query per facility — 2000 round-trips for page_size=1000, ~50s). Latest
    # score comes from the mv_facility_latest_score materialized view (see
    # migrations); alert counts are pre-aggregated in a CTE.
    where: list[str] = []
    params: dict[str, Any] = {}
    if current_user.role not in ("STATE_ADMIN", "SUPERADMIN"):
        if current_user.district_id is not None:
            where.append("f.district_id = :uds")
            params["uds"] = current_user.district_id
        elif current_user.facility_id is not None:
            where.append("f.id = :ufid")
            params["ufid"] = str(current_user.facility_id)
    if district_id is not None:
        where.append("f.district_id = :did")
        params["did"] = district_id
    if facility_type is not None:
        valid_types = {"PHC", "CHC", "SUB_CENTRE", "DISTRICT_HOSPITAL"}
        if facility_type.upper() not in valid_types:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid facility_type. Must be one of: {', '.join(sorted(valid_types))}",
            )
        where.append("f.facility_type = :ft")
        params["ft"] = facility_type.upper()
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    params["lim"] = page_size
    params["off"] = (page - 1) * page_size

    rows = (
        await db.execute(
            sa_text(
                """
                WITH alert_ct AS (
                    SELECT facility_id, count(*) AS c FROM alerts
                    WHERE status = 'OPEN' GROUP BY facility_id
                )
                SELECT f.id, f.district_id, f.code, f.name, f.facility_type,
                       f.address, f.bed_capacity, f.created_at,
                       ST_Y(f.location) AS lat, ST_X(f.location) AS lng,
                       d.name AS district_name,
                       l.overall_score, l.status, COALESCE(ac.c, 0) AS alerts
                FROM facilities f
                JOIN districts d ON d.id = f.district_id
                LEFT JOIN mv_facility_latest_score l ON l.facility_id = f.id
                LEFT JOIN alert_ct ac ON ac.facility_id = f.id
                """
                + where_sql
                + " ORDER BY f.name OFFSET :off LIMIT :lim"
            ),
            params,
        )
    ).all()

    results: list[FacilityResponse] = []
    for r in rows:
        score = float(r.overall_score) if r.overall_score is not None else None
        results.append(
            FacilityResponse(
                id=r.id,
                district_id=r.district_id,
                code=r.code,
                name=r.name,
                facility_type=r.facility_type,
                address=r.address,
                bed_capacity=r.bed_capacity,
                created_at=r.created_at,
                lat=float(r.lat) if r.lat is not None else None,
                lng=float(r.lng) if r.lng is not None else None,
                district_name=r.district_name,
                latest_health_score=r.overall_score,
                health_score_status=r.status,
                active_alert_count=int(r.alerts),
                health_score=score,
                traffic_light=r.status,
                active_alerts=int(r.alerts),
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
# GET /facilities/stats  (must precede /{facility_id})
# ─────────────────────────────────────────────────────────────────────────────

class FacilityStats(BaseModel):
    total: int
    green: int
    yellow: int
    red: int
    unscored: int
    avg_score: float | None


@router.get("/stats", response_model=FacilityStats, summary="Facility totals + status breakdown")
async def facility_stats(
    district_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> FacilityStats:
    """Aggregate counts across ALL facilities in scope (not a single page) —
    powers the dashboard KPIs at national scale."""
    scope = []
    params: dict[str, Any] = {}
    if current_user.role not in ("STATE_ADMIN", "SUPERADMIN"):
        if current_user.district_id is not None:
            scope.append("f.district_id = :uds")
            params["uds"] = current_user.district_id
        elif current_user.facility_id is not None:
            scope.append("f.id = :ufid")
            params["ufid"] = str(current_user.facility_id)
    if district_id is not None:
        scope.append("f.district_id = :did")
        params["did"] = district_id
    where = ("WHERE " + " AND ".join(scope)) if scope else ""

    row = (
        await db.execute(
            sa_text(
                f"""
                SELECT
                    count(f.id) AS total,
                    count(*) FILTER (WHERE l.status = 'GREEN')  AS green,
                    count(*) FILTER (WHERE l.status = 'YELLOW') AS yellow,
                    count(*) FILTER (WHERE l.status = 'RED')    AS red,
                    count(*) FILTER (WHERE l.status IS NULL)    AS unscored,
                    round(avg(l.overall_score), 1)              AS avg_score
                FROM facilities f
                LEFT JOIN mv_facility_latest_score l ON l.facility_id = f.id
                {where}
                """
            ),
            params,
        )
    ).one()
    return FacilityStats(
        total=row.total or 0,
        green=row.green or 0,
        yellow=row.yellow or 0,
        red=row.red or 0,
        unscored=row.unscored or 0,
        avg_score=float(row.avg_score) if row.avg_score is not None else None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GET /facilities/map  (lightweight markers for ALL facilities; must precede /{id})
# ─────────────────────────────────────────────────────────────────────────────

class MapMarker(BaseModel):
    id: uuid.UUID
    name: str
    lat: float
    lng: float
    traffic_light: str | None = None
    health_score: float | None = None


@router.get("/map", response_model=list[MapMarker], summary="All facility markers (for map clustering)")
async def facilities_map(
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> list[MapMarker]:
    """Return a compact marker for every facility in scope in a single query
    (no per-facility loops) — the map clusters these client-side."""
    scope = ["f.location IS NOT NULL"]
    params: dict[str, Any] = {}
    if current_user.role not in ("STATE_ADMIN", "SUPERADMIN"):
        if current_user.district_id is not None:
            scope.append("f.district_id = :uds")
            params["uds"] = current_user.district_id
        elif current_user.facility_id is not None:
            scope.append("f.id = :ufid")
            params["ufid"] = str(current_user.facility_id)
    where = "WHERE " + " AND ".join(scope)

    rows = (
        await db.execute(
            sa_text(
                f"""
                SELECT f.id, f.name,
                       ST_Y(f.location) AS lat, ST_X(f.location) AS lng,
                       l.status AS traffic_light, l.overall_score AS health_score
                FROM facilities f
                LEFT JOIN mv_facility_latest_score l ON l.facility_id = f.id
                {where}
                """
            ),
            params,
        )
    ).fetchall()
    return [
        MapMarker(
            id=r.id, name=r.name, lat=float(r.lat), lng=float(r.lng),
            traffic_light=r.traffic_light,
            health_score=float(r.health_score) if r.health_score is not None else None,
        )
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /facilities/nearest  (nearest PHCs/CHCs to a point; must precede /{facility_id})
# ─────────────────────────────────────────────────────────────────────────────

class NearestFacility(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    facility_type: str
    lat: float
    lng: float
    distance_km: float
    traffic_light: str | None = None
    health_score: float | None = None
    district_id: int | None = None
    district_name: str | None = None
    state_id: int | None = None


@router.get("/nearest", response_model=list[NearestFacility], summary="Nearest PHCs/CHCs to a location")
async def nearest_facilities(
    lat: float = Query(..., ge=-90, le=90),
    lng: float = Query(..., ge=-180, le=180),
    limit: int = Query(10, ge=1, le=50),
    radius_km: float | None = Query(None, ge=0, description="Optional max radius"),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> list[NearestFacility]:
    """Nearest PHC/CHC facilities to (lat, lng), ordered by distance (PostGIS KNN).
    Scoped to the user's district for non-privileged roles; nationwide for STATE_ADMIN/SUPERADMIN."""
    scope = ["f.location IS NOT NULL", "f.facility_type IN ('PHC', 'CHC')"]
    params: dict[str, Any] = {"lat": lat, "lng": lng, "lim": limit}
    if current_user.role not in ("STATE_ADMIN", "SUPERADMIN") and current_user.district_id is not None:
        scope.append("f.district_id = :uds")
        params["uds"] = current_user.district_id
    if radius_km is not None:
        scope.append(
            "ST_DWithin(f.location::geography, "
            "ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography, :radius_m)"
        )
        params["radius_m"] = radius_km * 1000.0
    where = "WHERE " + " AND ".join(scope)

    rows = (
        await db.execute(
            sa_text(
                f"""
                WITH latest AS (
                    SELECT DISTINCT ON (facility_id) facility_id, status, overall_score
                    FROM facility_health_scores ORDER BY facility_id, time DESC
                )
                SELECT f.id, f.code, f.name, f.facility_type,
                       ST_Y(f.location) AS lat, ST_X(f.location) AS lng,
                       ST_Distance(
                           f.location::geography,
                           ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography
                       ) / 1000.0 AS distance_km,
                       l.status, l.overall_score, d.name AS district_name,
                       d.id AS district_id, d.state_id AS state_id
                FROM facilities f
                JOIN districts d ON d.id = f.district_id
                LEFT JOIN latest l ON l.facility_id = f.id
                {where}
                ORDER BY f.location <-> ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)
                LIMIT :lim
                """
            ),
            params,
        )
    ).fetchall()
    return [
        NearestFacility(
            id=r.id, code=r.code, name=r.name, facility_type=r.facility_type,
            lat=float(r.lat), lng=float(r.lng), distance_km=round(float(r.distance_km), 2),
            traffic_light=r.status,
            health_score=float(r.overall_score) if r.overall_score is not None else None,
            district_name=r.district_name,
            district_id=r.district_id,
            state_id=r.state_id,
        )
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# GET /facilities/at-risk  (true national bottom-N; must precede /{facility_id})
# ─────────────────────────────────────────────────────────────────────────────

class AtRiskFacility(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    facility_type: str
    health_score: float | None
    traffic_light: str | None
    active_alerts: int


@router.get("/at-risk", response_model=list[AtRiskFacility], summary="Lowest-scoring facilities in scope")
async def at_risk_facilities(
    limit: int = Query(5, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> list[AtRiskFacility]:
    scope = ["l.overall_score IS NOT NULL"]
    params: dict[str, Any] = {"lim": limit}
    if current_user.role not in ("STATE_ADMIN", "SUPERADMIN"):
        if current_user.district_id is not None:
            scope.append("f.district_id = :uds")
            params["uds"] = current_user.district_id
        elif current_user.facility_id is not None:
            scope.append("f.id = :ufid")
            params["ufid"] = str(current_user.facility_id)
    where = "WHERE " + " AND ".join(scope)

    rows = (
        await db.execute(
            sa_text(
                f"""
                WITH alert_counts AS (
                    SELECT facility_id, count(*) AS c FROM alerts
                    WHERE status = 'OPEN' GROUP BY facility_id
                )
                SELECT f.id, f.code, f.name, f.facility_type,
                       l.status, l.overall_score, COALESCE(ac.c, 0) AS alerts
                FROM facilities f
                JOIN mv_facility_latest_score l ON l.facility_id = f.id
                LEFT JOIN alert_counts ac ON ac.facility_id = f.id
                {where}
                ORDER BY l.overall_score ASC
                LIMIT :lim
                """
            ),
            params,
        )
    ).fetchall()
    return [
        AtRiskFacility(
            id=r.id, code=r.code, name=r.name, facility_type=r.facility_type,
            health_score=float(r.overall_score) if r.overall_score is not None else None,
            traffic_light=r.status, active_alerts=int(r.alerts),
        )
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Geo filter dropdowns + rich facility browse  (must precede /{facility_id})
# ─────────────────────────────────────────────────────────────────────────────

class GeoOption(BaseModel):
    id: int
    name: str


@router.get("/geo/states", response_model=list[GeoOption], summary="States for filter dropdown")
async def list_states(
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> list[GeoOption]:
    rows = (await db.execute(sa_text("SELECT id, name FROM states ORDER BY name"))).all()
    return [GeoOption(id=r[0], name=r[1]) for r in rows]


@router.get("/geo/districts", response_model=list[GeoOption], summary="Districts (optionally filtered by state)")
async def list_districts(
    state_id: int | None = Query(None, description="Filter districts by state"),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> list[GeoOption]:
    if state_id is not None:
        rows = (await db.execute(
            sa_text("SELECT id, name FROM districts WHERE state_id = :s ORDER BY name"),
            {"s": state_id},
        )).all()
    else:
        rows = (await db.execute(sa_text("SELECT id, name FROM districts ORDER BY name"))).all()
    return [GeoOption(id=r[0], name=r[1]) for r in rows]


class FacilityBrowseRow(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    facility_type: str
    district_name: str | None = None
    state_name: str | None = None
    health_score: float | None = None
    traffic_light: str | None = None      # GREEN | YELLOW | RED
    stockout_score: float | None = None   # medicine_score (0-100); low = stockout risk
    doctors_present: int | None = None
    patients: int | None = None           # latest OPD count
    beds_occupied: int | None = None
    bed_capacity: int = 0
    active_alerts: int = 0


class FacilityBrowseResponse(BaseModel):
    total: int
    items: list[FacilityBrowseRow]


@router.get(
    "/browse",
    response_model=FacilityBrowseResponse,
    summary="Filterable facility list with staffing / stock / beds",
)
async def browse_facilities(
    state_id: int | None = Query(None),
    district_id: int | None = Query(None),
    facility_type: str | None = Query(None, description="PHC | CHC | SUB_CENTRE | DISTRICT_HOSPITAL"),
    status_light: str | None = Query(None, alias="status", description="GREEN | YELLOW | RED"),
    search: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_field_plus),
) -> FacilityBrowseResponse:
    """Server-side filtered facility list. Fixes the client-side 1000-row cap
    (critical facilities were invisible) and adds state/district filters plus
    per-facility staffing (doctors), footfall (patients), stock-out score and
    bed occupancy for the browse table."""
    where: list[str] = []
    params: dict[str, Any] = {}

    # scope non-privileged users to their own district / facility
    if current_user.role not in ("STATE_ADMIN", "SUPERADMIN"):
        if getattr(current_user, "district_id", None) is not None:
            where.append("f.district_id = :uds")
            params["uds"] = current_user.district_id
        elif getattr(current_user, "facility_id", None) is not None:
            where.append("f.id = :ufid")
            params["ufid"] = current_user.facility_id
    if state_id is not None:
        where.append("d.state_id = :sid")
        params["sid"] = state_id
    if district_id is not None:
        where.append("f.district_id = :did")
        params["did"] = district_id
    if facility_type is not None:
        where.append("f.facility_type = :ft")
        params["ft"] = facility_type.upper()
    if status_light is not None:
        where.append("hs.status = :st")
        params["st"] = status_light.upper()
    if search:
        where.append("f.name ILIKE :q")
        params["q"] = f"%{search}%"
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    # Latest score and latest snapshot both come from materialized views (see
    # migrations / scoring task refresh) — this was a pair of DISTINCT ON scans
    # over facility_health_scores (~60s) and daily_snapshots (~12s) at national
    # scale. Alert counts are pre-aggregated in a CTE.
    cte = (
        "WITH alert_ct AS ("
        "  SELECT facility_id, count(*) AS c FROM alerts WHERE status = 'OPEN' GROUP BY facility_id) "
    )
    joins = (
        " FROM facilities f"
        " JOIN districts d ON d.id = f.district_id"
        " JOIN states s ON s.id = d.state_id"
        " LEFT JOIN mv_facility_latest_score hs ON hs.facility_id = f.id"
        " LEFT JOIN mv_facility_latest_snapshot snap ON snap.facility_id = f.id"
        " LEFT JOIN alert_ct al ON al.facility_id = f.id"
    )

    total = (await db.execute(sa_text(cte + "SELECT count(*)" + joins + where_sql), params)).scalar() or 0

    params["lim"] = page_size
    params["off"] = (page - 1) * page_size
    rows = (await db.execute(
        sa_text(
            cte
            + "SELECT f.id, f.code, f.name, f.facility_type, d.name AS district_name, s.name AS state_name, "
              "hs.overall_score, hs.status, hs.medicine_score, snap.doctors_present, snap.opd_count, "
              "snap.beds_occupied, f.bed_capacity, al.c"
            + joins + where_sql
            + " ORDER BY hs.overall_score ASC NULLS LAST, f.name LIMIT :lim OFFSET :off"
        ),
        params,
    )).all()

    items = [
        FacilityBrowseRow(
            id=r[0], code=r[1], name=r[2], facility_type=r[3],
            district_name=r[4], state_name=r[5],
            health_score=float(r[6]) if r[6] is not None else None,
            traffic_light=r[7],
            stockout_score=float(r[8]) if r[8] is not None else None,
            doctors_present=r[9], patients=r[10], beds_occupied=r[11],
            bed_capacity=r[12] or 0, active_alerts=int(r[13] or 0),
        )
        for r in rows
    ]
    return FacilityBrowseResponse(total=int(total), items=items)


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

    # ── District name + coordinates (extracted from PostGIS geometry) ──────
    meta_row = (
        await db.execute(
            select(
                District.name.label("district_name"),
                func.ST_Y(Facility.location).label("lat"),
                func.ST_X(Facility.location).label("lng"),
            )
            .join(District, District.id == Facility.district_id)
            .where(Facility.id == facility_id)
        )
    ).one_or_none()
    district_row = meta_row.district_name if meta_row else None
    fac_lat = float(meta_row.lat) if meta_row and meta_row.lat is not None else None
    fac_lng = float(meta_row.lng) if meta_row and meta_row.lng is not None else None

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
            body=a.body,
            created_at=a.created_at,
        )
        for a in recent_alert_rows
    ]

    # ── Active alert count ────────────────────────────────────────────────
    active_alert_count = await _get_active_alert_count(facility_id, db)

    # ── Real district OPD footfall (HMIS), matched by district name ────────
    real_opd_annual: int | None = None
    real_opd_period: str | None = None
    if district_row:
        ff_row = (
            await db.execute(
                sa_text(
                    """
                    SELECT opd_annual, period FROM district_footfall
                    WHERE lower(district) = lower(:dname)
                    ORDER BY period DESC LIMIT 1
                    """
                ),
                {"dname": district_row},
            )
        ).one_or_none()
        if ff_row:
            real_opd_annual = int(ff_row.opd_annual) if ff_row.opd_annual is not None else None
            real_opd_period = ff_row.period

    # ── Real district HMIS metrics (IPD, stock-out), matched by district name ──
    real_ipd_annual: int | None = None
    real_ipd_monthly: float | None = None
    real_stockout_rate: float | None = None
    real_fully_immunized: int | None = None
    real_institutional_deliveries: int | None = None
    real_hmis_period: str | None = None
    if district_row:
        metric_rows = (
            await db.execute(
                sa_text(
                    """
                    SELECT DISTINCT ON (metric) metric, annual_value, monthly_avg, period
                    FROM district_hmis_metrics
                    WHERE lower(district) = lower(:dname)
                    ORDER BY metric, period DESC
                    """
                ),
                {"dname": district_row},
            )
        ).fetchall()
        for r in metric_rows:
            if r.metric == "ipd_headcount":
                real_ipd_annual = int(r.annual_value) if r.annual_value is not None else None
                real_ipd_monthly = float(r.monthly_avg) if r.monthly_avg is not None else None
                real_hmis_period = r.period
            elif r.metric == "stockout_rate":
                # rate-type metric — monthly_avg is the meaningful figure
                real_stockout_rate = float(r.monthly_avg) if r.monthly_avg is not None else None
                real_hmis_period = real_hmis_period or r.period
            elif r.metric == "fully_immunized":
                real_fully_immunized = int(r.annual_value) if r.annual_value is not None else None
                real_hmis_period = real_hmis_period or r.period
            elif r.metric == "institutional_deliveries":
                real_institutional_deliveries = int(r.annual_value) if r.annual_value is not None else None
                real_hmis_period = real_hmis_period or r.period

    # ── Stock summary (per active medicine) ───────────────────────────────
    stock_rows = (
        await db.execute(
            sa_text(
                """
                SELECT m.id AS medicine_id, m.name AS medicine_name,
                       m.reorder_level,
                       COALESCE(SUM(sb.quantity), 0) AS total_stock
                FROM medicines m
                LEFT JOIN stock_batches sb
                       ON sb.medicine_id = m.id
                      AND sb.facility_id = :fid
                      AND sb.quantity > 0
                      AND sb.expiry_date >= CURRENT_DATE
                WHERE m.is_active = TRUE
                GROUP BY m.id, m.name, m.reorder_level
                ORDER BY m.name
                """
            ),
            {"fid": str(facility_id)},
        )
    ).fetchall()

    # Daily consumption proxy from 30-day avg OPD (same heuristic as /stock).
    avg_opd_val = (
        await db.execute(
            sa_text(
                """
                SELECT AVG(opd_count) FROM daily_snapshots
                WHERE facility_id = :fid AND time >= NOW() - INTERVAL '30 days'
                """
            ),
            {"fid": str(facility_id)},
        )
    ).scalar_one_or_none()
    avg_opd = float(avg_opd_val) if avg_opd_val else 0.0
    num_meds = len(stock_rows) or 1
    daily_consumption = (avg_opd * 0.5) / num_meds if avg_opd > 0 else 1.0
    daily_consumption = max(daily_consumption, 1.0)

    stock_summary = [
        StockSummaryItem(
            medicine_id=r.medicine_id,
            medicine_name=r.medicine_name,
            total_stock=int(r.total_stock or 0),
            reorder_level=r.reorder_level,
            days_of_stock=round(int(r.total_stock or 0) / daily_consumption, 1),
        )
        for r in stock_rows
    ]

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
        lat=fac_lat,
        lng=fac_lng,
        district_name=district_row,
        latest_health_score=hs.overall_score if hs else None,
        health_score_status=hs.status if hs else None,
        active_alert_count=active_alert_count,
        # Aliases consumed by the dashboard frontend.
        health_score=float(hs.overall_score) if hs and hs.overall_score is not None else None,
        traffic_light=hs.status if hs else None,
        active_alerts=active_alert_count,
        total_medicine_types=total_medicine_types,
        total_stock_units=total_stock_units,
        expiring_soon_count=expiring_soon_count,
        health_score_breakdown=hs_breakdown,
        footfall_7d=footfall_7d,
        recent_alerts=recent_alerts,
        stock_summary=stock_summary,
        real_district_opd_annual=real_opd_annual,
        real_district_opd_period=real_opd_period,
        real_district_ipd_annual=real_ipd_annual,
        real_district_ipd_monthly_avg=real_ipd_monthly,
        real_district_stockout_rate=real_stockout_rate,
        real_district_fully_immunized_annual=real_fully_immunized,
        real_district_institutional_deliveries_annual=real_institutional_deliveries,
        real_district_hmis_period=real_hmis_period,
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
                     CAST(:old_value AS jsonb), CAST(:new_value AS jsonb), CAST(:ip_address AS inet))
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
