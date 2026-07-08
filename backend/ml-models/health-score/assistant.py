from __future__ import annotations
"""
Multilingual AI Assistant — Module 07
Uses Gemini 2.0 Flash for natural language Q&A about district health.
Grounded: all answers based only on live DB context injected into the prompt.
Supports 8 Indian languages: hi, mr, ta, te, kn, bn, gu, or (+ en).
"""

import os
import json
import httpx
import structlog
from dataclasses import dataclass, field
from typing import Optional

log = structlog.get_logger()

SUPPORTED_LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi",
    "ta": "Tamil",
    "te": "Telugu",
    "kn": "Kannada",
    "bn": "Bengali",
    "gu": "Gujarati",
    "or": "Odia",
}

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "gemini-2.5-flash"

# Fallback messages in all supported languages for when the API is unavailable.
_FALLBACK_MESSAGES: dict[str, str] = {
    "en": "Data not available. Please check the dashboard.",
    "hi": "डेटा उपलब्ध नहीं है। कृपया डैशबोर्ड जाँचें।",
    "mr": "डेटा उपलब्ध नाही. कृपया डॅशबोर्ड तपासा.",
    "ta": "தரவு கிடைக்கவில்லோ. தயவுசெய்து டாஷ்போர்டைச் சரிபார்க்கவும்.",
    "te": "డేటా అందుబాటులో లేదు. దయచేసి డాష్‌బోర్డ్ తనిఖీ చేయండి.",
    "kn": "ಡೇಟಾ ಲಭ್ಯವಿಲ್ಲ. ದಯವಿಟ್ಟು ಡ್ಯಾಶ್‌ಬೋರ್ಡ್ ಪರಿಶೀಲಿಸಿ.",
    "bn": "ডেটা পাওয়া যাচ্ছে না। অনুগ্রহ করে ড্যাশবোর্ড দেখুন।",
    "gu": "ડેટા ઉપલબ્ધ નથી. કૃપા કરીને ડેશબોર્ડ તપાસો.",
    "or": "ତଥ୍ୟ ଉପଲବ୍ଧ ନାହିଁ। ଦୟାକରି ଡ୍ୟାସ୍‌ବୋର୍ଡ ଯାଞ୍ଚ କରନ୍ତୁ।",
}


@dataclass
class DistrictContext:
    """All live district data passed to the LLM as grounding context."""

    district_name: str
    total_facilities: int
    active_alerts: int
    pending_redistribution_plans: int
    avg_health_score: float
    # [{name, score, top_issue}]
    critical_facilities: list[dict] = field(default_factory=list)
    # [{facility, medicine, days_until_stockout, confidence}]
    recent_predictions: list[dict] = field(default_factory=list)
    top_risks: list[str] = field(default_factory=list)
    # [{name, critical_alerts, open_alerts}] — ranked by open CRITICAL alerts
    facilities_by_critical_alerts: list[dict] = field(default_factory=list)


class HealthAssistant:
    """
    Gemini-powered multilingual assistant for district health Q&A.

    All answers are strictly grounded in the DistrictContext provided at
    call time — the assistant is instructed not to speculate beyond the
    supplied data.
    """

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self._api_key:
            log.warning(
                "gemini_api_key_missing",
                msg="GEMINI_API_KEY not set; assistant will return fallback responses",
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def ask(
        self,
        question: str,
        context: DistrictContext,
        language: str = "en",
    ) -> str:
        """
        Answer a health-related question grounded in live district data.

        Parameters
        ----------
        question:  Natural-language question from the user.
        context:   Live district data used as the sole source of truth.
        language:  BCP-47 language code; must be in SUPPORTED_LANGUAGES.

        Returns
        -------
        str — The assistant's answer in the requested language, or a polite
              fallback message if the Gemini API call fails.
        """
        if language not in SUPPORTED_LANGUAGES:
            log.warning("unsupported_language", language=language, fallback="en")
            language = "en"

        language_name = SUPPORTED_LANGUAGES[language]

        system_instruction = (
            f"You are SmartHealth AI, a district health assistant for India. "
            f"For actual health questions, answer ONLY from the provided district "
            f"data, concisely and actionably. "
            f"If the user greets you or makes small talk (e.g. 'hi'), reply warmly "
            f"in one line and invite them to ask about facilities, stock, alerts, "
            f"or staffing — do NOT say data is unavailable for a greeting. "
            f"Only say data is insufficient when a genuine health question can't be "
            f"answered from the data. "
            f"Respond in {language_name}."
        )

        context_block = self._build_context_prompt(context)

        full_prompt = (
            f"{system_instruction}\n\n"
            f"--- DISTRICT DATA ---\n"
            f"{context_block}\n"
            f"--- END DATA ---\n\n"
            f"Question: {question}"
        )

        log.info(
            "assistant_ask",
            district=context.district_name,
            language=language,
            question_length=len(question),
        )

        return self._call_gemini(full_prompt, language)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call_gemini(self, prompt: str, language: str = "en") -> str:
        """
        POST to Gemini 2.0 Flash generateContent and return the response text.

        Falls back gracefully on any HTTP or parsing error.
        """
        if not self._api_key:
            return self._fallback_message(language)

        url = f"{GEMINI_API_BASE}/models/{GEMINI_MODEL}:generateContent?key={self._api_key}"
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 512,
                # Gemini 2.5 Flash "thinks" before answering by default, which adds
                # ~20-25s to a simple grounded lookup. Disable it — these answers
                # are direct reads over the supplied context, no reasoning budget
                # needed — cutting latency to a few seconds.
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }

        try:
            response = httpx.post(url, json=payload, timeout=30.0)
            response.raise_for_status()
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            log.info("gemini_response_ok", chars=len(text))
            return text.strip()

        except httpx.HTTPStatusError as exc:
            log.error(
                "gemini_http_error",
                status_code=exc.response.status_code,
                detail=exc.response.text[:200],
            )
        except httpx.HTTPError as exc:
            log.error("gemini_request_error", error=str(exc))
        except (KeyError, IndexError, ValueError) as exc:
            log.error("gemini_parse_error", error=str(exc))

        return self._fallback_message(language)

    def _build_context_prompt(self, context: DistrictContext) -> str:
        """
        Serialise a DistrictContext into a structured plain-text block that
        is easy for the model to parse and reason about.
        """
        lines: list[str] = [
            f"DISTRICT: {context.district_name}",
            f"FACILITIES: {context.total_facilities} total",
            f"ACTIVE ALERTS: {context.active_alerts}",
            f"PENDING REDISTRIBUTION PLANS: {context.pending_redistribution_plans}",
            f"AVG HEALTH SCORE: {context.avg_health_score:.1f}/100",
        ]

        # Critical facilities (score < 45)
        if context.critical_facilities:
            lines.append("CRITICAL FACILITIES (score < 45):")
            for fac in context.critical_facilities:
                name = fac.get("name", "Unknown")
                score = fac.get("score", "N/A")
                issue = fac.get("top_issue", "N/A")
                lines.append(f"  - {name}: {score}/100 — {issue}")
        else:
            lines.append("CRITICAL FACILITIES (score < 45): None")

        # Stockout predictions (within 3 days)
        if context.recent_predictions:
            lines.append("STOCKOUT PREDICTIONS (< 3 days):")
            for pred in context.recent_predictions:
                facility = pred.get("facility", "Unknown")
                medicine = pred.get("medicine", "Unknown")
                days = pred.get("days_until_stockout", "N/A")
                confidence = pred.get("confidence", 0)
                pct = int(float(confidence) * 100) if confidence != "N/A" else "N/A"
                lines.append(
                    f"  - {facility}: {medicine} runs out in {days} day(s) "
                    f"(confidence: {pct}%)"
                )
        else:
            lines.append("STOCKOUT PREDICTIONS (< 3 days): None")

        # Facilities ranked by open CRITICAL alerts (answers "which facility has
        # the most critical alerts")
        if context.facilities_by_critical_alerts:
            lines.append("")
            lines.append("FACILITIES BY OPEN CRITICAL ALERTS (highest first):")
            for fac in context.facilities_by_critical_alerts:
                lines.append(
                    f"  - {fac.get('name')}: {fac.get('critical_alerts', 0)} critical"
                    f", {fac.get('open_alerts', 0)} open total"
                )

        # Top risks
        if context.top_risks:
            lines.append(f"TOP RISKS: {', '.join(context.top_risks)}")
        else:
            lines.append("TOP RISKS: None identified")

        return "\n".join(lines)

    def _fallback_message(self, language: str) -> str:
        """Return a polite unavailability message in the requested language."""
        return _FALLBACK_MESSAGES.get(language, _FALLBACK_MESSAGES["en"])
