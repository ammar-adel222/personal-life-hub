"""Safe bidirectional synchronization with a local folder of .md and .txt notes."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from app import db

SUPPORTED_NOTE_EXTS = {".md", ".txt"}
IGNORED_DIR_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def _extract_title_from_file(path: Path, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading[:120]
        break
    return path.stem


def _slugify_filename(title: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "", title).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    return (cleaned[:60] or "note").strip(".-")


def sync_notes_folder(folder_path: str) -> dict[str, Any]:
    """Scan a local folder for .md/.txt files and import or refresh notes in SQLite."""
    clean_folder = (folder_path or "").strip()
    if not clean_folder:
        raise ValueError("No local notes folder is configured.")
    root = Path(clean_folder).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Notes folder does not exist: {root}")

    imported = 0
    updated = 0
    unchanged = 0
    errors: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in IGNORED_DIR_NAMES
        ]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            if fpath.suffix.lower() not in SUPPORTED_NOTE_EXTS:
                continue
            try:
                resolved = str(fpath.resolve())
                stat = fpath.stat()
                mtime = float(stat.st_mtime)
                text = fpath.read_text(encoding="utf-8", errors="replace").strip("\r\n")
                title = _extract_title_from_file(fpath, text)
                existing = db.get_note_by_source_path(resolved)
                if existing is None:
                    db.create_note(
                        title=title,
                        content=text,
                        tags=["synced"],
                        source_path=resolved,
                        file_mtime=mtime,
                    )
                    imported += 1
                else:
                    old_mtime = existing.get("file_mtime") or 0.0
                    if abs(mtime - float(old_mtime)) > 0.5:
                        db.update_note(
                            existing["id"],
                            {
                                "title": title,
                                "content": text,
                                "file_mtime": mtime,
                            },
                        )
                        updated += 1
                    else:
                        unchanged += 1
            except Exception as exc:
                errors.append(f"{fpath.name}: {exc}")

    return {
        "folder": str(root),
        "imported": imported,
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors,
    }


def write_note_to_source(note: dict[str, Any]) -> dict[str, Any]:
    """Write an edited synced note back to its source_path on disk cleanly."""
    source_path = note.get("source_path")
    if not source_path:
        return note
    target = Path(source_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = note.get("content", "")
    title = (note.get("title") or "").strip()
    if target.suffix.lower() == ".md" and title:
        first_non_empty = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
        if not first_non_empty.startswith("#"):
            disk_text = f"# {title}\n\n{content}\n"
        else:
            disk_text = f"{content}\n"
    else:
        disk_text = f"{content}\n"
    target.write_text(disk_text, encoding="utf-8")
    new_mtime = float(target.stat().st_mtime)
    updated = db.update_note(note["id"], {"file_mtime": new_mtime})
    return updated or note


def save_new_note_to_folder(note: dict[str, Any], folder_path: str) -> dict[str, Any]:
    """Create a .md file inside folder_path for a newly created app note."""
    root = Path(folder_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"Notes folder does not exist: {root}")
    slug = _slugify_filename(note.get("title") or "note")
    candidate = root / f"{slug}.md"
    counter = 2
    while candidate.exists():
        candidate = root / f"{slug}-{counter}.md"
        counter += 1
    title = (note.get("title") or "").strip()
    content = note.get("content", "")
    disk_text = f"# {title}\n\n{content}\n" if title else f"{content}\n"
    candidate.write_text(disk_text, encoding="utf-8")
    mtime = float(candidate.stat().st_mtime)
    tags = list(note.get("tags") or [])
    if "synced" not in [t.lower() for t in tags]:
        tags.append("synced")
    updated = db.update_note(
        note["id"],
        {
            "source_path": str(candidate.resolve()),
            "file_mtime": mtime,
            "tags": tags,
        },
    )
    return updated or note
