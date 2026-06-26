"""
Webhooks router — Meta WhatsApp Cloud API integration.

Endpoints:
  GET  /webhooks/whatsapp   Meta verification challenge (hub.challenge handshake)
  POST /webhooks/whatsapp   Inbound message handler (interactive buttons + text)

Security:
  POST requests are verified via X-Hub-Signature-256 HMAC-SHA256 header.
  WhatsApp requires HTTP 200 responses even for errors — failure to return 200
  causes Meta to retry the delivery, flooding the endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from db import get_db
from models.alert import Alert
from models.redistribution import RedistributionItem, RedistributionPlan

log = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_whatsapp_signature(body: bytes, signature_header: Optional[str]) -> bool:
    """
    Verify X-Hub-Signature-256 header from Meta.
    Expected format: 'sha256=<hex_digest>'
    Key: settings.whatsapp_token (the app secret, NOT the bearer token).
    """
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected_hex = signature_header[len("sha256="):]
    computed = hmac.new(
        settings.whatsapp_token.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, expected_hex)


def _extract_entry_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Walk the WhatsApp webhook payload and return a flat list of message objects."""
    messages: list[dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                msg["_metadata"] = value.get("metadata", {})
                msg["_contacts"] = value.get("contacts", [])
                messages.append(msg)
    return messages


async def _approve_plan(plan_id: uuid.UUID, db: AsyncSession, request: Request) -> None:
    """Internal helper: approve a plan (mirrors redistribution router logic)."""
    result = await db.execute(
        select(RedistributionPlan).where(RedistributionPlan.id == plan_id)
    )
    plan = result.scalar_one_or_none()
    if not plan or plan.status not in ("PENDING", "DEFERRED"):
        log.warning("whatsapp_approve_plan_skip", plan_id=str(plan_id),
                    reason="not found or wrong status")
        return

    now = datetime.now(timezone.utc)
    plan.status = "APPROVED"
    plan.approved_at = now

    await db.execute(
        update(RedistributionItem)
        .where(RedistributionItem.plan_id == plan_id)
        .values(status="APPROVED")
    )

    # Reload items to find trigger predictions
    items_result = await db.execute(
        select(RedistributionItem).where(RedistributionItem.plan_id == plan_id)
    )
    items = list(items_result.scalars().all())
    trigger_pred_ids = [i.trigger_prediction for i in items if i.trigger_prediction]
    if trigger_pred_ids:
        await db.execute(
            update(Alert)
            .where(Alert.prediction_id.in_(trigger_pred_ids), Alert.status == "OPEN")
            .values(status="RESOLVED", resolved_at=now)
        )

    # Queue Celery notification task
    try:
        from celery_app import celery_app
        celery_app.send_task(
            "tasks.notification_tasks.send_transfer_notifications",
            kwargs={"plan_id": str(plan_id)},
            queue="notifications",
        )
    except Exception as exc:
        log.error("celery_whatsapp_approve_failed", error=str(exc))

    # WebSocket broadcast
    try:
        ws_manager = request.app.state.ws_manager
        await ws_manager.broadcast({
            "type": "plan_approved",
            "plan_id": str(plan_id),
            "source": "whatsapp",
        })
    except Exception as exc:
        log.warning("ws_broadcast_whatsapp_failed", error=str(exc))

    log.info("whatsapp_plan_approved", plan_id=str(plan_id))


async def _defer_plan(plan_id: uuid.UUID, reason: str, db: AsyncSession) -> None:
    """Internal helper: defer a plan with a reason."""
    result = await db.execute(
        select(RedistributionPlan).where(RedistributionPlan.id == plan_id)
    )
    plan = result.scalar_one_or_none()
    if not plan or plan.status != "PENDING":
        log.warning("whatsapp_defer_plan_skip", plan_id=str(plan_id),
                    reason="not found or wrong status")
        return

    plan.status = "DEFERRED"
    existing_notes = plan.notes or ""
    plan.notes = f"deferred_reason={reason}" + (f"; {existing_notes}" if existing_notes else "")
    log.info("whatsapp_plan_deferred", plan_id=str(plan_id))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/whatsapp", response_class=PlainTextResponse)
async def whatsapp_verify(
    hub_mode: Optional[str] = None,
    hub_verify_token: Optional[str] = None,
    hub_challenge: Optional[str] = None,
):
    """
    Meta webhook verification endpoint.
    Meta sends GET with query params:
      hub.mode        = 'subscribe'
      hub.verify_token = the token you configured in the Meta developer console
      hub.challenge   = a random string to echo back
    """
    # FastAPI maps 'hub.mode' query params as 'hub_mode' via alias — use Request manually
    # to avoid aliasing issues with dots in query param names.
    # Parameters are injected via FastAPI Query with aliases below.
    # This handler signature uses underscores; the route reads raw query directly.
    # See implementation note in the function body.
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Use the raw-request handler below",
    )


# Override with a raw Request handler to properly read 'hub.mode' etc.
# Re-register the route using a raw Request parameter.
router.routes.pop()  # remove the placeholder above


@router.get("/whatsapp")
async def whatsapp_verify_raw(request: Request):
    """
    Meta webhook verification (GET).
    Query params use dot notation: hub.mode, hub.verify_token, hub.challenge.
    Returns the challenge as plain text on success, 403 on failure.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    verify_token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and verify_token == settings.whatsapp_verify_token:
        log.info("whatsapp_webhook_verified")
        return PlainTextResponse(content=challenge or "", status_code=200)

    log.warning("whatsapp_webhook_verify_failed", mode=mode, token_match=False)
    return PlainTextResponse(content="Forbidden", status_code=403)


@router.post("/whatsapp", status_code=status.HTTP_200_OK)
async def whatsapp_handler(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Meta WhatsApp Cloud API inbound message handler (POST).

    Always returns HTTP 200 — Meta retries on non-200 responses, which would
    cause duplicate processing.  Errors are logged internally.

    Handles:
      - interactive.button_reply — APPROVE_<plan_id> / DEFER_<plan_id>__<reason>
      - text messages             — stubbed LLM assistant queue
    """
    body_bytes = await request.body()

    # 1. Verify HMAC-SHA256 signature
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_whatsapp_signature(body_bytes, signature):
        log.warning("whatsapp_signature_mismatch", path=str(request.url))
        # Return 200 to stop Meta retries, but do not process
        return {"status": "ignored", "reason": "signature_mismatch"}

    # 2. Parse payload
    try:
        payload: dict[str, Any] = await request.json()
    except Exception as exc:
        log.error("whatsapp_payload_parse_error", error=str(exc))
        return {"status": "error", "reason": "invalid_json"}

    messages = _extract_entry_messages(payload)

    for msg in messages:
        msg_type = msg.get("type")
        msg_id = msg.get("id", "unknown")

        try:
            if msg_type == "interactive":
                interactive = msg.get("interactive", {})
                interactive_type = interactive.get("type")

                if interactive_type == "button_reply":
                    button_reply = interactive.get("button_reply", {})
                    button_id: str = button_reply.get("id", "")

                    if button_id.startswith("APPROVE_"):
                        # Format: APPROVE_<plan_uuid>
                        raw_plan_id = button_id[len("APPROVE_"):]
                        try:
                            plan_id = uuid.UUID(raw_plan_id)
                        except ValueError:
                            log.warning("whatsapp_invalid_plan_uuid",
                                        button_id=button_id)
                            continue
                        log.info("whatsapp_approve_action", plan_id=str(plan_id))
                        await _approve_plan(plan_id, db, request)

                    elif button_id.startswith("DEFER_"):
                        # Format: DEFER_<plan_uuid>__<reason>
                        # Double-underscore separates UUID from reason to avoid UUID hyphen collision
                        raw = button_id[len("DEFER_"):]
                        parts = raw.split("__", 1)
                        raw_plan_id = parts[0]
                        defer_reason = parts[1] if len(parts) > 1 else "Deferred via WhatsApp"
                        try:
                            plan_id = uuid.UUID(raw_plan_id)
                        except ValueError:
                            log.warning("whatsapp_invalid_plan_uuid",
                                        button_id=button_id)
                            continue
                        log.info("whatsapp_defer_action",
                                 plan_id=str(plan_id), reason=defer_reason)
                        await _defer_plan(plan_id, defer_reason, db)

                    else:
                        log.info("whatsapp_unknown_button", button_id=button_id)

                else:
                    log.info("whatsapp_interactive_unhandled_type",
                             interactive_type=interactive_type)

            elif msg_type == "text":
                # Route to LLM assistant — stubbed; queue for async processing
                text_body = msg.get("text", {}).get("body", "")
                sender = msg.get("from", "unknown")
                log.info("whatsapp_text_queued", sender=sender,
                         preview=text_body[:60])
                # TODO: enqueue LLM assistant task
                # celery_app.send_task(
                #     "tasks.notification_tasks.handle_whatsapp_text",
                #     kwargs={"sender": sender, "text": text_body},
                #     queue="notifications",
                # )

            else:
                log.debug("whatsapp_msg_type_ignored", type=msg_type, msg_id=msg_id)

        except Exception as exc:
            # Never let a single message failure break the 200 response
            log.error("whatsapp_message_processing_error",
                      msg_id=msg_id, error=str(exc), exc_info=True)

    return {"status": "ok"}
