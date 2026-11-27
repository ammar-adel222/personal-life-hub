"""Configuration and private local storage management.

All user data (SQLite database, settings.json with API key, and uploaded media)
lives outside the Git repository under %LOCALAPPDATA%\\PersonalLifeHub (or the
directory specified by LIFEHUB_DATA_DIR for isolated testing).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "PersonalLifeHub"
ALLOWED_GROQ_MODELS = (
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-20b",
)
ALLOWED_TTS_MODES = ("questions_only", "all", "off")
ALLOWED_TTS_PROVIDERS = ("groq", "local", "google")
ALLOWED_ORPHEUS_VOICES = ("troy", "hannah", "autumn", "diana", "austin", "daniel")

DEFAULT_SETTINGS: dict[str, Any] = {
    "groq_api_key": "",
    "groq_model": "openai/gpt-oss-120b",
    "voice_mode": "auto_ai",
    "mic_device": None,
    "notes_folder": "",
    "weight_unit": "kg",
    "tts_mode": "questions_only",
    "tts_provider": "groq",
    "tts_voice": "troy",
    "auto_silence": False,
    "silence_seconds": 3.5,
    "global_hotkey": "ctrl+shift+space",
    "minimize_to_tray": True,
    "floating_indicator": True,
    "sound_feedback": True,
    "cloud_folder": "",
    "cloud_auto_sync": False,
    "cloud_sync_notes": True,
    "cloud_sync_watchlist": True,
    "cloud_sync_backup": True,
    "cloud_last_sync": "",
    "gdrive_folder_name": "PersonalLifeHub",
    "gdrive_account_email": "",
    "gdrive_last_sync": "",
}


def get_data_dir() -> Path:
    """Return the local data directory, creating it and its media folder if needed."""
    override = os.environ.get("LIFEHUB_DATA_DIR", "").strip()
    if override:
        base = Path(override)
    else:
        local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
        base = (Path(local_app_data) if local_app_data else Path.home()) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    (base / "media").mkdir(parents=True, exist_ok=True)
    return base


def get_media_dir() -> Path:
    """Return the directory where uploaded art reference images are stored."""
    media_dir = get_data_dir() / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    return media_dir


def get_db_path() -> Path:
    """Return the SQLite database file path."""
    return get_data_dir() / "lifehub.db"


def get_settings_path() -> Path:
    """Return the settings.json file path."""
    return get_data_dir() / "settings.json"


def get_gdrive_credentials_path() -> Path:
    """Return private path for the user's Google Drive OAuth client secrets JSON."""
    return get_data_dir() / "google_credentials.json"


def get_gdrive_token_path() -> Path:
    """Return private path for the user's Google Drive OAuth user token JSON."""
    return get_data_dir() / "google_token.json"


def get_tray_icon_path() -> Path:
    """Return private path for cached System Tray .ico file."""
    return get_data_dir() / "tray.ico"


def get_runtime_info_path() -> Path:
    """Return private path storing the active desktop PID/port for single-instance focus."""
    return get_data_dir() / "runtime.json"


def _normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_SETTINGS)
    if raw:
        for key, value in raw.items():
            if key in DEFAULT_SETTINGS:
                merged[key] = value
    if merged["voice_mode"] not in ("auto_ai", "fill_input"):
        merged["voice_mode"] = "auto_ai"
    if merged["weight_unit"] not in ("kg", "lbs"):
        merged["weight_unit"] = "kg"
    if not isinstance(merged["groq_api_key"], str):
        merged["groq_api_key"] = ""
    if not isinstance(merged["notes_folder"], str):
        merged["notes_folder"] = ""
    if merged["groq_model"] not in ALLOWED_GROQ_MODELS:
        merged["groq_model"] = DEFAULT_SETTINGS["groq_model"]
    if merged["tts_mode"] not in ALLOWED_TTS_MODES:
        merged["tts_mode"] = DEFAULT_SETTINGS["tts_mode"]
    if merged["tts_provider"] not in ALLOWED_TTS_PROVIDERS:
        merged["tts_provider"] = DEFAULT_SETTINGS["tts_provider"]
    if merged["tts_voice"] not in ALLOWED_ORPHEUS_VOICES:
        merged["tts_voice"] = DEFAULT_SETTINGS["tts_voice"]
    merged["auto_silence"] = bool(merged.get("auto_silence", False))
    try:
        sil = float(merged.get("silence_seconds", 3.5))
    except (TypeError, ValueError):
        sil = 3.5
    merged["silence_seconds"] = max(0.6, min(6.0, round(sil, 1)))
    hk = str(merged.get("global_hotkey", "ctrl+shift+space") or "").strip().lower()
    merged["global_hotkey"] = hk
    for bkey in (
        "minimize_to_tray",
        "floating_indicator",
        "sound_feedback",
        "cloud_auto_sync",
        "cloud_sync_notes",
        "cloud_sync_watchlist",
        "cloud_sync_backup",
    ):
        merged[bkey] = bool(merged.get(bkey, DEFAULT_SETTINGS[bkey]))
    for skey in ("cloud_folder", "cloud_last_sync", "gdrive_account_email", "gdrive_last_sync"):
        if not isinstance(merged.get(skey), str):
            merged[skey] = ""
        else:
            merged[skey] = merged[skey].strip()
    gdir = str(merged.get("gdrive_folder_name") or "PersonalLifeHub").strip()
    merged["gdrive_folder_name"] = gdir[:80] or "PersonalLifeHub"
    return merged


def load_settings() -> dict[str, Any]:
    """Load settings from settings.json, returning normalized defaults if absent."""
    path = get_settings_path()
    if not path.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULT_SETTINGS)
        return _normalize_settings(data)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Persist normalized settings to settings.json atomically."""
    normalized = _normalize_settings(settings)
    path = get_settings_path()
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    return normalized


def update_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply partial updates to settings.json and return the updated config."""
    current = load_settings()
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            continue
        if key == "groq_api_key" and isinstance(value, str):
            current[key] = value.strip()
        elif key in ("notes_folder", "cloud_folder", "gdrive_folder_name") and isinstance(value, str):
            current[key] = value.strip()
        elif key == "global_hotkey" and isinstance(value, str):
            current[key] = value.strip().lower()
        else:
            current[key] = value
    return save_settings(current)


def mask_api_key(api_key: str) -> str:
    """Return a masked representation of a Groq API key suitable for UI display."""
    clean = (api_key or "").strip()
    if not clean:
        return ""
    if len(clean) <= 8:
        return "*" * len(clean)
    return f"{clean[:4]}...{clean[-4:]}"


def get_public_settings() -> dict[str, Any]:
    """Return settings safe to send to the frontend (never exposing the raw API key)."""
    settings = load_settings()
    raw_key = settings.get("groq_api_key", "")
    return {
        "has_api_key": bool(raw_key),
        "groq_api_key_masked": mask_api_key(raw_key),
        "groq_model": settings["groq_model"],
        "allowed_models": list(ALLOWED_GROQ_MODELS),
        "voice_mode": settings["voice_mode"],
        "mic_device": settings["mic_device"],
        "notes_folder": settings["notes_folder"],
        "weight_unit": settings["weight_unit"],
        "tts_mode": settings["tts_mode"],
        "tts_provider": settings["tts_provider"],
        "tts_voice": settings["tts_voice"],
        "auto_silence": settings["auto_silence"],
        "silence_seconds": settings["silence_seconds"],
        "global_hotkey": settings["global_hotkey"],
        "minimize_to_tray": settings["minimize_to_tray"],
        "floating_indicator": settings["floating_indicator"],
        "sound_feedback": settings["sound_feedback"],
        "cloud_folder": settings["cloud_folder"],
        "cloud_auto_sync": settings["cloud_auto_sync"],
        "cloud_sync_notes": settings["cloud_sync_notes"],
        "cloud_sync_watchlist": settings["cloud_sync_watchlist"],
        "cloud_sync_backup": settings["cloud_sync_backup"],
        "cloud_last_sync": settings["cloud_last_sync"],
        "gdrive_folder_name": settings["gdrive_folder_name"],
        "gdrive_account_email": settings["gdrive_account_email"],
        "gdrive_last_sync": settings["gdrive_last_sync"],
        "allowed_tts_modes": list(ALLOWED_TTS_MODES),
        "allowed_tts_providers": list(ALLOWED_TTS_PROVIDERS),
        "allowed_tts_voices": list(ALLOWED_ORPHEUS_VOICES),
        "data_dir": str(get_data_dir()),
    }
