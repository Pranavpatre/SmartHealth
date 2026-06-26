"""
Model drift detection and retraining tasks — run by Celery worker.

Schedule (from celery_app.py):
  - check_model_drift : daily 2 AM IST

Table alignment notes (001_core.sql):
  - ai_predictions uses `predicted_value` and `actual_value` (NUMERIC columns),
    not predicted_days_until_stockout / actual_outcome.
  - `worker_feedback` VARCHAR(20): correct | wrong | partial (also useful for
    drift computation once feedback flows).
"""

from __future__ import annotations

import logging
import os

from celery_app import celery_app

log = logging.getLogger(__name__)

# Trigger retraining when recent MAE exceeds baseline by this fraction.
MAE_DRIFT_THRESHOLD = 0.15  # 15% degradation


def _sync_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ---------------------------------------------------------------------------
# Drift check
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.retraining_tasks.check_model_drift",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def check_model_drift(self) -> dict:
    """
    Compare recent prediction MAE against a 7-30 day rolling baseline.

    For each model_version present in ai_predictions:
      - Compute MAE over the last 7 days (predictions with actual_value filled).
      - Compute baseline MAE over the prior 7–30-day window.
      - If recent_mae > baseline_mae * (1 + MAE_DRIFT_THRESHOLD): queue retrain.

    Requires actual_value to be populated post-hoc (by the outcomes ingestion
    job or field worker feedback via worker_feedback = 'correct'/'wrong').

    Returns a summary dict: models_checked, retrains_triggered.
    """
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(_sync_db_url())
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # ── Recent MAE per model (last 7 days, min 10 labelled samples) ───────
        cur.execute(
            """
            SELECT
                model_version,
                AVG(ABS(actual_value - predicted_value))  AS recent_mae,
                COUNT(*)                                   AS sample_count
            FROM ai_predictions
            WHERE predicted_at  > NOW() - INTERVAL '7 days'
              AND actual_value  IS NOT NULL
              AND predicted_value IS NOT NULL
            GROUP BY model_version
            HAVING COUNT(*) >= 10
            """
        )
        recent_rows = cur.fetchall()

        retrains_triggered = 0
        models_checked = len(recent_rows)

        for row in recent_rows:
            model_version: str = row["model_version"]
            recent_mae: float = float(row["recent_mae"])

            # ── Baseline MAE: 7–30 day window ────────────────────────────────
            cur.execute(
                """
                SELECT AVG(ABS(actual_value - predicted_value)) AS baseline_mae
                FROM ai_predictions
                WHERE model_version = %s
                  AND predicted_at BETWEEN NOW() - INTERVAL '30 days'
                                       AND NOW() - INTERVAL '7 days'
                  AND actual_value   IS NOT NULL
                  AND predicted_value IS NOT NULL
                """,
                (model_version,),
            )
            baseline_row = cur.fetchone()

            if not baseline_row or baseline_row["baseline_mae"] is None:
                log.info(
                    "drift_check_no_baseline",
                    model=model_version,
                    recent_mae=round(recent_mae, 3),
                )
                continue

            baseline_mae: float = float(baseline_row["baseline_mae"])
            drift_ratio: float = recent_mae / baseline_mae if baseline_mae else 0.0
            degraded: bool = drift_ratio > (1 + MAE_DRIFT_THRESHOLD)

            log.info(
                "drift_check_result",
                model=model_version,
                recent_mae=round(recent_mae, 3),
                baseline_mae=round(baseline_mae, 3),
                drift_ratio=round(drift_ratio, 3),
                degraded=degraded,
            )

            if degraded:
                log.warning(
                    "model_drift_detected",
                    model=model_version,
                    recent_mae=round(recent_mae, 3),
                    baseline_mae=round(baseline_mae, 3),
                    drift_pct=round((drift_ratio - 1) * 100, 1),
                )
                retrain_model.delay(model_version)
                retrains_triggered += 1

        cur.close()
        conn.close()

        log.info(
            "drift_check_complete",
            models_checked=models_checked,
            retrains_triggered=retrains_triggered,
        )
        return {
            "models_checked": models_checked,
            "retrains_triggered": retrains_triggered,
        }

    except Exception as exc:
        log.error("drift_check_failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Model retraining
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.retraining_tasks.retrain_model",
    bind=True,
    max_retries=1,
    default_retry_delay=600,
)
def retrain_model(self, model_version: str) -> dict:
    """
    Retrain the model identified by *model_version* using fresh data from DB.

    Current behaviour:
      1. Determine model family from version string (e.g. "stockout_v1_1.0"
         → family "stockout_v1").
      2. Call the corresponding training entrypoint (to be implemented in
         ml-models/{family}/train.py) if available; otherwise fall back to
         running a fresh district prediction scan so that new predictions are
         generated with current data while the true ML retraining is wired up.

    Artefact files are written to ml-models/{family}/artefacts/ by the trainer.

    Args:
        model_version: The model_version string as stored in ai_predictions
                       (e.g. "stockout_v1_1.0", "anomaly_zscore_v1").
    """
    log.info("retraining_started", model=model_version)

    # Derive model family from version tag
    # Convention: "{family}_{semver}" or just "{family}"
    parts = model_version.rsplit("_", 1)
    model_family = parts[0] if len(parts) == 2 and parts[1][0].isdigit() else model_version

    trained = False

    # ── Attempt to call the ML trainer module if it exists ───────────────────
    trainer_path = os.path.join(
        os.path.dirname(__file__),
        "../../../../ml-models",
        model_family,
        "train.py",
    )
    if os.path.exists(trainer_path):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                f"ml_{model_family}_train", trainer_path
            )
            trainer_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(trainer_mod)  # type: ignore[union-attr]
            if hasattr(trainer_mod, "train"):
                result = trainer_mod.train()
                log.info("retraining_ml_module_complete", model=model_version, result=result)
                trained = True
        except Exception as train_err:
            log.error(
                "ml_trainer_error",
                model=model_version,
                error=str(train_err),
            )

    # ── Fallback: re-run prediction scan to refresh predictions table ─────────
    if not trained:
        log.info(
            "retraining_fallback_prediction_scan",
            model=model_version,
            reason="trainer_module_not_found_or_failed",
        )
        from tasks.prediction_tasks import run_district_prediction_scan
        run_district_prediction_scan.apply_async(queue="predictions")

    log.info("retraining_queued_or_complete", model=model_version, trained=trained)
    return {
        "model": model_version,
        "model_family": model_family,
        "status": "retrained" if trained else "prediction_scan_queued",
    }
