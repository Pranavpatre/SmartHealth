"""
WhatsApp Cloud API client (Meta Graph API v19.0).

Environment variables required:
  WHATSAPP_TOKEN            — permanent or temporary access token
  WHATSAPP_PHONE_NUMBER_ID  — the phone-number object ID from Meta Business Suite
  WHATSAPP_API_VERSION      — default: v19.0

All public send_* methods are best-effort: they catch httpx errors and log them
rather than raising, so a WhatsApp delivery failure never blocks a Celery task.

API reference:
  https://developers.facebook.com/docs/whatsapp/cloud-api/reference/messages
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

log = logging.getLogger(__name__)

_DEFAULT_API_VERSION = "v19.0"


class WhatsAppClient:
    """
    Thin wrapper around the Meta WhatsApp Cloud API /messages endpoint.

    Usage::

        client = WhatsAppClient()
        client.send_stockout_alert_with_plan(
            phone="+919876543210",
            facility_name="PHC Shirur",
            medicine_name="Paracetamol 500mg",
            days_until_stockout=2,
            severity="CRITICAL",
            donor_facility_name="CHC Daund",
            transfer_quantity=500,
            plan_id="abc-123",
            language="hi",
        )
    """

    def __init__(self) -> None:
        self._token: str = os.environ.get("WHATSAPP_TOKEN", "")
        self._phone_number_id: str = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
        self._api_version: str = os.environ.get(
            "WHATSAPP_API_VERSION", _DEFAULT_API_VERSION
        )

        if not self._token:
            log.warning("whatsapp_token_missing")
        if not self._phone_number_id:
            log.warning("whatsapp_phone_number_id_missing")

        self._base_url: str = (
            f"https://graph.facebook.com/{self._api_version}"
            f"/{self._phone_number_id}/messages"
        )
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    # -------------------------------------------------------------------------
    # Public send methods
    # -------------------------------------------------------------------------

    def send_stockout_alert_with_plan(
        self,
        *,
        phone: str,
        facility_name: str,
        medicine_name: str,
        days_until_stockout: int,
        severity: str,
        donor_facility_name: str,
        transfer_quantity: int,
        plan_id: str,
        language: str = "en",
    ) -> None:
        """
        Send an interactive WhatsApp message with two quick-reply buttons:
          - ✅ Approve Transfer  (id: APPROVE_{plan_id})
          - ⏸ Defer for now     (id: DEFER_{plan_id}_later)

        Body text is in English. TODO: pipe through Gemini translation API for
        other BCP-47 language codes (hi, mr, ta, te, kn, bn, gu, or).

        Args:
            phone:                Recipient E.164 phone number, e.g. "+919876543210".
            facility_name:        Name of the facility running out of stock.
            medicine_name:        Name of the medicine at risk.
            days_until_stockout:  Days remaining before stockout.
            severity:             Alert severity label (CRITICAL | WARNING | INFO).
            donor_facility_name:  Facility that has surplus stock.
            transfer_quantity:    Units proposed for transfer.
            plan_id:              UUID of the redistribution plan.
            language:             BCP-47 language preference (default "en").
        """
        body_text = (
            f"\U0001f6a8 {severity}: {facility_name} will run out of "
            f"{medicine_name} in {days_until_stockout} day(s). "
            f"Transfer {transfer_quantity} units from {donor_facility_name} "
            f"available. Approve?"
        )

        # Button titles are capped at 20 chars by the WhatsApp API.
        buttons: list[dict[str, Any]] = [
            {
                "type": "reply",
                "reply": {
                    "id": f"APPROVE_{plan_id}",
                    "title": "✅ Approve Transfer",
                },
            },
            {
                "type": "reply",
                "reply": {
                    "id": f"DEFER_{plan_id}_later",
                    "title": "⏸ Defer for now",
                },
            },
        ]

        self._send_interactive(
            phone=phone,
            body_text=body_text,
            buttons=buttons,
        )

    def send_stockout_alert_no_plan(
        self,
        *,
        phone: str,
        facility_name: str,
        medicine_name: str,
        days_until_stockout: int,
        severity: str,
        language: str = "en",
    ) -> None:
        """
        Send a plain-text stockout alert when no redistribution plan exists.

        Args:
            phone:                Recipient E.164 phone number.
            facility_name:        Name of the at-risk facility.
            medicine_name:        Name of the medicine at risk.
            days_until_stockout:  Days remaining before stockout.
            severity:             Alert severity label.
            language:             BCP-47 language preference (default "en").
        """
        body = (
            f"\U0001f6a8 {severity}: {facility_name} — {medicine_name} "
            f"runs out in {days_until_stockout} day(s). "
            "No surplus facility nearby. Escalate procurement."
        )
        self._send_text(phone=phone, body=body)

    def send_morning_digest(
        self,
        *,
        phone: str,
        officer_name: str,
        pending_alerts: int,
        avg_district_score: float,
        bottom_facilities: list[tuple[str, float, str]],
        language: str = "en",
    ) -> None:
        """
        Send a daily morning digest to a district officer.

        Args:
            phone:               Recipient E.164 phone number.
            officer_name:        Officer's display name (for greeting).
            pending_alerts:      Count of OPEN alerts in the district.
            avg_district_score:  Average health score across all facilities (0–100).
            bottom_facilities:   Up to 3 tuples of (facility_name, score, status).
            language:            BCP-47 language preference (default "en").
        """
        status_emoji = {
            "GREEN": "\U0001f7e2",
            "YELLOW": "\U0001f7e1",
            "RED": "\U0001f534",
        }

        lines: list[str] = [
            f"\U0001f305 Good morning, {officer_name}!",
            f"District Health Summary — today:",
            f"  • Open alerts: {pending_alerts}",
            f"  • Avg district score: {avg_district_score:.1f}/100",
        ]

        if bottom_facilities:
            lines.append("\nFacilities needing attention:")
            for fname, score, fstatus in bottom_facilities:
                emoji = status_emoji.get(fstatus, "⚪")
                lines.append(f"  {emoji} {fname}: {score:.1f}")

        lines.append("\nReply HELP to see available commands.")
        self._send_text(phone=phone, body="\n".join(lines))

    def send_dispatch_instruction(
        self,
        *,
        phone: str,
        medicine_name: str,
        quantity: int,
        destination_name: str,
        language: str = "en",
    ) -> None:
        """
        Notify a donor facility worker to dispatch medicines.

        Args:
            phone:            Recipient E.164 phone number.
            medicine_name:    Medicine to dispatch.
            quantity:         Number of units to send.
            destination_name: Receiving facility name.
            language:         BCP-47 language preference (default "en").
        """
        body = (
            f"\U0001f4e6 Action required: Please dispatch {quantity} unit(s) "
            f"of {medicine_name} to {destination_name}. "
            "Confirm dispatch by replying DISPATCHED."
        )
        self._send_text(phone=phone, body=body)

    def send_incoming_transfer_notification(
        self,
        *,
        phone: str,
        medicine_name: str,
        quantity: int,
        source_name: str,
        language: str = "en",
    ) -> None:
        """
        Notify a receiving facility worker that stock is on its way.

        Args:
            phone:         Recipient E.164 phone number.
            medicine_name: Medicine being transferred.
            quantity:      Number of units dispatched.
            source_name:   Sending facility name.
            language:      BCP-47 language preference (default "en").
        """
        body = (
            f"\U0001f69a {quantity} unit(s) of {medicine_name} dispatched "
            f"from {source_name}. Expected within 24 hours. "
            "Reply RECEIVED once stock arrives."
        )
        self._send_text(phone=phone, body=body)

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _send_text(self, phone: str, body: str) -> None:
        """
        Send a plain-text WhatsApp message.

        Args:
            phone: E.164 phone number of the recipient.
            body:  Message content (max ~4096 chars).
        """
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": _normalise_phone(phone),
            "type": "text",
            "text": {
                "preview_url": False,
                "body": body,
            },
        }
        self._post(payload)

    def _send_interactive(
        self,
        phone: str,
        body_text: str,
        buttons: list[dict[str, Any]],
    ) -> None:
        """
        Send an interactive WhatsApp message with quick-reply buttons.

        The Cloud API supports up to 3 buttons per message.

        Args:
            phone:      E.164 phone number of the recipient.
            body_text:  Body copy shown above the buttons (max ~1024 chars).
            buttons:    List of button objects in Cloud API format:
                        [{"type": "reply", "reply": {"id": "...", "title": "..."}}]
        """
        if len(buttons) > 3:
            log.warning(
                "whatsapp_too_many_buttons",
                count=len(buttons),
                phone=phone,
            )
            buttons = buttons[:3]

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": _normalise_phone(phone),
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body_text},
                "action": {"buttons": buttons},
            },
        }
        self._post(payload)

    def _post(self, payload: dict[str, Any]) -> None:
        """
        Execute an HTTP POST to the WhatsApp Cloud API messages endpoint.

        Errors are logged and swallowed — notifications are best-effort.
        The caller is responsible for retry logic via Celery task retries.
        """
        if not self._token or not self._phone_number_id:
            log.error(
                "whatsapp_config_incomplete",
                has_token=bool(self._token),
                has_phone_number_id=bool(self._phone_number_id),
            )
            return

        try:
            response = httpx.post(
                self._base_url,
                headers=self._headers,
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
            log.info(
                "whatsapp_message_sent",
                to=payload.get("to"),
                message_id=data.get("messages", [{}])[0].get("id"),
            )
        except httpx.HTTPStatusError as exc:
            log.error(
                "whatsapp_http_status_error",
                status_code=exc.response.status_code,
                response_text=exc.response.text[:500],
                to=payload.get("to"),
            )
        except httpx.TimeoutException:
            log.error("whatsapp_timeout", to=payload.get("to"))
        except httpx.HTTPError as exc:
            log.error(
                "whatsapp_http_error",
                error=str(exc),
                to=payload.get("to"),
            )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _normalise_phone(phone: str) -> str:
    """
    Strip spaces and dashes; ensure the number starts with '+' for E.164.

    The WhatsApp Cloud API accepts E.164 without the leading '+' as well,
    but we keep it for consistency with the users table.
    """
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned
