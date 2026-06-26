"""
predict.py — FastAPI router for ML-powered stockout and district prediction.

Endpoints
---------
POST  /predict/stockout           — per-facility, per-medicine stockout forecast
POST  /predict/district           — queue a district-wide prediction scan
GET   /predict/jobs/{job_id}      — poll Celery task status / result

ML model
--------
StockoutPredictor lives at backend/ml-models/stockout/model.py.
Artefacts are persisted to settings.ml_artefacts_path under the pattern:
  {ml_artefacts_path}/stockout/{facility_id}/{medicine_id}.pkl

Schema notes (from 001_core.sql):
  daily_snapshots (TimescaleDB):
      time, facility_id, opd_count, ipd_count, emergency_count
  stock_batches:
      facility_id, medicine_id, quantity, expiry_date
  disease_events:
      district_id, disease_name, start_date, end_date, severity
  ai_predictions:
      facility_id, medicine_id, prediction_type='STOCKOUT', predicted_value,
      confidence, reasoning, recommendation, model_version, predicted_at,
      horizon_days
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from auth.rbac import require_role
from config import get_settings
from db import get_db
from models.facility import Facility
from models.inventory import Medicine, StockBatch
from models.prediction import AIPrediction

# ── ML model import ──────────────────────────────────────────────────────────
# Resolves backend/ml-models/stockout/model.py regardless of working directory.
_ML_MODEL_DIR = os.path.join(os.path.dirname(__file__), "../../../ml-models/stockout")
_ML_MODEL_DIR = os.path.normpath(_ML_MODEL_DIR)
if _ML_MODEL_DIR not in sys.path:
    sys.path.insert(0, _ML_MODEL_DIR)

try:
    from model import StockoutPredictor  # noqa: E402
    _PREDICTOR_AVAILABLE = True
except ImportError as _import_err:  # pragma: no cover
    # Graceful degradation when heavy ML deps (prophet, xgboost) are absent
    # e.g. during unit-test runs or Docker build stages without ML deps.
    _PREDICTOR_AVAILABLE = False
    _PREDICTOR_IMPORT_ERR = str(_import_err)

log = structlog.get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/predict")

# ── Role guards ───────────────────────────────────────────────────────────────
_phc_plus = require_role("PHC_ADMIN", "DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN")
_district_plus = require_role("DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN")


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class StockoutPredictRequest(BaseModel):
    facility_id: uuid.UUID
    medicine_id: int
    horizon_days: int = Field(7, ge=1, le=30, description="Forecast horizon in days")


class StockoutPredictResponse(BaseModel):
    facility_id: uuid.UUID
    medicine_id: int
    medicine_name: str
    days_until_stockout: int
    confidence: float           # 0.0 – 1.0
    reasoning: str
    recommended_action: str
    current_stock: int
    avg_daily_consumption: float
    predicted_daily: list[float]
    prediction_id: uuid.UUID | None = None   # persisted ai_predictions row


class DistrictPredictResponse(BaseModel):
    job_id: str
    status: str = "queued"


class JobStatusResponse(BaseModel):
    job_id: str
    status: str               # PENDING | STARTED | SUCCESS | FAILURE | REVOKED
    result: dict | None = None
    traceback: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _artefact_path(facility_id: str, medicine_id: int) -> str:
    """Return the on-disk path for a StockoutPredictor pickle artefact."""
    return str(
        Path(settings.ml_artefacts_path)
        / "stockout"
        / str(facility_id)
        / f"{medicine_id}.pkl"
    )


async def _load_history_df(
    facility_id: uuid.UUID,
    medicine_id: int,
    db: AsyncSession,
) -> "pd.DataFrame":  # type: ignore[name-defined]  # noqa: F821
    """
    Build a consumption history DataFrame from daily_snapshots.

    Consumption is approximated as the reduction in stock_batches quantity
    relative to prior day. When no direct consumption log exists, OPD count
    is used as a proportional proxy.

    Returns a DataFrame with columns: date (date), consumption (float).
    """
    import pandas as pd  # local import — optional heavy dep

    ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)

    # ── Prefer actual stock-batch changes (most accurate) ─────────────────
    # Since there is no dedicated consumption_log table in 001_core.sql,
    # fall back to OPD-derived proxy consumption.
    rows = (
        await db.execute(
            sa_text(
                """
                SELECT
                    DATE(time)             AS snapshot_date,
                    SUM(opd_count)         AS opd_total
                FROM daily_snapshots
                WHERE facility_id = :fid
                  AND time >= :since
                GROUP BY DATE(time)
                ORDER BY snapshot_date ASC
                """
            ),
            {"fid": str(facility_id), "since": ninety_days_ago},
        )
    ).fetchall()

    if not rows:
        return pd.DataFrame(columns=["date", "consumption"])

    # Count distinct active medicines at this facility to derive per-medicine share
    active_med_count_row = await db.execute(
        sa_text(
            """
            SELECT COUNT(DISTINCT medicine_id)
            FROM stock_batches
            WHERE facility_id = :fid
              AND quantity > 0
              AND expiry_date >= CURRENT_DATE
            """
        ),
        {"fid": str(facility_id)},
    )
    active_med_count: int = int(active_med_count_row.scalar_one() or 1)

    records = []
    for row in rows:
        # Conservative: 50% of OPD visits result in a dispense, shared equally
        consumption = float(row.opd_total or 0) * 0.5 / active_med_count
        records.append({"date": str(row.snapshot_date), "consumption": consumption})

    return pd.DataFrame(records)


async def _build_disease_weights(
    facility_id: uuid.UUID,
    horizon_days: int,
    db: AsyncSession,
) -> dict[str, float]:
    """
    Fetch disease_events for the facility's district and build a date→weight map
    covering the forecast horizon starting from today.

    Severity mapping:
      low      → 1.05
      moderate → 1.15
      high     → 1.25
      outbreak → 1.40
    """
    severity_multiplier = {
        "low": 1.05,
        "moderate": 1.15,
        "high": 1.25,
        "outbreak": 1.40,
    }

    today = date.today()
    horizon_end = today + timedelta(days=horizon_days)

    district_row = await db.execute(
        sa_text(
            """
            SELECT f.district_id
            FROM facilities f
            WHERE f.id = :fid
            """
        ),
        {"fid": str(facility_id)},
    )
    district_id = district_row.scalar_one_or_none()
    if district_id is None:
        return {}

    event_rows = (
        await db.execute(
            sa_text(
                """
                SELECT start_date, end_date, severity
                FROM disease_events
                WHERE district_id = :did
                  AND start_date <= :horizon_end
                  AND (end_date IS NULL OR end_date >= :today)
                """
            ),
            {"did": district_id, "today": today, "horizon_end": horizon_end},
        )
    ).fetchall()

    weights: dict[str, float] = {}
    for event in event_rows:
        multiplier = severity_multiplier.get(
            (event.severity or "low").lower(), 1.05
        )
        start = max(event.start_date, today)
        end_date = event.end_date or horizon_end
        end = min(end_date, horizon_end)
        current = start
        while current <= end:
            key = current.strftime("%Y-%m-%d")
            # Take the maximum multiplier if multiple events overlap
            weights[key] = max(weights.get(key, 1.0), multiplier)
            current += timedelta(days=1)

    return weights


async def _persist_prediction(
    facility_id: uuid.UUID,
    medicine_id: int,
    prediction,
    horizon_days: int,
    db: AsyncSession,
) -> uuid.UUID:
    """
    Insert a StockoutPrediction result into ai_predictions and return the new row ID.
    """
    import json

    pred_id = uuid.uuid4()
    await db.execute(
        sa_text(
            """
            INSERT INTO ai_predictions
                (id, facility_id, medicine_id, prediction_type, horizon_days,
                 predicted_value, confidence, reasoning, recommendation,
                 model_version, predicted_at)
            VALUES
                (:id, :facility_id, :medicine_id, 'STOCKOUT', :horizon_days,
                 :predicted_value, :confidence, :reasoning::jsonb,
                 :recommendation, :model_version, NOW())
            """
        ),
        {
            "id": str(pred_id),
            "facility_id": str(facility_id),
            "medicine_id": medicine_id,
            "horizon_days": horizon_days,
            "predicted_value": float(prediction.days_until_stockout),
            "confidence": float(prediction.confidence),
            "reasoning": json.dumps({"text": prediction.reasoning}),
            "recommendation": prediction.recommended_action,
            "model_version": getattr(prediction, "model_version", "1.0"),
        },
    )
    return pred_id


# ─────────────────────────────────────────────────────────────────────────────
# POST /predict/stockout
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/stockout",
    response_model=StockoutPredictResponse,
    summary="Predict days until stockout",
    status_code=status.HTTP_200_OK,
)
async def predict_stockout(
    body: StockoutPredictRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Any = Depends(_phc_plus),
) -> StockoutPredictResponse:
    """
    Predict days until stockout for a (facility, medicine) pair.

    Workflow:
      1. Validate facility and medicine exist.
      2. Load a saved StockoutPredictor artefact if present; otherwise
         fetch 90-day history from daily_snapshots and train a fresh model.
      3. Fetch disease_events for forecast-window weighting.
      4. Run prediction and persist to ai_predictions.
      5. Return the prediction with full detail.

    Requires PHC_ADMIN or above.
    """
    if not _PREDICTOR_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"ML predictor unavailable: {_PREDICTOR_IMPORT_ERR}",
        )

    logger = log.bind(
        facility_id=str(body.facility_id),
        medicine_id=body.medicine_id,
        user_id=str(current_user.id),
    )

    # ── Validate facility ─────────────────────────────────────────────────
    facility_row = await db.execute(
        select(Facility).where(Facility.id == body.facility_id)
    )
    facility = facility_row.scalar_one_or_none()
    if facility is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Facility {body.facility_id} not found.",
        )

    # ── Validate medicine ─────────────────────────────────────────────────
    med_row = await db.execute(
        select(Medicine).where(Medicine.id == body.medicine_id, Medicine.is_active == True)  # noqa: E712
    )
    medicine = med_row.scalar_one_or_none()
    if medicine is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Medicine {body.medicine_id} not found or inactive.",
        )

    # ── Current stock ─────────────────────────────────────────────────────
    stock_row = await db.execute(
        sa_text(
            """
            SELECT
                COALESCE(SUM(quantity), 0)                                  AS total_quantity,
                COUNT(*) FILTER (WHERE expiry_date < CURRENT_DATE + 30)     AS batches_expiring_soon,
                COUNT(*)                                                    AS total_batches
            FROM stock_batches
            WHERE facility_id = :fid
              AND medicine_id  = :mid
              AND quantity     > 0
              AND expiry_date  >= CURRENT_DATE
            """
        ),
        {"fid": str(body.facility_id), "mid": body.medicine_id},
    )
    stock_data = stock_row.one()
    current_stock: int = int(stock_data.total_quantity or 0)
    batches_expiring_soon: int = int(stock_data.batches_expiring_soon or 0)
    total_batches: int = int(stock_data.total_batches or 1)
    expiry_pressure: float = batches_expiring_soon / max(total_batches, 1)

    # ── Load or train model ───────────────────────────────────────────────
    artefact = _artefact_path(str(body.facility_id), body.medicine_id)
    predictor = StockoutPredictor(str(body.facility_id), body.medicine_id)

    if Path(artefact).exists():
        try:
            predictor.load(artefact)
            logger.info("model_loaded", artefact=artefact)
        except Exception as load_err:
            logger.warning("model_load_failed", error=str(load_err), artefact=artefact)
            # Fall through to re-train
            predictor = StockoutPredictor(str(body.facility_id), body.medicine_id)

    if not predictor._is_trained:
        import pandas as pd  # local import

        history_df = await _load_history_df(body.facility_id, body.medicine_id, db)
        disease_weights = await _build_disease_weights(
            body.facility_id, body.horizon_days, db
        )
        mae = predictor.train(
            history=history_df,
            disease_weights=disease_weights if disease_weights else None,
        )
        logger.info("model_trained", mae=round(mae, 4), history_rows=len(history_df))
        # Persist artefact for subsequent requests
        try:
            predictor.save(artefact)
        except Exception as save_err:
            logger.warning("model_save_failed", error=str(save_err))

    # ── Build disease weights for the forecast window ─────────────────────
    forecast_weights = await _build_disease_weights(
        body.facility_id, body.horizon_days, db
    )

    # ── Run prediction ────────────────────────────────────────────────────
    try:
        prediction = predictor.predict(
            current_stock=current_stock,
            horizon_days=body.horizon_days,
            lead_time_days=medicine.lead_time_days,
            expiry_pressure=expiry_pressure,
            disease_weights=forecast_weights if forecast_weights else None,
            medicine_name=medicine.name,
            reorder_level=medicine.reorder_level,
        )
    except Exception as pred_err:
        logger.error("prediction_failed", error=str(pred_err))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Prediction failed: {pred_err}",
        )

    # ── Persist to ai_predictions ─────────────────────────────────────────
    pred_id: uuid.UUID | None = None
    try:
        pred_id = await _persist_prediction(
            body.facility_id, body.medicine_id, prediction, body.horizon_days, db
        )
        logger.info("prediction_persisted", prediction_id=str(pred_id))
    except Exception as persist_err:
        logger.warning("prediction_persist_failed", error=str(persist_err))

    logger.info(
        "stockout_predicted",
        days_until_stockout=prediction.days_until_stockout,
        confidence=prediction.confidence,
        current_stock=current_stock,
    )

    return StockoutPredictResponse(
        facility_id=body.facility_id,
        medicine_id=body.medicine_id,
        medicine_name=medicine.name,
        days_until_stockout=prediction.days_until_stockout,
        confidence=prediction.confidence,
        reasoning=prediction.reasoning,
        recommended_action=prediction.recommended_action,
        current_stock=prediction.current_stock,
        avg_daily_consumption=prediction.avg_daily_consumption,
        predicted_daily=prediction.predicted_daily_consumption,
        prediction_id=pred_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# POST /predict/district
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/district",
    response_model=DistrictPredictResponse,
    summary="Queue district-wide prediction scan",
    status_code=status.HTTP_202_ACCEPTED,
)
async def predict_district(
    current_user: Any = Depends(_district_plus),
) -> DistrictPredictResponse:
    """
    Enqueue a Celery task that runs a full stockout prediction scan for
    every facility/medicine combination in the current user's district.

    Returns a job_id that can be polled via GET /predict/jobs/{job_id}.
    Requires DISTRICT_OFFICER or above.
    """
    try:
        from celery_app import celery_app

        kwargs: dict[str, Any] = {}
        if current_user.district_id is not None:
            kwargs["district_id"] = current_user.district_id

        task = celery_app.send_task(
            "tasks.prediction_tasks.run_district_prediction_scan",
            kwargs=kwargs,
            queue="predictions",
        )
        job_id: str = task.id
    except Exception as celery_err:
        log.error(
            "celery_dispatch_failed",
            task="run_district_prediction_scan",
            error=str(celery_err),
            user_id=str(current_user.id),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to queue prediction task: {celery_err}",
        )

    log.info(
        "district_prediction_queued",
        job_id=job_id,
        district_id=current_user.district_id,
        user_id=str(current_user.id),
    )
    return DistrictPredictResponse(job_id=job_id, status="queued")


# ─────────────────────────────────────────────────────────────────────────────
# GET /predict/jobs/{job_id}
# ─────────────────────────────────────────────────────────────────────────────

@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll Celery task status",
    status_code=status.HTTP_200_OK,
)
async def get_job_status(
    job_id: str,
    current_user: Any = Depends(_phc_plus),
) -> JobStatusResponse:
    """
    Query the Celery result backend for a task's current status and result.

    Status values mirror Celery task states:
      PENDING  — task not yet picked up (or unknown job_id)
      STARTED  — worker has begun execution
      SUCCESS  — completed; result field contains the output dict
      FAILURE  — task raised an exception; traceback field contains details
      REVOKED  — task was cancelled

    Requires PHC_ADMIN or above.
    """
    try:
        from celery.result import AsyncResult
        from celery_app import celery_app

        async_result = AsyncResult(job_id, app=celery_app)
        task_status: str = async_result.status  # PENDING | STARTED | SUCCESS | FAILURE | REVOKED

        result_payload: dict | None = None
        traceback_str: str | None = None

        if task_status == "SUCCESS":
            raw = async_result.result
            result_payload = raw if isinstance(raw, dict) else {"value": str(raw)}
        elif task_status == "FAILURE":
            traceback_str = str(async_result.traceback)

    except Exception as celery_err:
        log.error(
            "celery_result_fetch_failed",
            job_id=job_id,
            error=str(celery_err),
            user_id=str(current_user.id),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to fetch task status: {celery_err}",
        )

    log.info(
        "job_status_polled",
        job_id=job_id,
        task_status=task_status,
        user_id=str(current_user.id),
    )

    return JobStatusResponse(
        job_id=job_id,
        status=task_status,
        result=result_payload,
        traceback=traceback_str,
    )
