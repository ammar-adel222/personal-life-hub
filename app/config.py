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
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
)
DEFAULT_SETTINGS: dict[str, Any] = {
    "groq_api_key": "",
    "groq_model": "llama-3.3-70b-versatile",
    "voice_mode": "auto_ai",
    "mic_device": None,
    "notes_folder": "",
    "weight_unit": "kg",
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
        elif key == "notes_folder" and isinstance(value, str):
            current[key] = value.strip()
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
        "data_dir": str(get_data_dir()),
    }
