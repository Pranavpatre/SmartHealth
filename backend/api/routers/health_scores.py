"""
Health Scores router — Facility health score dashboard.

Endpoints:
  GET /health-scores                       latest score per facility for a district
  GET /health-scores/mine                  latest score for the current user's own facility (PHC_ADMIN)
  GET /health-scores/{facility_id}/history 30-day time-series (TimescaleDB time_bucket)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text as sqla_text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.rbac import require_role
from db import get_db
from models.user import User

router = APIRouter(prefix="/health-scores", tags=["health-scores"])

# Read endpoints that also allow PHC_ADMIN, scoped to their own facility.
_phc_plus = require_role("PHC_ADMIN", "DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN")


# ---------------------------------------------------------------------------
# Score classification helper
# ---------------------------------------------------------------------------

def _traffic_light(score: Optional[float]) -> str:
    """Map overall health score to traffic-light colour.

    Thresholds:
      >= 70  →  GREEN
      >= 45  →  YELLOW
       < 45  →  RED
      None   →  RED  (missing data is treated as worst)
    """
    if score is None:
        return "RED"
    if score >= 70:
        return "GREEN"
    if score >= 45:
        return "YELLOW"
    return "RED"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class FacilityScoreResponse(BaseModel):
    facility_id: uuid.UUID
    facility_name: str
    district_id: int
    overall_score: Optional[float]
    medicine_score: Optional[float]
    doctor_score: Optional[float]
    bed_score: Optional[float]
    wait_time_score: Optional[float]
    diagnostics_score: Optional[float]
    traffic_light: str
    status: Optional[str]
    recorded_at: datetime


class ScoreHistoryPoint(BaseModel):
    date: str                  # YYYY-MM-DD ISO date string
    score: Optional[float]
    medicine_score: Optional[float]
    doctor_score: Optional[float]
    bed_score: Optional[float]
    wait_time_score: Optional[float]
    diagnostics_score: Optional[float]
    traffic_light: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=list[FacilityScoreResponse])
async def list_health_scores(
    district_id: Optional[int] = None,
    current_user: User = Depends(require_role("DISTRICT_OFFICER")),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the latest health score for every facility in a district.
    Results sorted by overall_score ASC (worst facilities first).

    Requires DISTRICT_OFFICER or higher.
    district_id defaults to the requesting user's own district.
    STATE_ADMIN / SUPERADMIN may pass an explicit district_id.
    """
    from auth.rbac import ROLE_HIERARCHY

    effective_district_id = district_id

    # If district_id not provided, fall back to user's own district
    if effective_district_id is None:
        effective_district_id = current_user.district_id
        if effective_district_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="district_id is required or user must be assigned to a district",
            )

    # DISTRICT_OFFICERs may only see their own district
    if (
        ROLE_HIERARCHY.get(current_user.role, 0) < ROLE_HIERARCHY["STATE_ADMIN"]
        and current_user.district_id is not None
        and effective_district_id != current_user.district_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="DISTRICT_OFFICER may only query their own district",
        )

    # Latest score per facility using DISTINCT ON — efficient on the hypertable
    sql = sqla_text(
        """
        SELECT DISTINCT ON (fhs.facility_id)
            fhs.facility_id,
            f.name                  AS facility_name,
            f.district_id,
            fhs.overall_score,
            fhs.medicine_score,
            fhs.doctor_score,
            fhs.bed_score,
            fhs.wait_time_score,
            fhs.diagnostics_score,
            fhs.status,
            fhs.time                AS recorded_at
        FROM facility_health_scores fhs
        JOIN facilities f ON f.id = fhs.facility_id
        WHERE f.district_id = :district_id
        ORDER BY fhs.facility_id, fhs.time DESC
        """
    )
    result = await db.execute(sql, {"district_id": effective_district_id})
    rows = result.mappings().all()

    if not rows:
        return []

    scores: list[FacilityScoreResponse] = []
    for row in rows:
        overall = float(row["overall_score"]) if row["overall_score"] is not None else None
        scores.append(
            FacilityScoreResponse(
                facility_id=row["facility_id"],
                facility_name=row["facility_name"],
                district_id=row["district_id"],
                overall_score=overall,
                medicine_score=float(row["medicine_score"]) if row["medicine_score"] is not None else None,
                doctor_score=float(row["doctor_score"]) if row["doctor_score"] is not None else None,
                bed_score=float(row["bed_score"]) if row["bed_score"] is not None else None,
                wait_time_score=float(row["wait_time_score"]) if row["wait_time_score"] is not None else None,
                diagnostics_score=float(row["diagnostics_score"]) if row["diagnostics_score"] is not None else None,
                traffic_light=_traffic_light(overall),
                status=row["status"],
                recorded_at=row["recorded_at"],
            )
        )

    # Sort worst first (ascending overall_score; NULLs last = most-concerning first)
    scores.sort(key=lambda s: (s.overall_score is None, s.overall_score if s.overall_score is not None else 0))

    return scores


@router.get("/mine", response_model=Optional[FacilityScoreResponse])
async def get_my_facility_score(
    current_user: User = Depends(_phc_plus),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the latest health score for the current user's own facility.

    For PHC_ADMIN, whose account has a facility_id but typically no
    district_id — unlike list_health_scores above, this doesn't require a
    district at all. Returns null if no score has been recorded yet.
    """
    if not current_user.facility_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User has no associated facility",
        )

    sql = sqla_text(
        """
        SELECT DISTINCT ON (fhs.facility_id)
            fhs.facility_id,
            f.name                  AS facility_name,
            f.district_id,
            fhs.overall_score,
            fhs.medicine_score,
            fhs.doctor_score,
            fhs.bed_score,
            fhs.wait_time_score,
            fhs.diagnostics_score,
            fhs.status,
            fhs.time                AS recorded_at
        FROM facility_health_scores fhs
        JOIN facilities f ON f.id = fhs.facility_id
        WHERE fhs.facility_id = :facility_id
        ORDER BY fhs.facility_id, fhs.time DESC
        """
    )
    result = await db.execute(sql, {"facility_id": str(current_user.facility_id)})
    row = result.mappings().first()
    if row is None:
        return None

    overall = float(row["overall_score"]) if row["overall_score"] is not None else None
    return FacilityScoreResponse(
        facility_id=row["facility_id"],
        facility_name=row["facility_name"],
        district_id=row["district_id"],
        overall_score=overall,
        medicine_score=float(row["medicine_score"]) if row["medicine_score"] is not None else None,
        doctor_score=float(row["doctor_score"]) if row["doctor_score"] is not None else None,
        bed_score=float(row["bed_score"]) if row["bed_score"] is not None else None,
        wait_time_score=float(row["wait_time_score"]) if row["wait_time_score"] is not None else None,
        diagnostics_score=float(row["diagnostics_score"]) if row["diagnostics_score"] is not None else None,
        traffic_light=_traffic_light(overall),
        status=row["status"],
        recorded_at=row["recorded_at"],
    )


@router.get("/{facility_id}/history", response_model=list[ScoreHistoryPoint])
async def get_score_history(
    facility_id: uuid.UUID,
    current_user: User = Depends(_phc_plus),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the last 30 days of daily health score history for a facility.
    Uses TimescaleDB time_bucket to aggregate to one row per day.
    Results sorted by date ASC.
    """
    from auth.rbac import ROLE_HIERARCHY

    # PHC_ADMIN may only ever request their own facility — checked directly
    # against facility_id rather than district_id, since a PHC_ADMIN account
    # typically has facility_id set but no district_id.
    if current_user.role == "PHC_ADMIN":
        if current_user.facility_id != facility_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="PHC_ADMIN may only view their own facility's history",
            )
    # Verify facility belongs to user's district (unless STATE_ADMIN+)
    elif ROLE_HIERARCHY.get(current_user.role, 0) < ROLE_HIERARCHY["STATE_ADMIN"]:
        fac_check = sqla_text(
            "SELECT district_id FROM facilities WHERE id = :fid"
        )
        fac_result = await db.execute(fac_check, {"fid": str(facility_id)})
        fac_row = fac_result.mappings().first()
        if not fac_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Facility {facility_id} not found",
            )
        if (
            current_user.district_id is not None
            and fac_row["district_id"] != current_user.district_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Facility does not belong to your district",
            )

    # TimescaleDB time_bucket aggregation — 1 day buckets, last 30 days
    sql = sqla_text(
        """
        SELECT
            time_bucket('1 day', time)          AS day,
            AVG(overall_score)                  AS overall_score,
            AVG(medicine_score)                 AS medicine_score,
            AVG(doctor_score)                   AS doctor_score,
            AVG(bed_score)                      AS bed_score,
            AVG(wait_time_score)                AS wait_time_score,
            AVG(diagnostics_score)              AS diagnostics_score
        FROM facility_health_scores
        WHERE facility_id = :facility_id
          AND time >= NOW() - INTERVAL '30 days'
        GROUP BY day
        ORDER BY day ASC
        """
    )
    result = await db.execute(sql, {"facility_id": str(facility_id)})
    rows = result.mappings().all()

    history: list[ScoreHistoryPoint] = []
    for row in rows:
        overall = float(row["overall_score"]) if row["overall_score"] is not None else None
        history.append(
            ScoreHistoryPoint(
                date=row["day"].strftime("%Y-%m-%d"),
                score=overall,
                medicine_score=float(row["medicine_score"]) if row["medicine_score"] is not None else None,
                doctor_score=float(row["doctor_score"]) if row["doctor_score"] is not None else None,
                bed_score=float(row["bed_score"]) if row["bed_score"] is not None else None,
                wait_time_score=float(row["wait_time_score"]) if row["wait_time_score"] is not None else None,
                diagnostics_score=float(row["diagnostics_score"]) if row["diagnostics_score"] is not None else None,
                traffic_light=_traffic_light(overall),
            )
        )

    return history
