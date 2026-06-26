"""
Prediction tasks — run by Celery worker.

Scheduled: every 15 minutes via beat_schedule in celery_app.py.

Table alignment notes (001_core.sql):
  - ai_predictions: predicted_value (NUMERIC), actual_value (NUMERIC),
    reasoning (JSONB), confidence NUMERIC(4,3), no predicted_days_until_stockout column
  - alerts: severity uses alert_severity ENUM (INFO | WARNING | CRITICAL),
    status uses alert_status ENUM (OPEN | ACKNOWLEDGED | RESOLVED | SNOOZED)
  - daily_snapshots: opd_count + ipd_count + emergency_count (no medicines_dispensed_count)
  - stock_batches: facility_id UUID, medicine_id INT
"""

from __future__ import annotations

import json
import logging
import os
import sys

from celery_app import celery_app

# Prepend ML model source trees so local modules are importable once present.
_BASE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_BASE, "../../../../ml-models/stockout"))
sys.path.insert(0, os.path.join(_BASE, "../../../../ml-models/diagnostics"))

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sync_db_url() -> str:
    """Return a psycopg2-compatible connection string from DATABASE_URL."""
    url = os.environ.get("DATABASE_URL", "")
    # FastAPI uses postgresql+asyncpg://; psycopg2 needs plain postgresql://
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ---------------------------------------------------------------------------
# District-wide prediction scan
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.prediction_tasks.run_district_prediction_scan",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def run_district_prediction_scan(self) -> dict:
    """
    Runs stockout predictions for every active (facility, medicine) pair.

    For each pair:
      1. Fetch 90 days of daily_snapshots (opd + ipd footfall as consumption proxy)
      2. Compute average daily consumption; derive days_until_stockout
      3. Write result to ai_predictions (predicted_value = days_until_stockout)
      4. If days_until_stockout < 3 and stock < reorder_level: open/re-open alert
      5. Queue send_alert_notification for CRITICAL / WARNING severity

    Uses psycopg2 (synchronous) — Celery tasks run in a regular thread, not an
    async event loop.
    """
    import psycopg2
    import psycopg2.extras

    log.info("district_prediction_scan_started")

    try:
        conn = psycopg2.connect(_sync_db_url())
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # ── Active facilities ────────────────────────────────────────────────
        cur.execute(
            "SELECT id, code, name, district_id FROM facilities ORDER BY code"
        )
        facilities = cur.fetchall()

        # ── Essential medicines ──────────────────────────────────────────────
        cur.execute(
            """
            SELECT id, name, reorder_level, lead_time_days
            FROM medicines
            WHERE is_active = TRUE
            ORDER BY id
            """
        )
        medicines = cur.fetchall()

        predictions_written = 0
        alerts_opened = 0

        for fac in facilities:
            fac_id = str(fac["id"])
            fac_code = fac["code"]
            fac_name = fac["name"]

            for med in medicines:
                med_id: int = med["id"]
                med_name: str = med["name"]
                reorder_level: int = med["reorder_level"]
                lead_time: int = med["lead_time_days"]

                try:
                    # ── Current stock (non-expired batches) ──────────────────
                    cur.execute(
                        """
                        SELECT COALESCE(SUM(quantity), 0)
                        FROM stock_batches
                        WHERE facility_id = %s
                          AND medicine_id = %s
                          AND expiry_date > CURRENT_DATE
                        """,
                        (fac_id, med_id),
                    )
                    current_stock: int = int(cur.fetchone()[0])

                    # ── 90-day daily footfall as consumption proxy ────────────
                    # daily_snapshots.opd_count + ipd_count treated as proxy for
                    # medicine consumption when per-medicine dispensing data is
                    # unavailable. Replace with dispensing actuals when available.
                    cur.execute(
                        """
                        SELECT DATE(time) AS day,
                               SUM(opd_count + ipd_count + emergency_count) AS total_visits
                        FROM daily_snapshots
                        WHERE facility_id = %s
                          AND time >= NOW() - INTERVAL '90 days'
                        GROUP BY DATE(time)
                        ORDER BY day DESC
                        """,
                        (fac_id,),
                    )
                    history = cur.fetchall()

                    if not history:
                        log.debug(
                            "no_history_skip",
                            facility=fac_code,
                            medicine=med_name,
                        )
                        continue

                    avg_daily_consumption = max(
                        float(sum(row[1] for row in history)) / len(history),
                        0.1,  # guard against zero division
                    )
                    days_until_stockout: int = int(
                        current_stock / avg_daily_consumption
                    )
                    # Confidence: higher when we have more history
                    confidence: float = min(0.5 + len(history) / 180.0, 0.95)

                    reasoning: dict = {
                        "avg_daily_consumption": round(avg_daily_consumption, 2),
                        "current_stock": current_stock,
                        "history_days": len(history),
                        "lead_time_days": lead_time,
                    }

                    # ── Write prediction row ─────────────────────────────────
                    cur.execute(
                        """
                        INSERT INTO ai_predictions (
                            facility_id, medicine_id,
                            prediction_type, predicted_value,
                            confidence, reasoning, recommendation,
                            model_version, horizon_days
                        )
                        VALUES (%s, %s, 'STOCKOUT', %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            fac_id,
                            med_id,
                            days_until_stockout,
                            round(confidence, 3),
                            json.dumps(reasoning),
                            "URGENT_TRANSFER" if days_until_stockout < lead_time else "MONITOR",
                            "stockout_v1_1.0",
                            3,
                        ),
                    )
                    predictions_written += 1

                    # ── Create / update alert if stock is critical ────────────
                    if days_until_stockout < 3 and current_stock < reorder_level:
                        severity = (
                            "CRITICAL" if days_until_stockout <= 1 else "WARNING"
                        )
                        alert_title = f"Stockout risk: {med_name}"
                        alert_body = (
                            f"{fac_name}: {med_name} will run out in "
                            f"{days_until_stockout} day(s). "
                            f"Current stock {current_stock} units "
                            f"(reorder level {reorder_level}). "
                            f"Confidence: {confidence:.0%}."
                        )

                        # Insert only if no OPEN alert for same facility+medicine
                        cur.execute(
                            """
                            INSERT INTO alerts (
                                facility_id, severity, status, title, body
                            )
                            SELECT %s, %s::alert_severity, 'OPEN', %s, %s
                            WHERE NOT EXISTS (
                                SELECT 1 FROM alerts
                                WHERE facility_id = %s
                                  AND title = %s
                                  AND status = 'OPEN'
                            )
                            """,
                            (
                                fac_id, severity, alert_title, alert_body,
                                fac_id, alert_title,
                            ),
                        )
                        if cur.rowcount:
                            alerts_opened += 1
                            conn.commit()  # commit before queuing downstream task
                            send_alert_notification.delay(
                                fac_id, med_id, days_until_stockout, severity
                            )

                except Exception as pair_err:
                    # Isolate per-pair failures; roll back only the current tx
                    conn.rollback()
                    log.error(
                        "prediction_pair_error",
                        facility=fac_code,
                        medicine=med_name,
                        error=str(pair_err),
                    )
                    continue

        conn.commit()
        cur.close()
        conn.close()

        log.info(
            "district_scan_complete",
            predictions_written=predictions_written,
            alerts_opened=alerts_opened,
        )
        return {
            "predictions_written": predictions_written,
            "alerts_opened": alerts_opened,
        }

    except Exception as exc:
        log.error("district_scan_failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Single-facility on-demand prediction
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.prediction_tasks.run_single_facility_prediction",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def run_single_facility_prediction(self, facility_id: str, medicine_id: int) -> dict:
    """
    Run a stockout prediction for a single (facility, medicine) pair on demand.

    Shares the same logic as run_district_prediction_scan but scoped to one pair.
    Called from API routers when a field worker triggers a manual refresh.
    """
    import psycopg2
    import psycopg2.extras

    log.info(
        "single_facility_prediction_started",
        facility=facility_id,
        medicine=medicine_id,
    )

    try:
        conn = psycopg2.connect(_sync_db_url())
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cur.execute(
            "SELECT id, code, name, district_id FROM facilities WHERE id = %s",
            (facility_id,),
        )
        fac = cur.fetchone()
        if not fac:
            log.warning("facility_not_found", facility_id=facility_id)
            return {"error": "facility_not_found"}

        cur.execute(
            "SELECT id, name, reorder_level, lead_time_days FROM medicines WHERE id = %s",
            (medicine_id,),
        )
        med = cur.fetchone()
        if not med:
            log.warning("medicine_not_found", medicine_id=medicine_id)
            return {"error": "medicine_not_found"}

        cur.execute(
            """
            SELECT COALESCE(SUM(quantity), 0)
            FROM stock_batches
            WHERE facility_id = %s AND medicine_id = %s AND expiry_date > CURRENT_DATE
            """,
            (facility_id, medicine_id),
        )
        current_stock = int(cur.fetchone()[0])

        cur.execute(
            """
            SELECT DATE(time) AS day,
                   SUM(opd_count + ipd_count + emergency_count) AS total_visits
            FROM daily_snapshots
            WHERE facility_id = %s AND time >= NOW() - INTERVAL '90 days'
            GROUP BY DATE(time)
            ORDER BY day DESC
            """,
            (facility_id,),
        )
        history = cur.fetchall()

        if not history:
            cur.close()
            conn.close()
            return {"warning": "insufficient_history", "current_stock": current_stock}

        avg_consumption = max(
            float(sum(r[1] for r in history)) / len(history), 0.1
        )
        days_until_stockout = int(current_stock / avg_consumption)
        confidence = min(0.5 + len(history) / 180.0, 0.95)

        reasoning = {
            "avg_daily_consumption": round(avg_consumption, 2),
            "current_stock": current_stock,
            "history_days": len(history),
            "lead_time_days": med["lead_time_days"],
        }

        cur.execute(
            """
            INSERT INTO ai_predictions (
                facility_id, medicine_id, prediction_type, predicted_value,
                confidence, reasoning, recommendation, model_version, horizon_days
            )
            VALUES (%s, %s, 'STOCKOUT', %s, %s, %s, %s, %s, 3)
            RETURNING id
            """,
            (
                facility_id,
                medicine_id,
                days_until_stockout,
                round(confidence, 3),
                json.dumps(reasoning),
                "URGENT_TRANSFER" if days_until_stockout < med["lead_time_days"] else "MONITOR",
                "stockout_v1_1.0",
            ),
        )
        prediction_id = str(cur.fetchone()[0])
        conn.commit()

        if days_until_stockout < 3 and current_stock < med["reorder_level"]:
            severity = "CRITICAL" if days_until_stockout <= 1 else "WARNING"
            send_alert_notification.delay(
                facility_id, medicine_id, days_until_stockout, severity
            )

        cur.close()
        conn.close()

        log.info(
            "single_facility_prediction_complete",
            facility=facility_id,
            medicine=medicine_id,
            days_until_stockout=days_until_stockout,
            prediction_id=prediction_id,
        )
        return {
            "prediction_id": prediction_id,
            "days_until_stockout": days_until_stockout,
            "current_stock": current_stock,
            "confidence": round(confidence, 3),
        }

    except Exception as exc:
        log.error(
            "single_prediction_failed",
            facility=facility_id,
            medicine=medicine_id,
            error=str(exc),
        )
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Notification relay
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.prediction_tasks.send_alert_notification",
    max_retries=2,
    default_retry_delay=15,
)
def send_alert_notification(
    facility_id: str,
    medicine_id: int,
    days_until_stockout: int,
    severity: str,
) -> None:
    """
    Relay a stockout alert to the notifications queue.

    Thin shim: prediction_tasks queues this task (on the "predictions" queue),
    which then calls the notification task on the "notifications" queue so the
    two queues remain decoupled and retries don't cross-contaminate.
    """
    from tasks.notification_tasks import send_whatsapp_alert

    log.info(
        "queuing_whatsapp_alert",
        facility=facility_id,
        medicine=medicine_id,
        days=days_until_stockout,
        severity=severity,
    )
    send_whatsapp_alert.delay(facility_id, medicine_id, days_until_stockout, severity)
