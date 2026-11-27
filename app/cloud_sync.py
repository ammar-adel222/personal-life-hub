"""Cloud backup & sync: Instant Cloud Folder Auto-Sync + Direct Google Drive API OAuth."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from app import db
from app.config import (
    get_gdrive_credentials_path,
    get_gdrive_token_path,
    load_settings,
    update_settings,
)

GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
FOLDER_MIME = "application/vnd.google-apps.folder"
_AUTO_SYNC_LOCK = threading.Lock()


def _gdrive_libs_available() -> bool:
    try:
        import google.auth.transport.requests  # noqa: F401
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
        import googleapiclient.http  # noqa: F401

        return True
    except ImportError:
        return False


def _slugify(text: str, fallback: str = "item") -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", str(text or "")).strip()
    clean = re.sub(r"\s+", "-", clean).strip(".-")
    return (clean[:60] or fallback).strip(".-") or fallback


def detect_local_cloud_folders() -> list[dict[str, str]]:
    """Detect common Windows cloud drive sync folders (Google Drive Desktop, OneDrive, Dropbox)."""
    candidates: list[tuple[str, Path]] = []
    home = Path.home()

    for drive in ("G:\\", "H:\\", "D:\\", "E:\\"):
        p = Path(drive) / "My Drive"
        if p.exists() and p.is_dir():
            candidates.append(("Google Drive", p))

    for sub in ("Google Drive", "Google Drive\\My Drive", "My Drive"):
        p = home / sub
        if p.exists() and p.is_dir():
            candidates.append(("Google Drive", p))

    for env_key in ("OneDriveConsumer", "OneDrive", "OneDriveCommercial"):
        raw = os.environ.get(env_key, "").strip()
        if raw:
            p = Path(raw)
            if p.exists() and p.is_dir():
                candidates.append(("OneDrive", p))
    if (home / "OneDrive").exists() and (home / "OneDrive").is_dir():
        candidates.append(("OneDrive", home / "OneDrive"))

    if (home / "Dropbox").exists() and (home / "Dropbox").is_dir():
        candidates.append(("Dropbox", home / "Dropbox"))

    seen: set[str] = set()
    detected: list[dict[str, str]] = []
    for provider, path in candidates:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        key = resolved.lower()
        if key in seen:
            continue
        seen.add(key)
        detected.append(
            {
                "provider": provider,
                "path": resolved,
                "suggested_target": str(Path(resolved) / "PersonalLifeHub"),
            }
        )
    return detected


def _render_note_markdown(note: dict[str, Any]) -> str:
    lines: list[str] = [f"# {note['title'] or 'Untitled Note'}"]
    meta_parts: list[str] = []
    if note.get("folder"):
        meta_parts.append(f"Notebook: {note['folder']}")
    if note.get("tags"):
        meta_parts.append("Tags: " + ", ".join(f"#{t}" for t in note["tags"]))
    if meta_parts:
        lines.append(f"> {' · '.join(meta_parts)}")
    if note.get("summary"):
        lines.append(f"> **Summary:** {note['summary']}")
    lines.append("")
    lines.append((note.get("content") or "").rstrip())
    return "\n".join(lines).rstrip() + "\n"


def _render_watchlist_markdown(items: list[dict[str, Any]]) -> str:
    lines = ["# Watchlist & Playlists", ""]
    if not items:
        lines.append("_No watchlist items saved yet._")
        return "\n".join(lines) + "\n"

    groups = [("watching", "Currently Watching"), ("to_watch", "To Watch"), ("watched", "Watched")]
    for st, heading in groups:
        sub = [it for it in items if it["status"] == st]
        if not sub:
            continue
        lines.append(f"## {heading}")
        for it in sub:
            title_part = f"[{it['title']}]({it['url']})" if it.get("url") else it["title"]
            meta: list[str] = [it["content_type"].title()]
            if it.get("source"):
                meta.append(it["source"])
            if it.get("category"):
                meta.append(it["category"])
            if it.get("progress_total", 0) > 0:
                meta.append(f"Progress {it['progress_current']}/{it['progress_total']}")
            lines.append(f"- {title_part} ({' · '.join(meta)})")
            if it.get("notes"):
                lines.append(f"  - Note: {it['notes']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_tasks_markdown() -> str:
    lines = ["# Tasks & Want to Do", ""]
    for lt, heading in (("daily", "Active Tasks"), ("want_to_do", "Want to Do / Someday")):
        items = db.list_tasks(list_type=lt, include_done=True)
        lines.append(f"## {heading}")
        if not items:
            lines.append("_None_")
        for t in items:
            mark = "x" if t["done"] else ("~" if t["status"] == "partially_completed" else " ")
            extras: list[str] = []
            if t.get("timeline"):
                extras.append(t["timeline"])
            if t.get("frequency"):
                extras.append(t["frequency"])
            if t.get("due_date"):
                extras.append(f"Due {t['due_date']}")
            suffix = f" _({' · '.join(extras)})_" if extras else ""
            lines.append(f"- [{mark}] **{t['title']}**{suffix}")
            for sub in t.get("checklist_items", []):
                sm = "x" if sub["done"] else " "
                lines.append(f"  - [{sm}] {sub['text']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_export_files(
    include_notes: bool = True,
    include_watchlist: bool = True,
    include_backup: bool = True,
) -> dict[str, str]:
    """Build relative POSIX paths -> UTF-8 content to mirror to local cloud or Google Drive."""
    files: dict[str, str] = {}

    if include_notes:
        for note in db.list_notes():
            slug = _slugify(note.get("title") or "note", "note")
            fname = f"{slug}-{note['id']}.md"
            folder = (note.get("folder") or "").strip()
            if folder:
                rel = f"Notes/{_slugify(folder, 'General')}/{fname}"
            else:
                rel = f"Notes/{fname}"
            files[rel] = _render_note_markdown(note)

    if include_watchlist:
        files["Watchlist.md"] = _render_watchlist_markdown(db.list_watchlist())
        files["Tasks.md"] = _render_tasks_markdown()

    if include_backup:
        backup = db.export_all_data()
        files["lifehub-backup.json"] = json.dumps(backup, indent=2, ensure_ascii=False) + "\n"

    return files


def sync_to_cloud_folder(folder_path: str | None = None) -> dict[str, Any]:
    """Export organized Markdown notes, watchlist, tasks, and JSON backup into a local cloud folder."""
    settings = load_settings()
    raw_target = (folder_path if folder_path is not None else settings.get("cloud_folder", "")).strip()
    if not raw_target:
        raise ValueError("Configure a Cloud Sync folder path in Settings first.")

    target_root = Path(raw_target).expanduser().resolve()
    target_root.mkdir(parents=True, exist_ok=True)

    payloads = build_export_files(
        include_notes=bool(settings.get("cloud_sync_notes", True)),
        include_watchlist=bool(settings.get("cloud_sync_watchlist", True)),
        include_backup=bool(settings.get("cloud_sync_backup", True)),
    )

    written = 0
    unchanged = 0
    for rel_path, content in payloads.items():
        dest = (target_root / rel_path).resolve()
        if not dest.is_relative_to(target_root):
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        existing = None
        if dest.exists() and dest.is_file():
            try:
                existing = dest.read_text(encoding="utf-8")
            except OSError:
                existing = None
        if existing == content:
            unchanged += 1
        else:
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(dest)
            written += 1

    ts = db.now_iso()
    update_settings({"cloud_last_sync": ts})
    return {
        "folder": str(target_root),
        "files_written": written,
        "files_unchanged": unchanged,
        "total_files": len(payloads),
        "synced_at": ts,
    }


def maybe_auto_sync_cloud_folder() -> None:
    """Trigger a non-blocking cloud folder sync if cloud_auto_sync is enabled."""
    if os.environ.get("LIFEHUB_DISABLE_AUTO_SYNC", "").strip() == "1":
        return
    settings = load_settings()
    if not settings.get("cloud_auto_sync") or not settings.get("cloud_folder", "").strip():
        return

    def _worker() -> None:
        with _AUTO_SYNC_LOCK:
            try:
                sync_to_cloud_folder()
            except Exception:
                pass

    threading.Thread(target=_worker, daemon=True, name="lifehub-cloud-autosync").start()


# ---------------------------------------------------------------------------
# Direct Google Drive API OAuth (`drive.file` scope)
# ---------------------------------------------------------------------------

def get_gdrive_status() -> dict[str, Any]:
    settings = load_settings()
    cred_path = get_gdrive_credentials_path()
    token_path = get_gdrive_token_path()
    return {
        "libraries_available": _gdrive_libs_available(),
        "has_credentials": cred_path.exists() and cred_path.is_file(),
        "connected": token_path.exists() and token_path.is_file(),
        "account_email": settings.get("gdrive_account_email", ""),
        "folder_name": settings.get("gdrive_folder_name", "PersonalLifeHub"),
        "last_sync": settings.get("gdrive_last_sync", ""),
    }


def _validate_and_save_credentials_json(raw_json: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON file: {exc}") from exc
    if not isinstance(parsed, dict) or ("installed" not in parsed and "web" not in parsed):
        raise ValueError(
            "This JSON does not look like a Google OAuth Desktop credentials.json "
            "(expected top-level 'installed' or 'web' key)."
        )
    dest = get_gdrive_credentials_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    tmp.replace(dest)
    return get_gdrive_status()


def import_gdrive_credentials_file(src_path: str) -> dict[str, Any]:
    clean = (src_path or "").strip()
    if not clean:
        raise ValueError("No credentials file selected.")
    p = Path(clean)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")
    return _validate_and_save_credentials_json(p.read_text(encoding="utf-8"))


def save_gdrive_credentials_raw(raw_json: str) -> dict[str, Any]:
    return _validate_and_save_credentials_json(raw_json)


def connect_gdrive_oauth() -> dict[str, Any]:
    if not _gdrive_libs_available():
        raise RuntimeError(
            "Google Drive client packages are not installed in this Python environment."
        )
    cred_path = get_gdrive_credentials_path()
    if not cred_path.exists():
        raise ValueError("Load your Google Cloud OAuth credentials.json first.")

    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), GDRIVE_SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=180)
    token_path = get_gdrive_token_path()
    token_path.write_text(creds.to_json(), encoding="utf-8")

    email = ""
    try:
        svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = svc.about().get(fields="user(emailAddress)").execute()
        email = str((about.get("user") or {}).get("emailAddress") or "").strip()
    except Exception:
        pass
    update_settings({"gdrive_account_email": email})
    return get_gdrive_status()


def disconnect_gdrive() -> dict[str, Any]:
    token_path = get_gdrive_token_path()
    if token_path.exists():
        token_path.unlink(missing_ok=True)
    update_settings({"gdrive_account_email": ""})
    return get_gdrive_status()


def _build_gdrive_service() -> Any:
    if not _gdrive_libs_available():
        raise RuntimeError("Google Drive packages are not installed.")
    token_path = get_gdrive_token_path()
    if not token_path.exists():
        raise ValueError("Not signed in to Google Drive yet. Click 'Sign in with Google Drive' in Settings.")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials.from_authorized_user_file(str(token_path), GDRIVE_SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    if not creds or not creds.valid:
        raise ValueError("Google Drive session expired. Please reconnect in Settings.")
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _escape_drive_query(val: str) -> str:
    return val.replace("\\", "\\\\").replace("'", "\\'")


def _find_or_create_drive_folder(
    service: Any, name: str, parent_id: str | None = None
) -> str:
    q_parts = [
        f"name = '{_escape_drive_query(name)}'",
        f"mimeType = '{FOLDER_MIME}'",
        "trashed = false",
    ]
    if parent_id:
        q_parts.append(f"'{_escape_drive_query(parent_id)}' in parents")
    res = (
        service.files()
        .list(q=" and ".join(q_parts), spaces="drive", fields="files(id, name)", pageSize=5)
        .execute()
    )
    files = res.get("files") or []
    if files:
        return str(files[0]["id"])
    body: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
    if parent_id:
        body["parents"] = [parent_id]
    created = service.files().create(body=body, fields="id").execute()
    return str(created["id"])


def sync_to_gdrive(service: Any | None = None) -> dict[str, Any]:
    """Upload or update Notes, Watchlist, Tasks, and Backup into the user's Google Drive folder."""
    from googleapiclient.http import MediaInMemoryUpload

    settings = load_settings()
    svc = service if service is not None else _build_gdrive_service()
    root_name = settings.get("gdrive_folder_name", "PersonalLifeHub") or "PersonalLifeHub"
    root_id = _find_or_create_drive_folder(svc, root_name, parent_id=None)

    folder_cache: dict[tuple[str, ...], str] = {(): root_id}
    payloads = build_export_files(
        include_notes=bool(settings.get("cloud_sync_notes", True)),
        include_watchlist=bool(settings.get("cloud_sync_watchlist", True)),
        include_backup=bool(settings.get("cloud_sync_backup", True)),
    )

    created_count = 0
    updated_count = 0
    for rel_path, content in payloads.items():
        parts = rel_path.split("/")
        dir_parts = parts[:-1]
        filename = parts[-1]

        parent_id = root_id
        for idx in range(1, len(dir_parts) + 1):
            key = tuple(dir_parts[:idx])
            if key not in folder_cache:
                folder_cache[key] = _find_or_create_drive_folder(
                    svc, dir_parts[idx - 1], parent_id=parent_id
                )
            parent_id = folder_cache[key]

        mime = "application/json" if filename.endswith(".json") else "text/markdown"
        media = MediaInMemoryUpload(content.encode("utf-8"), mimetype=mime, resumable=False)

        q = (
            f"name = '{_escape_drive_query(filename)}' "
            f"and '{_escape_drive_query(parent_id)}' in parents "
            "and trashed = false"
        )
        existing = (
            svc.files().list(q=q, spaces="drive", fields="files(id)", pageSize=5).execute().get("files") or []
        )
        if existing:
            svc.files().update(fileId=existing[0]["id"], media_body=media, fields="id").execute()
            updated_count += 1
        else:
            meta = {"name": filename, "parents": [parent_id]}
            svc.files().create(body=meta, media_body=media, fields="id").execute()
            created_count += 1

    ts = db.now_iso()
    update_settings({"gdrive_last_sync": ts})
    return {
        "folder_name": root_name,
        "folder_id": root_id,
        "created": created_count,
        "updated": updated_count,
        "total_files": len(payloads),
        "synced_at": ts,
    }
