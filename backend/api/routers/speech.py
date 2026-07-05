"""
speech.py — Gemini (Google AI Studio) speech-to-speech helpers.

  POST /speech/stt  — base64 audio  -> transcript          (Gemini multimodal)
  POST /speech/tts  — {text, language} -> WAV audio (base64) (Gemini TTS)

The voice loop (dashboard + field-app) is:
  mic (MediaRecorder) -> /speech/stt -> /assistant/query -> /speech/tts -> play

Both endpoints degrade to HTTP 503 when GEMINI_API_KEY is unset, and surface a
clear 502 (with the upstream status) when Gemini rejects the call — e.g. a
zero-quota key returns 429, which the client shows as "voice temporarily
unavailable" without breaking the text assistant.
"""
from __future__ import annotations

import base64
import struct
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth.rbac import require_role
from config import get_settings

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/speech", tags=["speech"])

_any_user = require_role(
    "FIELD_WORKER", "PHC_ADMIN", "DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN"
)

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
STT_MODEL = "gemini-2.0-flash"              # multimodal audio understanding
TTS_MODEL = "gemini-2.5-flash-preview-tts"  # native text-to-speech
TTS_VOICE = "Kore"                          # prebuilt multilingual voice


def _api_key() -> str:
    key = get_settings().gemini_api_key or ""
    if not key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Speech is unavailable: GEMINI_API_KEY is not configured.",
        )
    return key


def _pcm_to_wav(pcm: bytes, sample_rate: int = 24000, channels: int = 1, bits: int = 16) -> bytes:
    """Wrap raw little-endian PCM (what Gemini TTS returns) in a WAV container
    so browsers can play it directly."""
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_len = len(pcm)
    return (
        b"RIFF" + struct.pack("<I", 36 + data_len) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
        + b"data" + struct.pack("<I", data_len) + pcm
    )


async def _post_gemini(url: str, payload: dict, what: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        log.error(f"{what}_gemini_error", status=code, body=exc.response.text[:200])
        hint = " (Gemini quota exhausted — set a funded AI Studio key)" if code == 429 else ""
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{what} failed upstream ({code}){hint}.",
        )
    except httpx.HTTPError as exc:
        log.error(f"{what}_gemini_transport_error", error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"{what} failed: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Speech-to-text
# ─────────────────────────────────────────────────────────────────────────────

class STTRequest(BaseModel):
    audio_base64: str = Field(..., description="Base64-encoded audio bytes")
    mime: str = Field("audio/webm", description="Audio MIME type, e.g. audio/webm")
    language: str = "en"


class STTResponse(BaseModel):
    text: str


@router.post("/stt", response_model=STTResponse, summary="Transcribe speech (Gemini)")
async def speech_to_text(body: STTRequest, current_user: Any = Depends(_any_user)) -> STTResponse:
    key = _api_key()
    if not body.audio_base64:
        raise HTTPException(status_code=400, detail="Empty audio.")
    prompt = (
        "Transcribe the following audio verbatim. Output ONLY the transcript text, "
        "in the same language that was actually spoken. Do not translate, do not add "
        "commentary, quotes, or timestamps. If nothing intelligible was said, return an empty string."
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": body.mime or "audio/webm", "data": body.audio_base64}},
                ]
            }
        ]
    }
    url = f"{GEMINI_BASE}/models/{STT_MODEL}:generateContent?key={key}"
    data = await _post_gemini(url, payload, "speech-to-text")
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        text = ""
    return STTResponse(text=text)


# ─────────────────────────────────────────────────────────────────────────────
# Text-to-speech
# ─────────────────────────────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    text: str
    language: str = "en"


class TTSResponse(BaseModel):
    audio_base64: str
    mime: str = "audio/wav"


@router.post("/tts", response_model=TTSResponse, summary="Synthesize speech (Gemini TTS)")
async def text_to_speech(body: TTSRequest, current_user: Any = Depends(_any_user)) -> TTSResponse:
    key = _api_key()
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text to synthesize.")
    # Gemini TTS auto-detects language from the text; the answer text is already
    # in the requested language, so no explicit language tag is needed.
    payload = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": TTS_VOICE}}},
        },
    }
    url = f"{GEMINI_BASE}/models/{TTS_MODEL}:generateContent?key={key}"
    data = await _post_gemini(url, payload, "text-to-speech")
    try:
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(part["data"])
        rate = 24000
        mime = part.get("mimeType", "")
        if "rate=" in mime:
            try:
                rate = int(mime.split("rate=")[1].split(";")[0])
            except ValueError:
                pass
    except (KeyError, IndexError, TypeError):
        raise HTTPException(status_code=502, detail="Text-to-speech returned no audio.")
    wav = _pcm_to_wav(pcm, sample_rate=rate)
    return TTSResponse(audio_base64=base64.b64encode(wav).decode(), mime="audio/wav")
