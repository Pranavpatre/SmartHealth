"""
Scoring and anomaly detection tasks — run by Celery worker.

Schedule (from celery_app.py):
  - run_health_scores  : every 6 hours
  - run_anomaly_scan   : every hour

Table alignment notes (001_core.sql):
  - facility_health_scores is a TimescaleDB hypertable partitioned on `time`.
    Every INSERT must supply `time`.
  - Weights: medicine 25%, doctor 20%, bed 20%, wait_time 20%, diagnostics 15%.
    (overall_score is the composite; the remaining 100% is covered by all five.)
"""

from __future__ import annotations

import logging
import os

from celery_app import celery_app

log = logging.getLogger(__name__)


def _sync_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ---------------------------------------------------------------------------
# Facility health scores
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.scoring_tasks.run_health_scores",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def run_health_scores(self) -> dict:
    """
    Compute a composite health score for every active facility and persist a
    new row in facility_health_scores (TimescaleDB hypertable).

    Sub-scores (0-100):
      medicine_score    — avg stock coverage vs reorder_level, capped at 100
      doctor_score      — doctors_present / doctors_rostered from latest snapshot
      bed_score         — inverse occupancy: (capacity - occupied) / capacity
      wait_time_score   — placeholder 75; replace when wait-time table is added
      diagnostics_score — avg diagnostic kit availability; placeholder 80

    Overall = 0.25·med + 0.20·doc + 0.20·bed + 0.20·wait + 0.15·diag

    Status thresholds: GREEN ≥ 70, YELLOW ≥ 45, RED < 45
    """
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(_sync_db_url())
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cur.execute(
            "SELECT id, bed_capacity FROM facilities ORDER BY id"
        )
        facilities = cur.fetchall()

        scored = 0
        skipped = 0

        for fac in facilities:
            fac_id = str(fac["id"])
            bed_capacity: int = max(fac["bed_capacity"] or 10, 1)

            try:
                # ── Medicine score ───────────────────────────────────────────
                # Coverage = min(current_stock / reorder_level, 1.0) per medicine,
                # averaged across all active medicines.
                cur.execute(
                    """
                    SELECT
                        AVG(
                            LEAST(
                                COALESCE(sb.total_stock, 0)::float
                                    / NULLIF(m.reorder_level, 0),
                                1.0
                            )
                        ) AS coverage
                    FROM medicines m
                    LEFT JOIN (
                        SELECT medicine_id, SUM(quantity) AS total_stock
                        FROM stock_batches
                        WHERE facility_id = %s AND expiry_date > CURRENT_DATE
                        GROUP BY medicine_id
                    ) sb ON sb.medicine_id = m.id
                    WHERE m.is_active = TRUE
                    """,
                    (fac_id,),
                )
                row = cur.fetchone()
                medicine_score = round(float(row["coverage"] or 0.5) * 100, 1)

                # ── Doctor score ─────────────────────────────────────────────
                # Ratio of present to rostered doctors from the most recent snapshot.
                cur.execute(
                    """
                    SELECT doctors_present, doctors_rostered
                    FROM daily_snapshots
                    WHERE facility_id = %s
                    ORDER BY time DESC
                    LIMIT 1
                    """,
                    (fac_id,),
                )
                snap = cur.fetchone()
                if snap and snap["doctors_rostered"] and snap["doctors_rostered"] > 0:
                    doctor_ratio = min(
                        snap["doctors_present"] / snap["doctors_rostered"], 1.0
                    )
                else:
                    doctor_ratio = 0.8  # assume reasonable coverage when no data
                doctor_score = round(doctor_ratio * 100, 1)

                # ── Bed score ────────────────────────────────────────────────
                # How much spare capacity exists. Higher = better.
                cur.execute(
                    """
                    SELECT beds_occupied
                    FROM daily_snapshots
                    WHERE facility_id = %s
                    ORDER BY time DESC
                    LIMIT 1
                    """,
                    (fac_id,),
                )
                bed_snap = cur.fetchone()
                beds_occupied = int(bed_snap["beds_occupied"]) if bed_snap else 0
                bed_ratio = max(0.0, (bed_capacity - beds_occupied) / bed_capacity)
                bed_score = round(min(bed_ratio, 1.0) * 100, 1)

                # ── Wait-time score (placeholder) ────────────────────────────
                # Replace with actual wait-time data when the table is available.
                wait_time_score = 75.0

                # ── Diagnostics score (placeholder) ──────────────────────────
                # Replace with: avg(diagnostic_stock_snapshots.quantity / reorder_level)
                # once diagnostic kit data flows regularly.
                cur.execute(
                    """
                    SELECT AVG(
                        LEAST(dss.quantity::float / NULLIF(dt.reorder_level, 0), 1.0)
                    ) AS diag_coverage
                    FROM diagnostic_stock_snapshots dss
                    JOIN diagnostic_tests dt ON dt.id = dss.test_id
                    WHERE dss.facility_id = %s
                      AND dss.time >= NOW() - INTERVAL '24 hours'
                    """,
                    (fac_id,),
                )
                diag_row = cur.fetchone()
                diag_coverage = float(diag_row["diag_coverage"] or 0.8)
                diagnostics_score = round(min(diag_coverage, 1.0) * 100, 1)

                # ── Composite overall score ──────────────────────────────────
                overall_score = round(
                    0.25 * medicine_score
                    + 0.20 * doctor_score
                    + 0.20 * bed_score
                    + 0.20 * wait_time_score
                    + 0.15 * diagnostics_score,
                    1,
                )
                status = (
                    "GREEN" if overall_score >= 70
                    else ("YELLOW" if overall_score >= 45 else "RED")
                )

                # ── Persist (TimescaleDB hypertable — must supply `time`) ────
                cur.execute(
                    """
                    INSERT INTO facility_health_scores (
                        time, facility_id,
                        medicine_score, doctor_score, bed_score,
                        wait_time_score, diagnostics_score,
                        overall_score, status
                    )
                    VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        fac_id,
                        medicine_score,
                        doctor_score,
                        bed_score,
                        wait_time_score,
                        diagnostics_score,
                        overall_score,
                        status,
                    ),
                )
                scored += 1

            except Exception as fac_err:
                conn.rollback()
                skipped += 1
                log.error(
                    "health_score_error",
                    facility=fac_id,
                    error=str(fac_err),
                )
                continue

        conn.commit()
        cur.close()
        conn.close()

        log.info("health_scores_updated", scored=scored, skipped=skipped)
        return {"scored": scored, "skipped": skipped}

    except Exception as exc:
        log.error("health_scoring_failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Anomaly detection scan
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.scoring_tasks.run_anomaly_scan",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def run_anomaly_scan(self) -> dict:
    """
    Run statistical anomaly detection across all facilities.

    Current implementation: z-score detection on rolling 30-day opd_count.
    Any facility whose latest opd_count deviates by > 2.5 σ from its own
    30-day mean triggers an ANOMALY prediction and an INFO alert.

    Replace with the ml-models/anomaly IsolationForest artefact once trained.
    """
    import psycopg2
    import psycopg2.extras

    log.info("anomaly_scan_started")

    try:
        conn = psycopg2.connect(_sync_db_url())
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cur.execute("SELECT id, code FROM facilities ORDER BY id")
        facilities = cur.fetchall()

        anomalies_detected = 0

        for fac in facilities:
            fac_id = str(fac["id"])
            fac_code = fac["code"]

            try:
                # 30-day rolling stats per facility
                cur.execute(
                    """
                    SELECT
                        AVG(opd_count + ipd_count)         AS mean_visits,
                        STDDEV(opd_count + ipd_count)      AS stddev_visits,
                        MAX(time)                          AS latest_time,
                        (
                            SELECT opd_count + ipd_count
                            FROM daily_snapshots ds2
                            WHERE ds2.facility_id = %s
                            ORDER BY time DESC
                            LIMIT 1
                        )                                  AS latest_visits
                    FROM daily_snapshots
                    WHERE facility_id = %s
                      AND time >= NOW() - INTERVAL '30 days'
                    """,
                    (fac_id, fac_id),
                )
                stats = cur.fetchone()

                if (
                    not stats
                    or stats["mean_visits"] is None
                    or stats["stddev_visits"] is None
                    or float(stats["stddev_visits"]) == 0
                ):
                    continue

                mean = float(stats["mean_visits"])
                stddev = float(stats["stddev_visits"])
                latest = float(stats["latest_visits"] or 0)
                z_score = (latest - mean) / stddev

                if abs(z_score) > 2.5:
                    anomalies_detected += 1
                    direction = "spike" if z_score > 0 else "drop"
                    reasoning = {
                        "z_score": round(z_score, 2),
                        "mean_visits": round(mean, 1),
                        "stddev_visits": round(stddev, 1),
                        "latest_visits": latest,
                        "direction": direction,
                    }

                    import json
                    cur.execute(
                        """
                        INSERT INTO ai_predictions (
                            facility_id, prediction_type, predicted_value,
                            confidence, reasoning, recommendation,
                            model_version, horizon_days
                        )
                        VALUES (%s, 'ANOMALY', %s, %s, %s,
                                %s, 'anomaly_zscore_v1', 1)
                        """,
                        (
                            fac_id,
                            round(abs(z_score), 2),
                            round(min(abs(z_score) / 5.0, 0.99), 3),
                            json.dumps(reasoning),
                            f"Unusual footfall {direction} detected (z={z_score:.2f}). Investigate.",
                        ),
                    )

                    anomaly_params = {
                        "facility": fac_code,
                        "direction": direction,   # 'spike' | 'drop'
                        "latest": round(latest),
                        "mean": round(mean, 1),
                        "zscore": round(z_score, 2),
                    }
                    cur.execute(
                        """
                        INSERT INTO alerts (
                            facility_id, severity, status, title, body,
                            message_key, message_params
                        )
                        SELECT %s, 'INFO'::alert_severity, 'OPEN', %s, %s,
                               'alert.anomaly', %s::jsonb
                        WHERE NOT EXISTS (
                            SELECT 1 FROM alerts
                            WHERE facility_id = %s
                              AND title = %s
                              AND status = 'OPEN'
                              AND created_at >= NOW() - INTERVAL '6 hours'
                        )
                        """,
                        (
                            fac_id,
                            f"Anomaly detected: {fac_code}",
                            (
                                f"Footfall {direction} at {fac_code}: "
                                f"{latest:.0f} visits vs {mean:.1f} avg "
                                f"(z={z_score:.2f}). Review staffing and supplies."
                            ),
                            json.dumps(anomaly_params),
                            fac_id,
                            f"Anomaly detected: {fac_code}",
                        ),
                    )

            except Exception as fac_err:
                conn.rollback()
                log.error(
                    "anomaly_scan_facility_error",
                    facility=fac_code,
                    error=str(fac_err),
                )
                continue

        conn.commit()
        cur.close()
        conn.close()

        log.info("anomaly_scan_complete", anomalies_detected=anomalies_detected)
        return {
            "status": "anomaly_scan_completed",
            "anomalies_detected": anomalies_detected,
        }

    except Exception as exc:
        log.error("anomaly_scan_failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Staff attendance escalation (Project Pulse Module 3)
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.scoring_tasks.run_attendance_escalation",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def run_attendance_escalation(self) -> dict:
    """Escalate facilities with zero on-site (geofenced) attendance for N+
    consecutive days into the admin feed as CRITICAL alerts.

    Only considers facilities that HAVE attendance history (at least one
    check-in ever) — facilities that never onboarded attendance are not
    flagged as "absent". Dedups against alerts opened in the last 24h.
    """
    import psycopg2

    days = int(os.environ.get("ATTENDANCE_ESCALATION_DAYS", "3"))
    try:
        conn = psycopg2.connect(_sync_db_url())
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO alerts (facility_id, severity, status, title, body,
                                message_key, message_params)
            SELECT f.id, 'CRITICAL'::alert_severity, 'OPEN',
                   'Zero doctor attendance: ' || f.name,
                   f.name || ' has reported zero on-site attendance for '
                       || %s || '+ consecutive days. Action recommended.',
                   'alert.attendance',
                   jsonb_build_object('facility', f.name, 'days', %s::int)
            FROM facilities f
            WHERE EXISTS (
                    SELECT 1 FROM staff_attendance a WHERE a.facility_id = f.id
                  )
              AND NOT EXISTS (
                    SELECT 1 FROM staff_attendance a
                    WHERE a.facility_id = f.id
                      AND a.within_geofence = TRUE
                      AND a.attendance_date > CURRENT_DATE - %s
                  )
              AND NOT EXISTS (
                    SELECT 1 FROM alerts al
                    WHERE al.facility_id = f.id
                      AND al.title = 'Zero doctor attendance: ' || f.name
                      AND al.status = 'OPEN'
                      AND al.created_at >= NOW() - INTERVAL '24 hours'
                  )
            """,
            (days, days, days),
        )
        escalated = cur.rowcount
        conn.commit()
        cur.close()
        conn.close()
        log.info("attendance_escalation_complete", escalated=escalated, days=days)
        return {"status": "attendance_escalation_completed", "escalated": escalated}
    except Exception as exc:
        log.error("attendance_escalation_failed", error=str(exc))
        raise self.retry(exc=exc)
