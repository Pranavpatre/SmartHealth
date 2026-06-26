"""
Assistant router — Module 07
Multilingual AI Q&A about district health, grounded in live DB data.

Endpoints:
  POST /assistant/query   — ask a natural-language question about your district

Access control: PHC_ADMIN or higher.

The endpoint builds a DistrictContext from live DB queries and forwards it
to the HealthAssistant (Gemini 2.0 Flash) together with the user's question.
All answers are strictly grounded in the supplied context — the model is
instructed not to speculate beyond the provided data.
"""

from __future__ import annotations

import sys
import os

# Co-located with health scoring module (Module 05) as per the project plan.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "../../../../ml-models/health-score"),
)
from assistant import HealthAssistant, DistrictContext  # noqa: E402

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text as sqla_text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.rbac import require_role
from db import get_db
from models.user import User

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])

# Role guard: PHC_ADMIN and above may use the assistant.
_phc_admin_plus = require_role("PHC_ADMIN", "DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class AssistantQueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Natural-language question about district health",
    )
    language: str = Field(
        "en",
        description=(
            "BCP-47 language code for the response. "
            "Supported: en, hi, mr, ta, te, kn, bn, gu, or"
        ),
        pattern=r"^(en|hi|mr|ta|te|kn|bn|gu|or)$",
    )


class AssistantQueryResponse(BaseModel):
    answer: str
    language: str
    sources_used: list[str]


# ---------------------------------------------------------------------------
# POST /assistant/query
# ---------------------------------------------------------------------------

@router.post(
    "/query",
    response_model=AssistantQueryResponse,
    summary="Ask the SmartHealth AI assistant",
    status_code=status.HTTP_200_OK,
)
async def query_assistant(
    body: AssistantQueryRequest,
    current_user: User = Depends(_phc_admin_plus),
    db: AsyncSession = Depends(get_db),
) -> AssistantQueryResponse:
    """
    Ask a natural-language question about your district's health status.

    The answer is grounded exclusively in live data pulled from the database
    for the requesting user's district. No external knowledge is used.

    Requires PHC_ADMIN or higher.
    """
    logger = log.bind(user_id=str(current_user.id), language=body.language)

    # ------------------------------------------------------------------
    # Resolve the district for this user
    # ------------------------------------------------------------------
    district_id = current_user.district_id
    if district_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not assigned to a district.",
        )

    # ------------------------------------------------------------------
    # 1. District name
    # ------------------------------------------------------------------
    district_row = (
        await db.execute(
            sqla_text("SELECT name FROM districts WHERE id = :did"),
            {"did": district_id},
        )
    ).mappings().first()

    if district_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"District {district_id} not found.",
        )

    district_name: str = district_row["name"]

    # ------------------------------------------------------------------
    # 2. Facility count
    # ------------------------------------------------------------------
    facility_count_row = (
        await db.execute(
            sqla_text(
                "SELECT COUNT(*) AS cnt FROM facilities WHERE district_id = :did"
            ),
            {"did": district_id},
        )
    ).mappings().first()
    total_facilities: int = int(facility_count_row["cnt"]) if facility_count_row else 0

    # ------------------------------------------------------------------
    # 3. Active alerts count
    # ------------------------------------------------------------------
    active_alerts_row = (
        await db.execute(
            sqla_text(
                """
                SELECT COUNT(*) AS cnt
                FROM alerts a
                JOIN facilities f ON f.id = a.facility_id
                WHERE f.district_id = :did
                  AND a.status = 'OPEN'
                """
            ),
            {"did": district_id},
        )
    ).mappings().first()
    active_alerts: int = int(active_alerts_row["cnt"]) if active_alerts_row else 0

    # ------------------------------------------------------------------
    # 4. Pending redistribution plans
    # ------------------------------------------------------------------
    pending_plans_row = (
        await db.execute(
            sqla_text(
                """
                SELECT COUNT(*) AS cnt
                FROM redistribution_plans
                WHERE district_id = :did
                  AND status = 'PENDING'
                """
            ),
            {"did": district_id},
        )
    ).mappings().first()
    pending_redistribution_plans: int = (
        int(pending_plans_row["cnt"]) if pending_plans_row else 0
    )

    # ------------------------------------------------------------------
    # 5. Average health score across all facilities
    # ------------------------------------------------------------------
    avg_score_row = (
        await db.execute(
            sqla_text(
                """
                SELECT AVG(latest.overall_score) AS avg_score
                FROM (
                    SELECT DISTINCT ON (fhs.facility_id)
                        fhs.overall_score
                    FROM facility_health_scores fhs
                    JOIN facilities f ON f.id = fhs.facility_id
                    WHERE f.district_id = :did
                    ORDER BY fhs.facility_id, fhs.time DESC
                ) AS latest
                """
            ),
            {"did": district_id},
        )
    ).mappings().first()
    avg_health_score: float = (
        float(avg_score_row["avg_score"])
        if avg_score_row and avg_score_row["avg_score"] is not None
        else 0.0
    )

    # ------------------------------------------------------------------
    # 6. Critical facilities (overall_score < 45), most recent score
    # ------------------------------------------------------------------
    critical_rows = (
        await db.execute(
            sqla_text(
                """
                SELECT
                    f.name          AS facility_name,
                    fhs.overall_score,
                    fhs.status
                FROM (
                    SELECT DISTINCT ON (fhs2.facility_id)
                        fhs2.facility_id,
                        fhs2.overall_score,
                        fhs2.status,
                        fhs2.medicine_score,
                        fhs2.doctor_score,
                        fhs2.bed_score,
                        fhs2.wait_time_score,
                        fhs2.diagnostics_score
                    FROM facility_health_scores fhs2
                    JOIN facilities f2 ON f2.id = fhs2.facility_id
                    WHERE f2.district_id = :did
                    ORDER BY fhs2.facility_id, fhs2.time DESC
                ) AS fhs
                JOIN facilities f ON f.id = fhs.facility_id
                WHERE fhs.overall_score < 45
                ORDER BY fhs.overall_score ASC
                LIMIT 10
                """
            ),
            {"did": district_id},
        )
    ).mappings().all()

    # Derive the top issue from whichever sub-score is lowest
    def _top_issue(row: dict) -> str:
        """Return a human-readable label for the weakest dimension."""
        scores = {
            "medicine supply": row.get("medicine_score"),
            "doctor attendance": row.get("doctor_score"),
            "bed availability": row.get("bed_score"),
            "wait time": row.get("wait_time_score"),
            "diagnostics": row.get("diagnostics_score"),
        }
        # Filter None values then pick the minimum
        valid = {k: float(v) for k, v in scores.items() if v is not None}
        if not valid:
            return "unknown"
        return min(valid, key=valid.get)

    critical_facilities: list[dict] = [
        {
            "name": row["facility_name"],
            "score": round(float(row["overall_score"]), 1) if row["overall_score"] is not None else "N/A",
            "top_issue": _top_issue(row),
        }
        for row in critical_rows
    ]

    # ------------------------------------------------------------------
    # 7. Stockout predictions with horizon <= 3 days (most urgent first)
    # ------------------------------------------------------------------
    prediction_rows = (
        await db.execute(
            sqla_text(
                """
                SELECT
                    f.name              AS facility_name,
                    m.name              AS medicine_name,
                    p.predicted_value   AS days_until_stockout,
                    p.confidence
                FROM ai_predictions p
                JOIN facilities f ON f.id = p.facility_id
                JOIN medicines m  ON m.id = p.medicine_id
                WHERE f.district_id = :did
                  AND p.prediction_type = 'STOCKOUT'
                  AND p.predicted_value <= 3
                  AND p.predicted_at >= NOW() - INTERVAL '24 hours'
                ORDER BY p.predicted_value ASC, p.confidence DESC
                LIMIT 15
                """
            ),
            {"did": district_id},
        )
    ).mappings().all()

    recent_predictions: list[dict] = [
        {
            "facility": row["facility_name"],
            "medicine": row["medicine_name"],
            "days_until_stockout": (
                round(float(row["days_until_stockout"]), 1)
                if row["days_until_stockout"] is not None
                else "N/A"
            ),
            "confidence": (
                float(row["confidence"])
                if row["confidence"] is not None
                else 0.0
            ),
        }
        for row in prediction_rows
    ]

    # ------------------------------------------------------------------
    # 8. Top risks — derive from open CRITICAL alerts
    # ------------------------------------------------------------------
    risk_rows = (
        await db.execute(
            sqla_text(
                """
                SELECT DISTINCT a.title
                FROM alerts a
                JOIN facilities f ON f.id = a.facility_id
                WHERE f.district_id = :did
                  AND a.status = 'OPEN'
                  AND a.severity = 'CRITICAL'
                ORDER BY a.title
                LIMIT 5
                """
            ),
            {"did": district_id},
        )
    ).mappings().all()

    top_risks: list[str] = [row["title"] for row in risk_rows]

    # ------------------------------------------------------------------
    # Assemble context and call the assistant
    # ------------------------------------------------------------------
    context = DistrictContext(
        district_name=district_name,
        total_facilities=total_facilities,
        active_alerts=active_alerts,
        pending_redistribution_plans=pending_redistribution_plans,
        avg_health_score=avg_health_score,
        critical_facilities=critical_facilities,
        recent_predictions=recent_predictions,
        top_risks=top_risks,
    )

    from config import get_settings
    settings = get_settings()

    assistant = HealthAssistant(api_key=settings.gemini_api_key or None)
    answer = assistant.ask(
        question=body.question,
        context=context,
        language=body.language,
    )

    # Record which live data sources contributed to the answer
    sources_used: list[str] = ["facilities", "facility_health_scores", "alerts"]
    if recent_predictions:
        sources_used.append("ai_predictions")
    if pending_redistribution_plans > 0:
        sources_used.append("redistribution_plans")

    logger.info(
        "assistant_query_served",
        district=district_name,
        language=body.language,
        critical_facilities=len(critical_facilities),
        active_alerts=active_alerts,
    )

    return AssistantQueryResponse(
        answer=answer,
        language=body.language,
        sources_used=sources_used,
    )
