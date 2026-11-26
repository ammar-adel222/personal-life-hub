"""Text-to-Speech (TTS) synthesis with Groq Orpheus primary and local fallback."""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import ALLOWED_ORPHEUS_VOICES, load_settings

GROQ_SPEECH_URL = "https://api.groq.com/openai/v1/audio/speech"
ORPHEUS_MODEL = "canopylabs/orpheus-v1-english"


class TTSError(Exception):
    """Raised when cloud TTS cannot generate audio and should fall back locally."""


def _clean_text_for_speech(text: str, max_chars: int = 260) -> str:
    clean = re.sub(r"[`*_#>~]+", " ", text or "")
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) <= max_chars:
        return clean
    truncated = clean[:max_chars].rsplit(" ", 1)[0].strip()
    return (truncated or clean[:max_chars]).rstrip(",;:") + "."


async def _synthesize_google_tts(_text: str) -> bytes:
    """Placeholder hook for optional Google Cloud / Gemini TTS integration later."""
    raise TTSError("Google TTS is not configured yet — falling back to built-in speech.")


async def synthesize_speech(
    text: str,
    provider: str | None = None,
    voice: str | None = None,
) -> dict[str, Any]:
    """Synthesize spoken WAV audio via Groq Orpheus or indicate local browser fallback."""
    clean_text = _clean_text_for_speech(text)
    if not clean_text:
        return {"audio_bytes": None, "fallback_local": True, "reason": "Empty text."}

    settings = load_settings()
    chosen_provider = (provider or settings.get("tts_provider") or "groq").strip().lower()
    chosen_voice = (voice or settings.get("tts_voice") or "troy").strip().lower()
    if chosen_voice not in ALLOWED_ORPHEUS_VOICES:
        chosen_voice = "troy"

    if chosen_provider == "local":
        return {"audio_bytes": None, "fallback_local": True, "reason": "Local speech selected."}

    if chosen_provider == "google":
        try:
            wav_bytes = await _synthesize_google_tts(clean_text)
            return {
                "audio_bytes": wav_bytes,
                "media_type": "audio/wav",
                "provider": "google",
                "voice": chosen_voice,
                "fallback_local": False,
            }
        except Exception as exc:
            return {"audio_bytes": None, "fallback_local": True, "reason": str(exc)}

    api_key = settings.get("groq_api_key", "").strip()
    if not api_key:
        return {
            "audio_bytes": None,
            "fallback_local": True,
            "reason": "No Groq API key configured — using built-in speech.",
        }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                GROQ_SPEECH_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": ORPHEUS_MODEL,
                    "input": clean_text,
                    "voice": chosen_voice,
                    "response_format": "wav",
                },
            )
        if resp.status_code == 200 and resp.content:
            return {
                "audio_bytes": resp.content,
                "media_type": "audio/wav",
                "provider": "groq",
                "voice": chosen_voice,
                "fallback_local": False,
            }
        return {
            "audio_bytes": None,
            "fallback_local": True,
            "reason": f"Groq TTS returned HTTP {resp.status_code} — using built-in speech.",
        }
    except Exception as exc:
        return {
            "audio_bytes": None,
            "fallback_local": True,
            "reason": f"Groq TTS unavailable ({exc}) — using built-in speech.",
        }
