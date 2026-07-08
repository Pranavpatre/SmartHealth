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
        min_length=1,
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
    # Resolve scope
    # A district-scoped user drills into their own district. STATE_ADMIN /
    # SUPERADMIN have no district_id, so they get a national aggregate
    # instead of a 400 — the SQL district filters simply drop out.
    # ------------------------------------------------------------------
    district_id = current_user.district_id
    is_national = district_id is None

    if is_national and current_user.role not in ("STATE_ADMIN", "SUPERADMIN"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not assigned to a district.",
        )

    if is_national:
        # Empty fragments → national scope (no district filter).
        _dcond = ""        # facilities aliased as `f`
        _dcond2 = ""       # facilities aliased as `f2`
        _dcond_plain = ""  # tables with a direct district_id column
        params: dict = {}
        district_name = "All India"
    else:
        _dcond = "AND f.district_id = :did"
        _dcond2 = "AND f2.district_id = :did"
        _dcond_plain = "AND district_id = :did"
        params = {"did": district_id}

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

        district_name = district_row["name"]

    # ------------------------------------------------------------------
    # 2. Facility count
    # ------------------------------------------------------------------
    facility_count_row = (
        await db.execute(
            sqla_text(
                f"SELECT COUNT(*) AS cnt FROM facilities WHERE 1=1 {_dcond_plain}"
            ),
            params,
        )
    ).mappings().first()
    total_facilities: int = int(facility_count_row["cnt"]) if facility_count_row else 0

    # ------------------------------------------------------------------
    # 3. Active alerts count
    # ------------------------------------------------------------------
    active_alerts_row = (
        await db.execute(
            sqla_text(
                f"""
                SELECT COUNT(*) AS cnt
                FROM alerts a
                JOIN facilities f ON f.id = a.facility_id
                WHERE a.status = 'OPEN'
                  {_dcond}
                """
            ),
            params,
        )
    ).mappings().first()
    active_alerts: int = int(active_alerts_row["cnt"]) if active_alerts_row else 0

    # ------------------------------------------------------------------
    # 4. Pending redistribution plans
    # ------------------------------------------------------------------
    pending_plans_row = (
        await db.execute(
            sqla_text(
                f"""
                SELECT COUNT(*) AS cnt
                FROM redistribution_plans
                WHERE status = 'PENDING'
                  {_dcond_plain}
                """
            ),
            params,
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
                f"""
                SELECT AVG(l.overall_score) AS avg_score
                FROM facilities f
                JOIN mv_facility_latest_score l ON l.facility_id = f.id
                WHERE 1=1 {_dcond}
                """
            ),
            params,
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
                f"""
                SELECT
                    f.name AS facility_name,
                    l.overall_score,
                    l.status,
                    l.medicine_score,
                    l.doctor_score,
                    l.bed_score,
                    l.wait_time_score,
                    l.diagnostics_score
                FROM facilities f
                JOIN mv_facility_latest_score l ON l.facility_id = f.id
                WHERE l.overall_score < 45 {_dcond}
                ORDER BY l.overall_score ASC
                LIMIT 10
                """
            ),
            params,
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
    #
    # ai_predictions holds tens of millions of rows. In district scope the
    # facility-id join makes this selective and fast; in national scope there
    # is no facility filter, so a full scan would take ~a minute. National
    # answers are high-level (covered by avg-score / critical / alert counts),
    # so we skip the per-prediction detail there.
    # ------------------------------------------------------------------
    if is_national:
        prediction_rows = []
    else:
        prediction_rows = (
            await db.execute(
                sqla_text(
                    f"""
                    SELECT
                        f.name              AS facility_name,
                        m.name              AS medicine_name,
                        p.predicted_value   AS days_until_stockout,
                        p.confidence
                    FROM ai_predictions p
                    JOIN facilities f ON f.id = p.facility_id
                    JOIN medicines m  ON m.id = p.medicine_id
                    WHERE p.prediction_type = 'STOCKOUT'
                      AND p.predicted_value <= 3
                      AND p.predicted_at >= NOW() - INTERVAL '24 hours'
                      {_dcond}
                    ORDER BY p.predicted_value ASC, p.confidence DESC
                    LIMIT 15
                    """
                ),
                params,
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
                f"""
                SELECT DISTINCT a.title
                FROM alerts a
                JOIN facilities f ON f.id = a.facility_id
                WHERE a.status = 'OPEN'
                  AND a.severity = 'CRITICAL'
                  {_dcond}
                ORDER BY a.title
                LIMIT 5
                """
            ),
            params,
        )
    ).mappings().all()

    top_risks: list[str] = [row["title"] for row in risk_rows]

    # ------------------------------------------------------------------
    # 8b. Facilities ranked by open CRITICAL alert count (+ total open)
    #     — answers "which facility has the most critical alerts".
    # ------------------------------------------------------------------
    crit_rank_rows = (
        await db.execute(
            sqla_text(
                f"""
                SELECT f.name AS name,
                       count(*) FILTER (WHERE a.severity = 'CRITICAL') AS critical_alerts,
                       count(*) AS open_alerts
                FROM alerts a
                JOIN facilities f ON f.id = a.facility_id
                WHERE a.status = 'OPEN'
                  {_dcond}
                GROUP BY f.name
                HAVING count(*) FILTER (WHERE a.severity = 'CRITICAL') > 0
                ORDER BY critical_alerts DESC, open_alerts DESC
                LIMIT 10
                """
            ),
            params,
        )
    ).mappings().all()

    facilities_by_critical_alerts: list[dict] = [
        {
            "name": row["name"],
            "critical_alerts": int(row["critical_alerts"]),
            "open_alerts": int(row["open_alerts"]),
        }
        for row in crit_rank_rows
    ]

    # ------------------------------------------------------------------
    # 9. Actual medicine shortages — facilities whose current (non-expired)
    #    stock is below the reorder level (demand-derived dynamic level when
    #    available, else the medicine's global reorder). This is live stock, not
    #    a prediction, so the assistant can answer "which facilities are short of
    #    medicine X / facing shortages". National scope skips it (would scan all
    #    facilities × medicines); high-level national answers don't need it.
    # ------------------------------------------------------------------
    if is_national:
        shortage_rows = []
    else:
        shortage_rows = (
            await db.execute(
                sqla_text(
                    f"""
                    SELECT f.name AS facility, m.name AS medicine,
                           COALESCE(SUM(sb.quantity) FILTER (
                               WHERE sb.expiry_date > CURRENT_DATE), 0) AS stock,
                           COALESCE(fmr.dynamic_reorder_level, m.reorder_level) AS reorder
                    FROM facilities f
                    JOIN medicines m ON m.is_active = TRUE
                    LEFT JOIN stock_batches sb
                           ON sb.facility_id = f.id AND sb.medicine_id = m.id
                    LEFT JOIN facility_medicine_requirements fmr
                           ON fmr.facility_id = f.id AND fmr.medicine_id = m.id
                    WHERE 1=1 {_dcond}
                    GROUP BY f.name, m.name, fmr.dynamic_reorder_level, m.reorder_level
                    HAVING COALESCE(SUM(sb.quantity) FILTER (
                               WHERE sb.expiry_date > CURRENT_DATE), 0)
                           < COALESCE(fmr.dynamic_reorder_level, m.reorder_level)
                    ORDER BY (COALESCE(fmr.dynamic_reorder_level, m.reorder_level)
                              - COALESCE(SUM(sb.quantity) FILTER (
                                  WHERE sb.expiry_date > CURRENT_DATE), 0)) DESC
                    LIMIT 25
                    """
                ),
                params,
            )
        ).mappings().all()

    medicine_shortages: list[dict] = [
        {
            "facility": row["facility"],
            "medicine": row["medicine"],
            "stock": int(row["stock"]),
            "reorder": int(row["reorder"]),
        }
        for row in shortage_rows
    ]

    # ------------------------------------------------------------------
    # 10-12. Doctors, tests, beds (district scope only — national skips).
    # ------------------------------------------------------------------
    doctor_gaps: list[dict] = []
    test_gaps: list[dict] = []
    bed_pressure: list[dict] = []
    if not is_national:
        # Understaffed facilities: roster < ~OPD/50 doctors needed.
        doc_rows = (await db.execute(sqla_text(
            f"""
            WITH dc AS (SELECT facility_id, count(*) n FROM doctors GROUP BY facility_id)
            SELECT f.name AS facility, COALESCE(dc.n,0) AS doctors,
                   COALESCE(snap.opd_count,0) AS opd
            FROM facilities f
            LEFT JOIN dc ON dc.facility_id = f.id
            LEFT JOIN mv_facility_latest_snapshot snap ON snap.facility_id = f.id
            WHERE 1=1 {_dcond}
              AND COALESCE(dc.n,0) < GREATEST(1, ceil(COALESCE(snap.opd_count,0)/50.0))
            ORDER BY (GREATEST(1, ceil(COALESCE(snap.opd_count,0)/50.0)) - COALESCE(dc.n,0)) DESC
            LIMIT 15
            """), params)).mappings().all()
        doctor_gaps = [{"facility": r["facility"], "doctors": int(r["doctors"]),
                        "needed": max(1, -(-int(r["opd"]) // 50))} for r in doc_rows]

        # Unavailable diagnostic tests.
        test_rows = (await db.execute(sqla_text(
            f"""
            SELECT f.name AS facility, dt.name AS test
            FROM test_availability ta
            JOIN facilities f ON f.id = ta.facility_id
            JOIN diagnostic_tests dt ON dt.id = ta.test_id
            WHERE ta.available = FALSE {_dcond}
            ORDER BY f.name LIMIT 20
            """), params)).mappings().all()
        test_gaps = [{"facility": r["facility"], "test": r["test"]} for r in test_rows]

        # Facilities running near bed capacity (>= 80% occupied).
        bed_rows = (await db.execute(sqla_text(
            f"""
            SELECT f.name AS facility, SUM(fb.occupied_beds) AS occ, SUM(fb.total_beds) AS cap
            FROM facility_beds fb JOIN facilities f ON f.id = fb.facility_id
            WHERE 1=1 {_dcond}
            GROUP BY f.name
            HAVING SUM(fb.total_beds) > 0
               AND SUM(fb.occupied_beds)::float / SUM(fb.total_beds) >= 0.8
            ORDER BY SUM(fb.occupied_beds)::float / NULLIF(SUM(fb.total_beds),0) DESC
            LIMIT 15
            """), params)).mappings().all()
        bed_pressure = [{"facility": r["facility"], "occupied": int(r["occ"]),
                         "capacity": int(r["cap"])} for r in bed_rows]

    # ------------------------------------------------------------------
    # Assemble context and call the assistant
    # ------------------------------------------------------------------
    # Lazy-import the ML assistant module so a missing ml-models dir or the
    # Gemini SDK does not crash API startup. Mirrors predict.py /
    # redistribution.py, which also resolve ml-models at request time.
    import os
    import sys

    _ml_health_dir = os.path.join(
        os.environ.get("ML_MODELS_DIR", "/app/ml-models"), "health-score"
    )
    if _ml_health_dir not in sys.path:
        sys.path.insert(0, _ml_health_dir)
    from assistant import HealthAssistant, DistrictContext  # noqa: E402

    context = DistrictContext(
        district_name=district_name,
        total_facilities=total_facilities,
        active_alerts=active_alerts,
        pending_redistribution_plans=pending_redistribution_plans,
        avg_health_score=avg_health_score,
        critical_facilities=critical_facilities,
        recent_predictions=recent_predictions,
        top_risks=top_risks,
        facilities_by_critical_alerts=facilities_by_critical_alerts,
        medicine_shortages=medicine_shortages,
        doctor_gaps=doctor_gaps,
        test_gaps=test_gaps,
        bed_pressure=bed_pressure,
    )

    from config import get_settings
    settings = get_settings()

    assistant = HealthAssistant(api_key=settings.gemini_api_key or None)
    # ask() makes a blocking HTTP call to Gemini — run it off the event loop so a
    # single in-flight question doesn't stall other requests (dashboard polling etc.).
    from starlette.concurrency import run_in_threadpool
    answer = await run_in_threadpool(
        assistant.ask, question=body.question, context=context, language=body.language,
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
