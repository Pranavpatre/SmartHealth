"""
Notification and messaging tasks — run by Celery worker on the "notifications" queue.

Schedule (from celery_app.py):
  - send_morning_digest : daily 7 AM IST

Table alignment notes (001_core.sql):
  - notifications: requires `language` VARCHAR(10) NOT NULL — always populated.
  - redistribution_items: columns are `from_facility` / `to_facility` (UUID),
    not from_facility_id / to_facility_id.
  - alerts.status ENUM: OPEN | ACKNOWLEDGED | RESOLVED | SNOOZED (not PENDING).
  - facility_health_scores is a TimescaleDB hypertable; use DISTINCT ON to get
    the latest score per facility.
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
# Stockout alert — WhatsApp
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.notification_tasks.send_whatsapp_alert",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_whatsapp_alert(
    self,
    facility_id: str,
    medicine_id: int,
    days_until_stockout: int,
    severity: str,
) -> dict:
    """
    Send a WhatsApp stockout alert to every DISTRICT_OFFICER responsible for
    the facility's district.

    If a PENDING redistribution plan exists for this (to_facility, medicine)
    pair, sends an interactive approve/defer message. Otherwise sends a plain
    text escalation notice.

    Inserts a row into notifications for each message dispatched.
    """
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(_sync_db_url())
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # ── Fetch district officers for this facility's district ──────────────
        cur.execute(
            """
            SELECT
                u.id        AS user_id,
                u.phone,
                u.language_pref,
                u.name,
                f.name      AS facility_name,
                m.name      AS medicine_name,
                f.district_id
            FROM facilities f
            JOIN districts d ON d.id = f.district_id
            JOIN users u     ON u.district_id = d.id
                            AND u.role = 'DISTRICT_OFFICER'
                            AND u.is_active = TRUE
            JOIN medicines m ON m.id = %s
            WHERE f.id = %s
            """,
            (medicine_id, facility_id),
        )
        officers = cur.fetchall()

        if not officers:
            log.warning(
                "no_district_officers",
                facility_id=facility_id,
                medicine_id=medicine_id,
            )
            cur.close()
            conn.close()
            return {"sent": 0, "reason": "no_district_officers"}

        # ── Look up pending redistribution plan ──────────────────────────────
        # redistribution_items uses `from_facility` and `to_facility` (UUID cols)
        cur.execute(
            """
            SELECT
                rp.id        AS plan_id,
                ri.from_facility,
                f2.name      AS donor_name,
                ri.quantity
            FROM redistribution_plans rp
            JOIN redistribution_items ri ON ri.plan_id = rp.id
            JOIN facilities f2           ON f2.id = ri.from_facility
            WHERE ri.to_facility = %s
              AND ri.medicine_id = %s
              AND rp.status = 'PENDING'
            ORDER BY rp.generated_at DESC
            LIMIT 1
            """,
            (facility_id, medicine_id),
        )
        plan = cur.fetchone()

        from integrations.whatsapp import WhatsAppClient
        client = WhatsAppClient()

        sent = 0
        for officer in officers:
            phone: str = officer["phone"]
            lang: str = officer["language_pref"]
            facility_name: str = officer["facility_name"]
            medicine_name: str = officer["medicine_name"]
            officer_user_id: str = str(officer["user_id"])

            message_text: str
            if plan:
                client.send_stockout_alert_with_plan(
                    phone=phone,
                    facility_name=facility_name,
                    medicine_name=medicine_name,
                    days_until_stockout=days_until_stockout,
                    severity=severity,
                    donor_facility_name=plan["donor_name"],
                    transfer_quantity=plan["quantity"],
                    plan_id=str(plan["plan_id"]),
                    language=lang,
                )
                message_text = (
                    f"{severity}: {facility_name} — {medicine_name} in "
                    f"{days_until_stockout}d. Transfer plan {plan['plan_id']} available."
                )
            else:
                client.send_stockout_alert_no_plan(
                    phone=phone,
                    facility_name=facility_name,
                    medicine_name=medicine_name,
                    days_until_stockout=days_until_stockout,
                    severity=severity,
                    language=lang,
                )
                message_text = (
                    f"{severity}: {facility_name} — {medicine_name} in "
                    f"{days_until_stockout}d. No surplus nearby. Escalate."
                )

            # ── Log notification delivery ─────────────────────────────────────
            cur.execute(
                """
                INSERT INTO notifications (
                    user_id, channel, language, message, sent_at
                )
                VALUES (%s, 'whatsapp', %s, %s, NOW())
                """,
                (officer_user_id, lang, message_text),
            )
            sent += 1

        conn.commit()
        cur.close()
        conn.close()

        log.info(
            "whatsapp_alert_sent",
            facility=facility_id,
            medicine=medicine_id,
            recipients=sent,
        )
        return {"sent": sent}

    except Exception as exc:
        log.error("whatsapp_alert_failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Morning digest
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.notification_tasks.send_morning_digest",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def send_morning_digest(self) -> dict:
    """
    Send a daily 7 AM morning digest to all active DISTRICT_OFFICERs.

    Each digest contains:
      - Count of OPEN alerts in their district
      - District average health score (latest per facility)
      - Three lowest-scoring facilities
    """
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(_sync_db_url())
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cur.execute(
            """
            SELECT id, phone, name, language_pref, district_id
            FROM users
            WHERE role = 'DISTRICT_OFFICER' AND is_active = TRUE
            """
        )
        officers = cur.fetchall()

        from integrations.whatsapp import WhatsAppClient
        client = WhatsAppClient()
        sent = 0

        for officer in officers:
            user_id = str(officer["id"])
            phone: str = officer["phone"]
            lang: str = officer["language_pref"]
            name: str = officer["name"]
            district_id: int = officer["district_id"]

            # ── OPEN alerts in district ───────────────────────────────────────
            cur.execute(
                """
                SELECT COUNT(*)
                FROM alerts a
                JOIN facilities f ON f.id = a.facility_id
                WHERE f.district_id = %s AND a.status = 'OPEN'
                """,
                (district_id,),
            )
            pending_alerts: int = int(cur.fetchone()[0] or 0)

            # ── Latest health score per facility (TimescaleDB DISTINCT ON) ────
            # facility_health_scores is a hypertable; use DISTINCT ON (facility_id)
            # ORDER BY facility_id, time DESC to retrieve the latest row each.
            cur.execute(
                """
                SELECT
                    f.name        AS facility_name,
                    fhs.overall_score,
                    fhs.status
                FROM (
                    SELECT DISTINCT ON (facility_id)
                        facility_id, overall_score, status
                    FROM facility_health_scores
                    ORDER BY facility_id, time DESC
                ) fhs
                JOIN facilities f ON f.id = fhs.facility_id
                WHERE f.district_id = %s
                ORDER BY fhs.overall_score ASC
                """,
                (district_id,),
            )
            all_scores = cur.fetchall()

            if not all_scores:
                avg_district_score = 0.0
                bottom_facilities: list[tuple[str, float, str]] = []
            else:
                scores = [float(r["overall_score"] or 0) for r in all_scores]
                avg_district_score = round(sum(scores) / len(scores), 1)
                bottom_facilities = [
                    (r["facility_name"], float(r["overall_score"] or 0), r["status"])
                    for r in all_scores[:3]
                ]

            client.send_morning_digest(
                phone=phone,
                officer_name=name,
                pending_alerts=pending_alerts,
                avg_district_score=avg_district_score,
                bottom_facilities=bottom_facilities,
                language=lang,
            )

            digest_text = (
                f"Morning digest: {pending_alerts} open alerts, "
                f"avg score {avg_district_score}."
            )
            cur.execute(
                """
                INSERT INTO notifications (
                    user_id, channel, language, message, sent_at
                )
                VALUES (%s, 'whatsapp', %s, %s, NOW())
                """,
                (user_id, lang, digest_text),
            )
            sent += 1

        conn.commit()
        cur.close()
        conn.close()

        log.info("morning_digest_sent", recipients=sent)
        return {"sent": sent}

    except Exception as exc:
        log.error("morning_digest_failed", error=str(exc))
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Transfer notifications after plan approval
# ---------------------------------------------------------------------------

@celery_app.task(
    name="tasks.notification_tasks.send_transfer_notifications",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def send_transfer_notifications(self, plan_id: str) -> dict:
    """
    Notify donor and receiver facility field workers / PHC admins after a
    redistribution plan is approved.

    Uses redistribution_items.from_facility / to_facility (UUID columns per schema).
    Called from the API router that handles plan approval (PATCH /redistribution/{id}).
    """
    import psycopg2
    import psycopg2.extras

    try:
        conn = psycopg2.connect(_sync_db_url())
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        cur.execute(
            """
            SELECT
                ri.from_facility,
                ri.to_facility,
                ri.medicine_id,
                ri.quantity,
                f1.name  AS from_name,
                f2.name  AS to_name,
                m.name   AS medicine_name
            FROM redistribution_items ri
            JOIN redistribution_plans rp ON rp.id = ri.plan_id
            JOIN facilities f1            ON f1.id = ri.from_facility
            JOIN facilities f2            ON f2.id = ri.to_facility
            JOIN medicines m              ON m.id  = ri.medicine_id
            WHERE rp.id = %s
            """,
            (plan_id,),
        )
        items = cur.fetchall()

        if not items:
            log.warning("transfer_plan_not_found", plan_id=plan_id)
            cur.close()
            conn.close()
            return {"sent": 0, "reason": "plan_not_found"}

        from integrations.whatsapp import WhatsAppClient
        client = WhatsAppClient()
        sent = 0

        for item in items:
            from_fac_id = str(item["from_facility"])
            to_fac_id = str(item["to_facility"])
            qty: int = item["quantity"]
            from_name: str = item["from_name"]
            to_name: str = item["to_name"]
            medicine_name: str = item["medicine_name"]

            # ── Notify donor facility staff ───────────────────────────────────
            cur.execute(
                """
                SELECT id, phone, language_pref
                FROM users
                WHERE facility_id = %s
                  AND role IN ('FIELD_WORKER', 'PHC_ADMIN')
                  AND is_active = TRUE
                """,
                (from_fac_id,),
            )
            for donor_user in cur.fetchall():
                lang = donor_user["language_pref"]
                client.send_dispatch_instruction(
                    phone=donor_user["phone"],
                    medicine_name=medicine_name,
                    quantity=qty,
                    destination_name=to_name,
                    language=lang,
                )
                dispatch_msg = (
                    f"Dispatch {qty} units of {medicine_name} to {to_name}."
                )
                cur.execute(
                    """
                    INSERT INTO notifications (
                        user_id, channel, language, message, sent_at
                    )
                    VALUES (%s, 'whatsapp', %s, %s, NOW())
                    """,
                    (str(donor_user["id"]), lang, dispatch_msg),
                )
                sent += 1

            # ── Notify receiver facility staff ────────────────────────────────
            cur.execute(
                """
                SELECT id, phone, language_pref
                FROM users
                WHERE facility_id = %s
                  AND role IN ('FIELD_WORKER', 'PHC_ADMIN')
                  AND is_active = TRUE
                """,
                (to_fac_id,),
            )
            for recv_user in cur.fetchall():
                lang = recv_user["language_pref"]
                client.send_incoming_transfer_notification(
                    phone=recv_user["phone"],
                    medicine_name=medicine_name,
                    quantity=qty,
                    source_name=from_name,
                    language=lang,
                )
                incoming_msg = (
                    f"{qty} units of {medicine_name} incoming from {from_name}."
                )
                cur.execute(
                    """
                    INSERT INTO notifications (
                        user_id, channel, language, message, sent_at
                    )
                    VALUES (%s, 'whatsapp', %s, %s, NOW())
                    """,
                    (str(recv_user["id"]), lang, incoming_msg),
                )
                sent += 1

        conn.commit()
        cur.close()
        conn.close()

        log.info(
            "transfer_notifications_sent",
            plan_id=plan_id,
            items=len(items),
            messages_sent=sent,
        )
        return {"sent": sent, "items": len(items)}

    except Exception as exc:
        log.error("transfer_notifications_failed", plan_id=plan_id, error=str(exc))
        raise self.retry(exc=exc)
