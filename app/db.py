"""SQLite persistence layer for Personal Life Hub."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date as dt_date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app.config import get_db_path, get_media_dir

TASK_LIST_TYPES = ("daily", "want_to_do")
TASK_PRIORITIES = ("normal", "high")
TASK_STATUSES = ("not_started", "partially_completed", "completed")
TASK_PROGRESS_MODES = ("auto", "percent", "days", "checklist")
DEFAULT_NOTE_FOLDERS = ("Lectures", "Projects", "Study", "Personal", "Ideas")
NOTE_SORTS = ("updated", "created", "title")
DEFAULT_STUDY_SUBJECTS = ("Study", "Lectures", "Coding", "Projects", "Art", "Reading", "Research")
STUDY_MODES = ("countdown", "stopwatch", "manual")
TIMER_MODES = ("countdown", "stopwatch")
TIMER_PHASES = ("focus", "break")
ACTIVITY_DOMAINS = ("gym", "art")
ART_STATUSES = ("idea", "wip", "finished")
GAME_STATUSES = ("playing", "want_to_play", "finished", "on_hold")
WATCHLIST_TYPES = ("playlist", "video", "course", "podcast", "article")
WATCHLIST_STATUSES = ("to_watch", "watching", "watched")


def today_str() -> str:
    """Return local date formatted as YYYY-MM-DD."""
    return datetime.now().strftime("%Y-%m-%d")


def now_iso() -> str:
    """Return current local ISO-8601 timestamp to the second."""
    return datetime.now().isoformat(timespec="seconds")


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    list_type TEXT NOT NULL DEFAULT 'daily' CHECK(list_type IN ('daily', 'want_to_do')),
    category TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('normal', 'high')),
    start_date TEXT,
    due_date TEXT,
    timeline TEXT NOT NULL DEFAULT '',
    frequency TEXT NOT NULL DEFAULT '',
    parent_task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    parent_item_id INTEGER,
    is_recurring INTEGER NOT NULL DEFAULT 0,
    target_days INTEGER NOT NULL DEFAULT 0,
    daily_target_minutes INTEGER NOT NULL DEFAULT 0,
    progress_mode TEXT NOT NULL DEFAULT 'auto',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    done INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'not_started'
        CHECK(status IN ('not_started', 'partially_completed', 'completed')),
    progress_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS task_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS task_daily_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL,
    UNIQUE(task_id, date)
);

CREATE TABLE IF NOT EXISTS task_progress_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    folder TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    pinned INTEGER NOT NULL DEFAULT 0,
    source_path TEXT UNIQUE,
    file_mtime REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL CHECK(domain IN ('gym', 'art')),
    date TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    UNIQUE(domain, date)
);

CREATE TABLE IF NOT EXISTS gym_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    exercise TEXT NOT NULL,
    sets INTEGER NOT NULL DEFAULT 1,
    reps INTEGER NOT NULL DEFAULT 0,
    weight REAL,
    unit TEXT NOT NULL DEFAULT 'kg',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS art_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'idea' CHECK(status IN ('idea', 'wip', 'finished')),
    notes TEXT NOT NULL DEFAULT '',
    image_files TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'want_to_play'
        CHECK(status IN ('playing', 'want_to_play', 'finished', 'on_hold')),
    platform TEXT NOT NULL DEFAULT '',
    rating INTEGER CHECK(rating IS NULL OR (rating >= 1 AND rating <= 10)),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capture_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'text',
    summary TEXT NOT NULL DEFAULT '',
    actions_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'Voice Session',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    actions_json TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT 'text',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL DEFAULT 'Study',
    topic TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'countdown' CHECK(mode IN ('countdown', 'stopwatch', 'manual')),
    duration_seconds INTEGER NOT NULL,
    planned_seconds INTEGER,
    date TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT NOT NULL,
    task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS active_timer (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    active INTEGER NOT NULL DEFAULT 0,
    paused INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL DEFAULT 'countdown',
    phase TEXT NOT NULL DEFAULT 'focus',
    subject TEXT NOT NULL DEFAULT 'Study',
    topic TEXT NOT NULL DEFAULT '',
    task_id INTEGER,
    note_id INTEGER,
    target_seconds INTEGER NOT NULL DEFAULT 1500,
    break_seconds INTEGER NOT NULL DEFAULT 300,
    accumulated_seconds INTEGER NOT NULL DEFAULT 0,
    started_at_epoch REAL,
    started_at_iso TEXT,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS watchlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT 'playlist'
        CHECK(content_type IN ('playlist', 'video', 'course', 'podcast', 'article')),
    source TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'to_watch'
        CHECK(status IN ('to_watch', 'watching', 'watched')),
    progress_current INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_list_done ON tasks(list_type, done);
CREATE INDEX IF NOT EXISTS idx_task_items_task ON task_items(task_id, position ASC, id ASC);
CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(pinned DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_domain_date ON activity_days(domain, date DESC);
CREATE INDEX IF NOT EXISTS idx_gym_logs_date ON gym_logs(date DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_gym_logs_exercise ON gym_logs(exercise COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_art_status ON art_items(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_games_status ON games(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_msgs_session ON agent_messages(session_id, id ASC);
"""

_initialized_db_paths: set[str] = set()


def _migrate_db(conn: sqlite3.Connection) -> None:
    """Safely migrate legacy v0.1 databases without dropping user rows."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "status" not in cols:
        conn.execute(
            "ALTER TABLE tasks ADD COLUMN status TEXT NOT NULL DEFAULT 'not_started'"
        )
        conn.execute(
            "UPDATE tasks SET status = CASE WHEN done = 1 THEN 'completed' ELSE 'not_started' END"
        )
    if "progress_note" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN progress_note TEXT NOT NULL DEFAULT ''")
    if "start_date" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN start_date TEXT")
    if "timeline" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN timeline TEXT NOT NULL DEFAULT ''")
    if "frequency" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN frequency TEXT NOT NULL DEFAULT ''")
    if "parent_task_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER")
    if "parent_item_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN parent_item_id INTEGER")
    if "is_recurring" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN is_recurring INTEGER NOT NULL DEFAULT 0")
    if "target_days" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN target_days INTEGER NOT NULL DEFAULT 0")
    if "daily_target_minutes" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN daily_target_minutes INTEGER NOT NULL DEFAULT 0")
    if "progress_mode" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN progress_mode TEXT NOT NULL DEFAULT 'auto'")
    if "progress_percent" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN progress_percent INTEGER NOT NULL DEFAULT 0")
    if "note_id" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN note_id INTEGER")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_note ON tasks(note_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_daily_logs_task_date ON task_daily_logs(task_id, date DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_progress_logs_task ON task_progress_logs(task_id, id DESC);")

    note_cols = {r["name"] for r in conn.execute("PRAGMA table_info(notes)").fetchall()}
    if "folder" not in note_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN folder TEXT NOT NULL DEFAULT ''")
    if "summary" not in note_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder COLLATE NOCASE);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_sessions_date ON study_sessions(date DESC, id DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_study_sessions_subject ON study_sessions(subject COLLATE NOCASE);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist_items(status, updated_at DESC);")


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a configured SQLite connection with automatic commit/rollback."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    resolved = str(db_path.resolve())
    existed = db_path.exists()
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    if not existed or resolved not in _initialized_db_paths:
        conn.executescript(_SCHEMA_SQL)
        _migrate_db(conn)
        _initialized_db_paths.add(resolved)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize all SQLite tables and indexes if they do not exist."""
    with get_conn() as conn:
        conn.executescript(_SCHEMA_SQL)
        _migrate_db(conn)
        _initialized_db_paths.add(str(get_db_path().resolve()))


def _parse_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _clean_tags(tags: list[str] | str | None) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        items = [t.strip().lstrip("#") for t in tags.split(",")]
    else:
        items = [str(t).strip().lstrip("#") for t in tags]
    seen: set[str] = set()
    cleaned: list[str] = []
    for item in items:
        if item and item.lower() not in seen:
            seen.add(item.lower())
            cleaned.append(item)
    return cleaned


_CHECKLIST_LINE_RE = re.compile(r"^(\s*[-*]\s*)\[([ xX])\](\s+)(.*)$")


def _compute_note_checklist_stats(content: str) -> dict[str, Any]:
    """Parse Markdown `- [ ]` / `- [x]` checklist lines inside a note."""
    items: list[dict[str, Any]] = []
    done_count = 0
    for line_no, line in enumerate(content.splitlines()):
        m = _CHECKLIST_LINE_RE.match(line)
        if not m:
            continue
        is_done = m.group(2).lower() == "x"
        if is_done:
            done_count += 1
        items.append(
            {
                "index": len(items),
                "line_no": line_no,
                "text": m.group(4).strip(),
                "done": is_done,
            }
        )
    total = len(items)
    if total == 0:
        status = None
    elif done_count == 0:
        status = "not_started"
    elif done_count == total:
        status = "completed"
    else:
        status = "partially_completed"
    return {
        "items": items,
        "total": total,
        "done": done_count,
        "status": status,
    }


def _row_to_task_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "text": row["text"],
        "done": bool(row["done"]),
        "position": row["position"],
    }


def _resolve_effective_progress_mode(
    stored_mode: str,
    is_rec: bool,
    target_days: int,
    items_count: int,
    progress_percent: int,
) -> str:
    if stored_mode in ("percent", "days", "checklist"):
        return stored_mode
    if is_rec or target_days > 1:
        return "days"
    if items_count > 0 and progress_percent == 0:
        return "checklist"
    return "percent"


def _build_day_slots(
    start_date: str | None,
    target_days: int,
    daily_dates: list[str],
    today: str,
) -> list[dict[str, Any]]:
    done_set = set(daily_dates)
    today_dt = dt_date.fromisoformat(today)
    if target_days > 1:
        anchor_dt = today_dt
        if start_date:
            try:
                anchor_dt = dt_date.fromisoformat(start_date[:10])
            except ValueError:
                pass
        if daily_dates:
            try:
                earliest = dt_date.fromisoformat(min(daily_dates)[:10])
                if earliest < anchor_dt:
                    anchor_dt = earliest
            except ValueError:
                pass
        count = min(int(target_days), 31)
        slots: list[dict[str, Any]] = []
        for idx in range(count):
            d = anchor_dt + timedelta(days=idx)
            iso = d.isoformat()
            slots.append(
                {
                    "day_num": idx + 1,
                    "date": iso,
                    "label": d.strftime("%b %d"),
                    "done": iso in done_set,
                    "is_today": iso == today,
                }
            )
        return slots

    slots = []
    for idx in range(7):
        d = today_dt - timedelta(days=6 - idx)
        iso = d.isoformat()
        slots.append(
            {
                "day_num": idx + 1,
                "date": iso,
                "label": "Today" if iso == today else d.strftime("%b %d"),
                "done": iso in done_set,
                "is_today": iso == today,
            }
        )
    return slots


def _row_to_task(
    row: sqlite3.Row,
    checklist_items: list[dict[str, Any]] | None = None,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = checklist_items if checklist_items is not None else []
    done_count = sum(1 for item in items if item["done"])
    row_keys = row.keys()
    status = row["status"] if "status" in row_keys and row["status"] in TASK_STATUSES else (
        "completed" if row["done"] else "not_started"
    )
    progress_note = row["progress_note"] if "progress_note" in row_keys else ""
    start_date = row["start_date"] if "start_date" in row_keys else None
    timeline = row["timeline"] if "timeline" in row_keys else ""
    frequency = row["frequency"] if "frequency" in row_keys else ""
    parent_task_id = row["parent_task_id"] if "parent_task_id" in row_keys else None
    parent_item_id = row["parent_item_id"] if "parent_item_id" in row_keys else None
    raw_rec = bool(row["is_recurring"]) if "is_recurring" in row_keys else False
    raw_days = int(row["target_days"] or 0) if "target_days" in row_keys else 0
    raw_daily_mins = int(row["daily_target_minutes"] or 0) if "daily_target_minutes" in row_keys else 0
    raw_mode = (
        row["progress_mode"]
        if "progress_mode" in row_keys and row["progress_mode"] in TASK_PROGRESS_MODES
        else "auto"
    )
    raw_pct = max(0, min(100, int(row["progress_percent"] or 0))) if "progress_percent" in row_keys else 0
    note_id = row["note_id"] if "note_id" in row_keys else None

    inf_rec, inf_days, inf_mins = infer_recurrence_fields(
        row["title"],
        timeline=timeline or "",
        frequency=frequency or "",
        start_date=start_date,
        due_date=row["due_date"],
    )
    is_rec = raw_rec or inf_rec
    target_days = raw_days if raw_days > 0 else (inf_days if is_rec else 0)
    daily_mins = raw_daily_mins if raw_daily_mins > 0 else (inf_mins if is_rec else 0)
    effective_mode = _resolve_effective_progress_mode(raw_mode, is_rec, target_days, len(items), raw_pct)

    ext = extra or {}
    today = today_str()
    due = row["due_date"]
    tl_low = (timeline or "").lower()
    is_today_task = False
    if row["list_type"] == "daily":
        if parent_task_id is not None:
            is_today_task = True
        elif due == today or "today" in tl_low:
            is_today_task = True
        elif not is_rec and target_days <= 1 and not (due and due > today) and "next " not in tl_low and len(items) < 2 and raw_mode != "percent" and raw_pct == 0:
            is_today_task = True

    daily_dates = ext.get("daily_dates", [])
    days_completed = len(daily_dates)
    done_today = today in daily_dates
    day_slots = (
        _build_day_slots(start_date, target_days, daily_dates, today)
        if effective_mode == "days"
        else []
    )
    eff_pct = 100 if (status == "completed" and raw_pct == 0) else raw_pct

    return {
        "id": row["id"],
        "title": row["title"],
        "list_type": row["list_type"],
        "category": row["category"],
        "priority": row["priority"],
        "start_date": start_date,
        "due_date": due,
        "timeline": timeline,
        "frequency": frequency,
        "parent_task_id": parent_task_id,
        "parent_task_title": ext.get("parent_task_title", ""),
        "parent_item_id": parent_item_id,
        "parent_item_text": ext.get("parent_item_text", ""),
        "is_recurring": is_rec,
        "target_days": target_days,
        "daily_target_minutes": daily_mins,
        "progress_mode": raw_mode,
        "effective_progress_mode": effective_mode,
        "progress_percent": eff_pct,
        "progress_logs": ext.get("progress_logs", []),
        "day_slots": day_slots,
        "note_id": note_id,
        "note_title": ext.get("note_title", ""),
        "note_preview": ext.get("note_preview", ""),
        "daily_dates": daily_dates,
        "days_completed": days_completed,
        "done_today": done_today,
        "daily_streak": ext.get("daily_streak", 0),
        "child_count": ext.get("child_count", 0),
        "child_done_today_count": ext.get("child_done_today_count", 0),
        "study_seconds_today": ext.get("study_seconds_today", 0),
        "study_seconds_total": ext.get("study_seconds_total", 0),
        "is_today_task": is_today_task,
        "done": bool(row["done"]),
        "status": status,
        "progress_note": progress_note,
        "checklist_items": items,
        "checklist_total": len(items),
        "checklist_done": done_count,
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
    }


def _row_to_note(row: sqlite3.Row) -> dict[str, Any]:
    content = row["content"]
    row_keys = row.keys()
    folder = row["folder"] if "folder" in row_keys else ""
    summary = row["summary"] if "summary" in row_keys else ""
    return {
        "id": row["id"],
        "title": row["title"],
        "content": content,
        "folder": folder or "",
        "summary": summary or "",
        "word_count": len((content or "").split()),
        "tags": _parse_json_list(row["tags"]),
        "pinned": bool(row["pinned"]),
        "checklist": _compute_note_checklist_stats(content),
        "source_path": row["source_path"],
        "file_mtime": row["file_mtime"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_art(row: sqlite3.Row) -> dict[str, Any]:
    files = _parse_json_list(row["image_files"])
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "notes": row["notes"],
        "image_files": files,
        "image_urls": [f"/api/media/{fname}" for fname in files],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_game(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "platform": row["platform"],
        "rating": row["rating"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_gym_log(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "date": row["date"],
        "exercise": row["exercise"],
        "sets": row["sets"],
        "reps": row["reps"],
        "weight": row["weight"],
        "unit": row["unit"],
        "notes": row["notes"],
        "created_at": row["created_at"],
    }


# ---------------------------------------------------------------------------
# Tasks & Want to Do
# ---------------------------------------------------------------------------

_RECURRING_TEXT_RE = re.compile(
    r"\b(daily|every\s*day|each\s*day|a\s*day|per\s*day)\b|/\s*day\b",
    flags=re.IGNORECASE,
)
_DAYS_TARGET_RE = re.compile(
    r"(?:next\s+|for\s+|over\s+)?(\d{1,3})\s*(?:days?|d)\b",
    flags=re.IGNORECASE,
)
_TASK_STOPWORDS = {
    "the", "a", "an", "for", "of", "to", "on", "in", "at", "by", "with", "and",
    "or", "my", "today", "todays", "today's", "daily", "task", "tasks", "todo",
    "list", "next", "days", "day", "week", "weeks", "hour", "hours", "hr", "hrs",
    "min", "mins", "minute", "minutes", "timer", "practice", "study", "studying",
    "watch", "watching", "read", "reading", "complete", "completed", "finish",
    "finished", "do", "did", "done", "make", "create", "problems", "problem",
    "session", "sessions", "portion", "part", "item", "step", "steps", "work",
    "some", "more", "start", "opening", "open", "bigger", "parent", "plan",
}
_GENERIC_TOPIC_TOKENS = {
    "project", "projects", "course", "courses", "lecture", "lectures", "survey",
    "surveys", "video", "videos", "notes", "note", "paper", "papers", "review",
    "research", "coding", "learning", "goal", "goals", "track", "test", "tests",
}


def _extract_minutes_sum(text: str) -> int:
    raw = (text or "").strip().lower()
    if not raw:
        return 0
    total = 0.0
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr)\b", raw):
        total += float(m.group(1)) * 60.0
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(minutes?|mins?|min)\b", raw):
        total += float(m.group(1))
    # Shorthand like 1h or 30m when not already followed by hours/minutes
    cleaned = re.sub(r"\d+(?:\.\d+)?\s*(?:hours?|hrs?|hr|minutes?|mins?|min)\b", " ", raw)
    for m in re.finditer(r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*([hm])\b", cleaned):
        val = float(m.group(1))
        total += val * 60.0 if m.group(2) == "h" else val
    return int(round(total)) if total > 0 else 0


def infer_recurrence_fields(
    title: str,
    timeline: str = "",
    frequency: str = "",
    start_date: str | None = None,
    due_date: str | None = None,
) -> tuple[bool, int, int]:
    """Infer (is_recurring, target_days, daily_target_minutes) from task text fields."""
    combined = f"{title} {timeline} {frequency}".strip()
    is_rec = bool(_RECURRING_TEXT_RE.search(combined))

    target_days = 0
    m_days = _DAYS_TARGET_RE.search(f"{timeline} {frequency} {title}")
    if m_days:
        val = int(m_days.group(1))
        if 1 < val <= 365:
            target_days = val
    elif is_rec and start_date and due_date:
        try:
            diff = (dt_date.fromisoformat(due_date) - dt_date.fromisoformat(start_date)).days
            if 1 < diff <= 365:
                target_days = diff
        except ValueError:
            pass

    daily_mins = _extract_minutes_sum(frequency)
    if daily_mins == 0 and is_rec:
        daily_mins = _extract_minutes_sum(title)
    return is_rec, target_days, daily_mins


def _norm_token(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _tokenize_task_text(text: str) -> set[str]:
    raw_tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    out: set[str] = set()
    for tok in raw_tokens:
        if len(tok) < 2:
            continue
        if re.fullmatch(r"\d+[a-z]*", tok):
            continue
        if tok in _TASK_STOPWORDS:
            continue
        norm = _norm_token(tok)
        if norm in _TASK_STOPWORDS or len(norm) < 2:
            continue
        out.add(norm)
    return out


def _analyze_parent_candidates_conn(
    conn: sqlite3.Connection,
    title: str,
    *,
    timeline: str = "",
    frequency: str = "",
    due_date: str | None = None,
    exclude_task_id: int | None = None,
) -> dict[str, Any]:
    """Classify parent goal matches into auto-link vs ambiguous candidate prompts vs none."""
    child_tokens = _tokenize_task_text(title)
    if not child_tokens:
        return {"parent_task_id": None, "parent_item_id": None, "possible_parents": []}

    today = today_str()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE list_type = 'daily' AND done = 0 AND parent_task_id IS NULL ORDER BY id DESC"
    ).fetchall()
    if not rows:
        return {"parent_task_id": None, "parent_item_id": None, "possible_parents": []}

    items_map = _fetch_task_items_map(conn, [r["id"] for r in rows])
    title_norm = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()

    scored: list[dict[str, Any]] = []
    for r in rows:
        pid = int(r["id"])
        if exclude_task_id is not None and pid == exclude_task_id:
            continue
        r_keys = r.keys()
        p_tl = r["timeline"] if "timeline" in r_keys else ""
        p_freq = r["frequency"] if "frequency" in r_keys else ""
        p_rec, p_days, _ = infer_recurrence_fields(
            r["title"], timeline=p_tl, frequency=p_freq, start_date=r["start_date"], due_date=r["due_date"]
        )
        p_rec_flag = p_rec or (bool(r["is_recurring"]) if "is_recurring" in r_keys else False)
        p_days_val = max(int(r["target_days"] or 0) if "target_days" in r_keys else 0, p_days)
        c_items = items_map.get(pid, [])

        is_goal_candidate = (
            p_rec_flag
            or p_days_val > 1
            or bool(r["due_date"] and r["due_date"] > today)
            or "next " in (p_tl or "").lower()
            or len(c_items) >= 2
        )
        if not is_goal_candidate:
            continue

        parent_tokens = _tokenize_task_text(f"{r['title']} {r['category']}")
        overlap = child_tokens & parent_tokens

        # Check specific checklist item match on parent
        best_item_id: int | None = None
        best_item_score = 0.0
        for sub in c_items:
            sub_norm = re.sub(r"[^a-z0-9]+", " ", sub["text"].lower()).strip()
            sub_toks = _tokenize_task_text(sub["text"])
            sub_overlap = child_tokens & sub_toks
            specific_sub = [tok for tok in sub_overlap if tok not in _GENERIC_TOPIC_TOKENS]
            item_bonus = 0.05 if not sub["done"] else 0.0
            if len(sub_norm.split()) >= 2 and re.search(rf"\b{re.escape(sub_norm)}\b", title_norm) and overlap:
                cand_score = 0.94 + item_bonus
                if cand_score > best_item_score:
                    best_item_score = cand_score
                    best_item_id = int(sub["id"])
            elif sub_overlap and overlap and (
                specific_sub or (len(sub_toks) >= 2 and len(sub_overlap) == len(sub_toks))
            ):
                cand_score = 0.88 + item_bonus + 0.01 * min(3, len(sub_overlap))
                if cand_score > best_item_score:
                    best_item_score = cand_score
                    best_item_id = int(sub["id"])

        containment = len(overlap) / max(1, min(len(child_tokens), len(parent_tokens)))
        specific_overlap = [
            tok for tok in overlap if len(tok) >= 5 and tok not in _GENERIC_TOPIC_TOKENS
        ]
        strong_specific = [
            tok for tok in overlap if len(tok) >= 6 and tok not in _GENERIC_TOPIC_TOKENS
        ]

        title_score = 0.0
        if len(overlap) >= 2 and containment >= 0.5:
            title_score = 0.84 + min(0.1, 0.02 * len(overlap))
        elif (strong_specific and containment >= 0.30) or (specific_overlap and containment >= 0.45):
            title_score = 0.82
        elif specific_overlap and containment >= 0.20:
            title_score = 0.58
        elif len(overlap) >= 1:
            title_score = 0.46

        final_score = max(best_item_score, title_score)
        if final_score >= 0.35:
            matched_terms = sorted(overlap)
            scored.append(
                {
                    "id": pid,
                    "title": r["title"],
                    "timeline": p_tl,
                    "is_recurring": bool(p_rec_flag),
                    "item_id": best_item_id,
                    "score": round(final_score, 3),
                    "reason": (
                        f"matches checklist step & keywords ({', '.join(matched_terms)})"
                        if best_item_id
                        else f"shares topic ({', '.join(matched_terms)})"
                    ),
                }
            )

    if not scored:
        return {"parent_task_id": None, "parent_item_id": None, "possible_parents": []}

    scored.sort(key=lambda s: (-s["score"], s["id"]))
    top = scored[0]
    second_score = scored[1]["score"] if len(scored) > 1 else 0.0
    if top["score"] >= 0.80 and (len(scored) == 1 or (top["score"] - second_score) >= 0.15):
        return {
            "parent_task_id": int(top["id"]),
            "parent_item_id": top["item_id"],
            "possible_parents": [],
        }

    return {
        "parent_task_id": None,
        "parent_item_id": None,
        "possible_parents": [
            {
                "id": c["id"],
                "title": c["title"],
                "timeline": c["timeline"],
                "is_recurring": c["is_recurring"],
                "suggested_item_id": c["item_id"],
                "reason": c["reason"],
            }
            for c in scored[:3]
        ],
    }


def _recompute_recurring_parent_status_conn(conn: sqlite3.Connection, task_id: int) -> None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return
    r_keys = row.keys()
    inf_rec, inf_days, _ = infer_recurrence_fields(
        row["title"],
        timeline=row["timeline"] if "timeline" in r_keys else "",
        frequency=row["frequency"] if "frequency" in r_keys else "",
        start_date=row["start_date"] if "start_date" in r_keys else None,
        due_date=row["due_date"],
    )
    is_rec = (bool(row["is_recurring"]) if "is_recurring" in r_keys else False) or inf_rec
    stored_days = int(row["target_days"] or 0) if "target_days" in r_keys else 0
    target_days = stored_days if stored_days > 0 else (inf_days if is_rec else 0)
    if not is_rec and target_days <= 1:
        return

    days_count = int(
        conn.execute(
            "SELECT COUNT(DISTINCT date) FROM task_daily_logs WHERE task_id = ?", (task_id,)
        ).fetchone()[0]
    )
    sub_done_any = bool(
        conn.execute(
            "SELECT 1 FROM task_items WHERE task_id = ? AND done = 1 LIMIT 1", (task_id,)
        ).fetchone()
    )

    if target_days > 0:
        if days_count >= target_days:
            status, done_int, comp = "completed", 1, row["completed_at"] or now_iso()
        elif days_count > 0 or sub_done_any:
            status, done_int, comp = "partially_completed", 0, None
        else:
            status, done_int, comp = "not_started", 0, None
    else:
        if days_count > 0 or sub_done_any:
            status, done_int, comp = "partially_completed", 0, None
        else:
            status, done_int, comp = "not_started", 0, None

    conn.execute(
        "UPDATE tasks SET status = ?, done = ?, completed_at = ? WHERE id = ?",
        (status, done_int, comp, task_id),
    )


def _sync_parent_progress_conn(
    conn: sqlite3.Connection,
    child_row: sqlite3.Row,
    *,
    became_done: bool = False,
    became_undone: bool = False,
    day_date: str | None = None,
) -> list[dict[str, Any]]:
    r_keys = child_row.keys()
    parent_id = child_row["parent_task_id"] if "parent_task_id" in r_keys else None
    if parent_id is None:
        return []
    parent = conn.execute("SELECT * FROM tasks WHERE id = ?", (int(parent_id),)).fetchone()
    if not parent:
        return []

    target_date = day_date or (
        child_row["completed_at"][:10] if child_row["completed_at"] else today_str()
    )
    p_item_id = child_row["parent_item_id"] if "parent_item_id" in r_keys else None
    p_keys = parent.keys()
    inf_rec, inf_days, _ = infer_recurrence_fields(
        parent["title"],
        timeline=parent["timeline"] if "timeline" in p_keys else "",
        frequency=parent["frequency"] if "frequency" in p_keys else "",
        start_date=parent["start_date"] if "start_date" in p_keys else None,
        due_date=parent["due_date"],
    )
    parent_rec = (bool(parent["is_recurring"]) if "is_recurring" in p_keys else False) or inf_rec
    parent_days = max(
        int(parent["target_days"] or 0) if "target_days" in p_keys else 0,
        inf_days if parent_rec else 0,
    )

    checked_item_text = ""
    if became_done:
        if p_item_id is not None:
            it_row = conn.execute(
                "SELECT id, text FROM task_items WHERE id = ? AND task_id = ?",
                (int(p_item_id), int(parent_id)),
            ).fetchone()
            if it_row:
                conn.execute("UPDATE task_items SET done = 1 WHERE id = ?", (it_row["id"],))
                checked_item_text = it_row["text"]
                _recompute_task_rollup(conn, int(parent_id))

        if parent_rec or parent_days > 1 or p_item_id is None:
            conn.execute(
                """
                INSERT OR IGNORE INTO task_daily_logs (task_id, date, note, completed_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(parent_id), target_date, child_row["title"], now_iso()),
            )
            _recompute_recurring_parent_status_conn(conn, int(parent_id))

    elif became_undone:
        if p_item_id is not None:
            other_done = conn.execute(
                "SELECT 1 FROM tasks WHERE parent_task_id = ? AND parent_item_id = ? AND done = 1 AND id != ? LIMIT 1",
                (int(parent_id), int(p_item_id), int(child_row["id"])),
            ).fetchone()
            if not other_done:
                conn.execute(
                    "UPDATE task_items SET done = 0 WHERE id = ? AND task_id = ?",
                    (int(p_item_id), int(parent_id)),
                )
                _recompute_task_rollup(conn, int(parent_id))

        if parent_rec or parent_days > 1 or p_item_id is None:
            other_today = conn.execute(
                """
                SELECT 1 FROM tasks
                WHERE parent_task_id = ? AND done = 1 AND id != ?
                  AND substr(COALESCE(completed_at, ''), 1, 10) = ?
                LIMIT 1
                """,
                (int(parent_id), int(child_row["id"]), target_date),
            ).fetchone()
            if not other_today:
                conn.execute(
                    "DELETE FROM task_daily_logs WHERE task_id = ? AND date = ? AND note = ?",
                    (int(parent_id), target_date, child_row["title"]),
                )
                _recompute_recurring_parent_status_conn(conn, int(parent_id))

    refreshed_parent = conn.execute("SELECT * FROM tasks WHERE id = ?", (int(parent_id),)).fetchone()
    days_cnt = int(
        conn.execute(
            "SELECT COUNT(DISTINCT date) FROM task_daily_logs WHERE task_id = ?", (int(parent_id),)
        ).fetchone()[0]
    )
    done_today = bool(
        conn.execute(
            "SELECT 1 FROM task_daily_logs WHERE task_id = ? AND date = ? LIMIT 1",
            (int(parent_id), today_str()),
        ).fetchone()
    )
    return [
        {
            "parent_task_id": int(parent_id),
            "parent_title": refreshed_parent["title"],
            "status": refreshed_parent["status"],
            "done_today": done_today,
            "days_completed": days_cnt,
            "target_days": parent_days,
            "checked_item": checked_item_text,
        }
    ]


def _fetch_task_items_map(
    conn: sqlite3.Connection, task_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    rows = conn.execute(
        f"SELECT * FROM task_items WHERE task_id IN ({placeholders}) ORDER BY position ASC, id ASC",
        task_ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = {tid: [] for tid in task_ids}
    for r in rows:
        grouped.setdefault(r["task_id"], []).append(_row_to_task_item(r))
    return grouped


def _enrich_tasks_conn(
    conn: sqlite3.Connection, rows: list[sqlite3.Row]
) -> list[dict[str, Any]]:
    if not rows:
        return []
    task_ids = [int(r["id"]) for r in rows]
    placeholders = ",".join("?" for _ in task_ids)
    items_map = _fetch_task_items_map(conn, task_ids)
    today = today_str()

    # Daily logs for listed tasks
    daily_dates_map: dict[int, list[str]] = {tid: [] for tid in task_ids}
    try:
        dl_rows = conn.execute(
            f"SELECT task_id, date FROM task_daily_logs WHERE task_id IN ({placeholders}) ORDER BY date DESC",
            task_ids,
        ).fetchall()
        for r in dl_rows:
            daily_dates_map.setdefault(int(r["task_id"]), []).append(r["date"])
    except sqlite3.OperationalError:
        pass

    # Parent titles & matched item labels
    parent_ids = sorted(
        {int(r["parent_task_id"]) for r in rows if "parent_task_id" in r.keys() and r["parent_task_id"] is not None}
    )
    parent_titles: dict[int, str] = {}
    if parent_ids:
        p_ph = ",".join("?" for _ in parent_ids)
        for pr in conn.execute(f"SELECT id, title FROM tasks WHERE id IN ({p_ph})", parent_ids).fetchall():
            parent_titles[int(pr["id"])] = pr["title"]

    p_item_ids = sorted(
        {int(r["parent_item_id"]) for r in rows if "parent_item_id" in r.keys() and r["parent_item_id"] is not None}
    )
    p_item_texts: dict[int, str] = {}
    if p_item_ids:
        pi_ph = ",".join("?" for _ in p_item_ids)
        for pir in conn.execute(f"SELECT id, text FROM task_items WHERE id IN ({pi_ph})", p_item_ids).fetchall():
            p_item_texts[int(pir["id"])] = pir["text"]

    # Child counts & map child -> parent for study rollup
    child_stats: dict[int, tuple[int, int]] = {}
    child_to_parent: dict[int, int] = {}
    try:
        ch_rows = conn.execute(
            f"SELECT id, parent_task_id, done, completed_at FROM tasks WHERE parent_task_id IN ({placeholders})",
            task_ids,
        ).fetchall()
        for ch in ch_rows:
            pid = int(ch["parent_task_id"])
            chid = int(ch["id"])
            child_to_parent[chid] = pid
            tot, done_t = child_stats.get(pid, (0, 0))
            is_dt = 1 if (ch["done"] and (ch["completed_at"] or "").startswith(today)) else 0
            child_stats[pid] = (tot + 1, done_t + is_dt)
    except sqlite3.OperationalError:
        pass

    # Study session aggregation + active focus timer per task & rolled into parent
    all_study_ids = sorted(set(task_ids) | set(child_to_parent.keys()))
    study_today_map: dict[int, int] = {}
    study_total_map: dict[int, int] = {}
    if all_study_ids:
        s_ph = ",".join("?" for _ in all_study_ids)
        try:
            st_rows = conn.execute(
                f"""
                SELECT task_id,
                       SUM(duration_seconds) AS total_s,
                       SUM(CASE WHEN date = ? THEN duration_seconds ELSE 0 END) AS today_s
                FROM study_sessions
                WHERE task_id IN ({s_ph})
                GROUP BY task_id
                """,
                [today, *all_study_ids],
            ).fetchall()
            for sr in st_rows:
                tid = int(sr["task_id"])
                study_total_map[tid] = int(sr["total_s"] or 0)
                study_today_map[tid] = int(sr["today_s"] or 0)
        except sqlite3.OperationalError:
            pass

        try:
            t_row = conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()
            if t_row and t_row["active"] and t_row["phase"] == "focus" and t_row["task_id"] is not None:
                live_tid = int(t_row["task_id"])
                if live_tid in all_study_ids:
                    live_secs = _format_timer_row(conn, t_row)["elapsed_seconds"]
                    study_today_map[live_tid] = study_today_map.get(live_tid, 0) + live_secs
                    study_total_map[live_tid] = study_total_map.get(live_tid, 0) + live_secs
        except sqlite3.OperationalError:
            pass

        for chid, pid in child_to_parent.items():
            if chid == pid:
                continue
            study_today_map[pid] = study_today_map.get(pid, 0) + study_today_map.get(chid, 0)
            study_total_map[pid] = study_total_map.get(pid, 0) + study_total_map.get(chid, 0)

    # Progress logs & linked note previews
    progress_logs_map: dict[int, list[dict[str, Any]]] = {tid: [] for tid in task_ids}
    try:
        pl_rows = conn.execute(
            f"""
            SELECT id, task_id, progress_percent, note, created_at
            FROM task_progress_logs
            WHERE task_id IN ({placeholders})
            ORDER BY id DESC
            """,
            task_ids,
        ).fetchall()
        for r in pl_rows:
            lst = progress_logs_map.setdefault(int(r["task_id"]), [])
            if len(lst) < 12:
                lst.append(
                    {
                        "id": int(r["id"]),
                        "progress_percent": int(r["progress_percent"]),
                        "note": r["note"],
                        "created_at": r["created_at"],
                    }
                )
    except sqlite3.OperationalError:
        pass

    note_ids = sorted(
        {int(r["note_id"]) for r in rows if "note_id" in r.keys() and r["note_id"] is not None}
    )
    note_meta_map: dict[int, tuple[str, str]] = {}
    if note_ids:
        n_ph = ",".join("?" for _ in note_ids)
        for nr in conn.execute(
            f"SELECT id, title, summary, content FROM notes WHERE id IN ({n_ph})", note_ids
        ).fetchall():
            raw_prev = (nr["summary"] or nr["content"] or "").strip()
            clean_prev = re.sub(r"[#*_>`\[\]]+", " ", raw_prev)
            clean_prev = re.sub(r"\s+", " ", clean_prev).strip()[:140]
            note_meta_map[int(nr["id"])] = (nr["title"] or "", clean_prev)

    out: list[dict[str, Any]] = []
    for r in rows:
        tid = int(r["id"])
        rk = r.keys()
        pid = int(r["parent_task_id"]) if ("parent_task_id" in rk and r["parent_task_id"] is not None) else None
        piid = int(r["parent_item_id"]) if ("parent_item_id" in rk and r["parent_item_id"] is not None) else None
        nid = int(r["note_id"]) if ("note_id" in rk and r["note_id"] is not None) else None
        n_title, n_prev = note_meta_map.get(nid, ("", "")) if nid is not None else ("", "")
        d_dates = daily_dates_map.get(tid, [])
        ch_tot, ch_done_t = child_stats.get(tid, (0, 0))
        extra = {
            "daily_dates": d_dates,
            "daily_streak": _calculate_streak(d_dates, today),
            "parent_task_title": parent_titles.get(pid, "") if pid is not None else "",
            "parent_item_text": p_item_texts.get(piid, "") if piid is not None else "",
            "progress_logs": progress_logs_map.get(tid, []),
            "note_title": n_title,
            "note_preview": n_prev,
            "child_count": ch_tot,
            "child_done_today_count": ch_done_t,
            "study_seconds_today": study_today_map.get(tid, 0),
            "study_seconds_total": study_total_map.get(tid, 0),
        }
        out.append(_row_to_task(r, items_map.get(tid, []), extra=extra))
    return out


def _recompute_task_rollup(conn: sqlite3.Connection, task_id: int) -> None:
    rows = conn.execute(
        "SELECT done FROM task_items WHERE task_id = ?", (task_id,)
    ).fetchall()
    if not rows:
        return
    parent_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if parent_row:
        rk = parent_row.keys()
        inf_rec, inf_days, _ = infer_recurrence_fields(
            parent_row["title"],
            timeline=parent_row["timeline"] if "timeline" in rk else "",
            frequency=parent_row["frequency"] if "frequency" in rk else "",
            start_date=parent_row["start_date"] if "start_date" in rk else None,
            due_date=parent_row["due_date"],
        )
        is_rec = (bool(parent_row["is_recurring"]) if "is_recurring" in rk else False) or inf_rec
        if is_rec:
            total = len(rows)
            done_count = sum(1 for r in rows if r["done"])
            if done_count == total:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO task_daily_logs (task_id, date, note, completed_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (task_id, today_str(), "Completed daily checklist", now_iso()),
                )
            _recompute_recurring_parent_status_conn(conn, task_id)
            return

    total = len(rows)
    done_count = sum(1 for r in rows if r["done"])
    if done_count == 0:
        status = "not_started"
        done_int = 0
        completed_at = None
    elif done_count == total:
        status = "completed"
        done_int = 1
        completed_at = now_iso()
    else:
        status = "partially_completed"
        done_int = 0
        completed_at = None
    conn.execute(
        "UPDATE tasks SET status = ?, done = ?, completed_at = ? WHERE id = ?",
        (status, done_int, completed_at, task_id),
    )


def get_task(task_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return _enrich_tasks_conn(conn, [row])[0]


def list_tasks(
    list_type: str | None = None,
    include_done: bool = True,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if list_type in TASK_LIST_TYPES:
        clauses.append("list_type = ?")
        params.append(list_type)
    if status in TASK_STATUSES:
        clauses.append("status = ?")
        params.append(status)
    elif not include_done:
        clauses.append("done = 0")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT * FROM tasks {where_sql} "
        "ORDER BY done ASC, "
        "CASE status WHEN 'partially_completed' THEN 0 WHEN 'not_started' THEN 1 ELSE 2 END ASC, "
        "CASE priority WHEN 'high' THEN 0 ELSE 1 END ASC, id DESC"
    )
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return _enrich_tasks_conn(conn, rows)


def create_task(
    title: str,
    list_type: str = "daily",
    category: str = "",
    priority: str = "normal",
    due_date: str | None = None,
    status: str = "not_started",
    progress_note: str = "",
    checklist_items: list[str] | None = None,
    start_date: str | None = None,
    timeline: str = "",
    frequency: str = "",
    parent_task_id: int | None = None,
    parent_item_id: int | None = None,
    is_recurring: bool | None = None,
    target_days: int | None = None,
    daily_target_minutes: int | None = None,
    auto_link_parent: bool = True,
    progress_mode: str | None = None,
    progress_percent: int | None = None,
    note_id: int | None = None,
) -> dict[str, Any]:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Task title cannot be empty.")
    lt = list_type if list_type in TASK_LIST_TYPES else "daily"
    pr = priority if priority in TASK_PRIORITIES else "normal"
    st = status if status in TASK_STATUSES else "not_started"
    p_mode = progress_mode if progress_mode in TASK_PROGRESS_MODES else "auto"
    pct = max(0, min(100, int(progress_percent))) if progress_percent is not None else (100 if st == "completed" else 0)
    if progress_percent is not None and status == "not_started":
        if pct >= 100:
            st = "completed"
        elif pct > 0:
            st = "partially_completed"
    done_int = 1 if st == "completed" else 0
    completed_at = now_iso() if st == "completed" else None
    start = start_date.strip() if isinstance(start_date, str) and start_date.strip() else None
    due = due_date.strip() if isinstance(due_date, str) and due_date.strip() else None
    tl = timeline.strip()
    freq = frequency.strip()

    inf_rec, inf_days, inf_mins = infer_recurrence_fields(
        clean_title, timeline=tl, frequency=freq, start_date=start, due_date=due
    )
    eff_rec = bool(is_recurring) if is_recurring is not None else inf_rec
    eff_days = int(target_days) if target_days is not None and int(target_days) > 0 else inf_days
    eff_mins = (
        int(daily_target_minutes)
        if daily_target_minutes is not None and int(daily_target_minutes) > 0
        else inf_mins
    )
    if eff_rec and not start:
        start = today_str()

    ts = now_iso()
    today = today_str()
    possible_parents: list[dict[str, Any]] = []
    parent_updates: list[dict[str, Any]] = []

    with get_conn() as conn:
        _, resolved_note_id = _validate_fk_ids(conn, None, note_id)
        resolved_pid: int | None = None
        resolved_piid: int | None = None
        if parent_task_id is not None and int(parent_task_id) > 0:
            if conn.execute("SELECT id FROM tasks WHERE id = ?", (int(parent_task_id),)).fetchone():
                resolved_pid = int(parent_task_id)
                if parent_item_id is not None and int(parent_item_id) > 0:
                    if conn.execute(
                        "SELECT id FROM task_items WHERE id = ? AND task_id = ?",
                        (int(parent_item_id), resolved_pid),
                    ).fetchone():
                        resolved_piid = int(parent_item_id)
        elif (
            lt == "daily"
            and auto_link_parent
            and p_mode != "percent"
            and not (eff_rec or eff_days > 1 or (due and due > today) or "next " in tl.lower())
        ):
            match_res = _analyze_parent_candidates_conn(
                conn,
                clean_title,
                timeline=tl,
                frequency=freq,
                due_date=due,
            )
            resolved_pid = match_res["parent_task_id"]
            resolved_piid = match_res["parent_item_id"]
            possible_parents = match_res["possible_parents"]

        cur = conn.execute(
            """
            INSERT INTO tasks (
                title, list_type, category, priority, start_date, due_date, timeline, frequency,
                parent_task_id, parent_item_id, is_recurring, target_days, daily_target_minutes,
                progress_mode, progress_percent, note_id,
                done, status, progress_note, created_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_title,
                lt,
                category.strip(),
                pr,
                start,
                due,
                tl,
                freq,
                resolved_pid,
                resolved_piid,
                int(eff_rec),
                eff_days,
                eff_mins,
                p_mode,
                pct,
                resolved_note_id,
                done_int,
                st,
                progress_note.strip(),
                ts,
                completed_at,
            ),
        )
        task_id = int(cur.lastrowid)
        if pct > 0 or (progress_note.strip() and p_mode == "percent"):
            conn.execute(
                """
                INSERT INTO task_progress_logs (task_id, progress_percent, note, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, pct, progress_note.strip(), ts),
            )
        if checklist_items:
            for pos, text in enumerate(checklist_items):
                clean_item = str(text).strip()
                if clean_item:
                    conn.execute(
                        "INSERT INTO task_items (task_id, text, done, position) VALUES (?, ?, 0, ?)",
                        (task_id, clean_item, pos),
                    )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if done_int and resolved_pid is not None:
            parent_updates = _sync_parent_progress_conn(conn, row, became_done=True, day_date=today)
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        enriched = _enrich_tasks_conn(conn, [row])[0]
        enriched["possible_parents"] = possible_parents
        if parent_updates:
            enriched["parent_updates"] = parent_updates
        return enriched


def update_task(task_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            return None
        ex_keys = existing.keys()
        title = str(patch.get("title", existing["title"])).strip()
        if not title:
            raise ValueError("Task title cannot be empty.")
        list_type = patch.get("list_type", existing["list_type"])
        if list_type not in TASK_LIST_TYPES:
            list_type = existing["list_type"]
        category = str(patch.get("category", existing["category"])).strip()
        priority = patch.get("priority", existing["priority"])
        if priority not in TASK_PRIORITIES:
            priority = existing["priority"]
        start_date = patch.get("start_date", existing["start_date"])
        if isinstance(start_date, str):
            start_date = start_date.strip() or None
        due_date = patch.get("due_date", existing["due_date"])
        if isinstance(due_date, str):
            due_date = due_date.strip() or None
        timeline = str(patch.get("timeline", existing["timeline"])).strip()
        frequency = str(patch.get("frequency", existing["frequency"])).strip()
        progress_note = str(patch.get("progress_note", existing["progress_note"])).strip()

        raw_pid = patch["parent_task_id"] if "parent_task_id" in patch else (
            existing["parent_task_id"] if "parent_task_id" in ex_keys else None
        )
        parent_task_id: int | None = None
        if raw_pid is not None and str(raw_pid).strip() and int(raw_pid) > 0 and int(raw_pid) != task_id:
            if conn.execute("SELECT id FROM tasks WHERE id = ?", (int(raw_pid),)).fetchone():
                parent_task_id = int(raw_pid)

        raw_piid = patch["parent_item_id"] if "parent_item_id" in patch else (
            existing["parent_item_id"] if "parent_item_id" in ex_keys else None
        )
        parent_item_id: int | None = None
        if parent_task_id is not None and raw_piid is not None and str(raw_piid).strip() and int(raw_piid) > 0:
            if conn.execute(
                "SELECT id FROM task_items WHERE id = ? AND task_id = ?",
                (int(raw_piid), parent_task_id),
            ).fetchone():
                parent_item_id = int(raw_piid)

        is_recurring = bool(
            patch["is_recurring"]
            if "is_recurring" in patch and patch["is_recurring"] is not None
            else (existing["is_recurring"] if "is_recurring" in ex_keys else 0)
        )
        target_days = int(
            patch["target_days"]
            if "target_days" in patch and patch["target_days"] is not None
            else (existing["target_days"] if "target_days" in ex_keys else 0)
        )
        daily_target_minutes = int(
            patch["daily_target_minutes"]
            if "daily_target_minutes" in patch and patch["daily_target_minutes"] is not None
            else (existing["daily_target_minutes"] if "daily_target_minutes" in ex_keys else 0)
        )
        progress_mode = (
            existing["progress_mode"]
            if "progress_mode" in ex_keys and existing["progress_mode"] in TASK_PROGRESS_MODES
            else "auto"
        )
        if "progress_mode" in patch and patch["progress_mode"] is not None:
            cand_mode = str(patch["progress_mode"]).strip().lower()
            if cand_mode not in TASK_PROGRESS_MODES:
                raise ValueError(f"Unsupported progress_mode: {cand_mode}")
            progress_mode = cand_mode

        raw_note_id = patch["note_id"] if "note_id" in patch else (
            existing["note_id"] if "note_id" in ex_keys else None
        )
        _, note_id = _validate_fk_ids(conn, None, raw_note_id)

        existing_pct = max(
            0, min(100, int(existing["progress_percent"] or 0))
        ) if "progress_percent" in ex_keys else 0
        existing_status = (
            existing["status"]
            if existing["status"] in TASK_STATUSES
            else ("completed" if existing["done"] else "not_started")
        )
        was_done = existing_status == "completed" or bool(existing["done"])
        completed_at = existing["completed_at"]

        has_pct_patch = "progress_percent" in patch and patch["progress_percent"] is not None
        if has_pct_patch:
            progress_percent = max(0, min(100, int(patch["progress_percent"])))
        else:
            progress_percent = existing_pct

        if "status" in patch and patch["status"] in TASK_STATUSES:
            status = patch["status"]
            done = status == "completed"
            completed_at = (existing["completed_at"] or now_iso()) if done else None
            if not has_pct_patch:
                if done:
                    progress_percent = 100
                elif status == "not_started":
                    progress_percent = 0
        elif "done" in patch:
            done = bool(patch["done"])
            status = "completed" if done else "not_started"
            completed_at = (existing["completed_at"] or now_iso()) if done else None
            if not has_pct_patch:
                progress_percent = 100 if done else 0
        elif has_pct_patch:
            if progress_percent >= 100:
                status, done, completed_at = "completed", True, (existing["completed_at"] or now_iso())
            elif progress_percent > 0:
                status, done, completed_at = "partially_completed", False, None
            else:
                status, done, completed_at = "not_started", False, None
        else:
            status = existing_status
            done = bool(existing["done"])

        old_pid = existing["parent_task_id"] if "parent_task_id" in ex_keys else None
        conn.execute(
            """
            UPDATE tasks
            SET title = ?, list_type = ?, category = ?, priority = ?,
                start_date = ?, due_date = ?, timeline = ?, frequency = ?,
                parent_task_id = ?, parent_item_id = ?, is_recurring = ?,
                target_days = ?, daily_target_minutes = ?,
                progress_mode = ?, progress_percent = ?, note_id = ?,
                done = ?, status = ?, progress_note = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                title,
                list_type,
                category,
                priority,
                start_date,
                due_date,
                timeline,
                frequency,
                parent_task_id,
                parent_item_id,
                int(is_recurring),
                max(0, target_days),
                max(0, daily_target_minutes),
                progress_mode,
                progress_percent,
                note_id,
                int(done),
                status,
                progress_note,
                completed_at,
                task_id,
            ),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        parent_updates: list[dict[str, Any]] = []
        if done and (not was_done or (parent_task_id is not None and old_pid != parent_task_id)):
            parent_updates = _sync_parent_progress_conn(conn, row, became_done=True)
        elif was_done and not done:
            parent_updates = _sync_parent_progress_conn(
                conn,
                row,
                became_undone=True,
                day_date=(existing["completed_at"][:10] if existing["completed_at"] else today_str()),
            )

        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        enriched = _enrich_tasks_conn(conn, [row])[0]
        if parent_updates:
            enriched["parent_updates"] = parent_updates
        return enriched


def add_task_item(task_id: int, text: str) -> dict[str, Any] | None:
    clean = text.strip()
    if not clean:
        raise ValueError("Checklist item text cannot be empty.")
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            return None
        max_pos_row = conn.execute(
            "SELECT COALESCE(MAX(position), -1) AS mp FROM task_items WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        pos = int(max_pos_row["mp"]) + 1
        conn.execute(
            "INSERT INTO task_items (task_id, text, done, position) VALUES (?, ?, 0, ?)",
            (task_id, clean, pos),
        )
        _recompute_task_rollup(conn, task_id)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _enrich_tasks_conn(conn, [row])[0]


def update_task_item(
    task_id: int, item_id: int, patch: dict[str, Any]
) -> dict[str, Any] | None:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM task_items WHERE id = ? AND task_id = ?",
            (item_id, task_id),
        ).fetchone()
        if not existing:
            return None
        parent_before = conn.execute("SELECT done, completed_at FROM tasks WHERE id = ?", (task_id,)).fetchone()
        was_done = bool(parent_before["done"]) if parent_before else False

        text = str(patch.get("text", existing["text"])).strip()
        if not text:
            raise ValueError("Checklist item text cannot be empty.")
        done = bool(patch.get("done", bool(existing["done"])))
        conn.execute(
            "UPDATE task_items SET text = ?, done = ? WHERE id = ? AND task_id = ?",
            (text, int(done), item_id, task_id),
        )
        _recompute_task_rollup(conn, task_id)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        now_done = bool(row["done"])
        parent_updates: list[dict[str, Any]] = []
        if not was_done and now_done:
            parent_updates = _sync_parent_progress_conn(conn, row, became_done=True)
        elif was_done and not now_done:
            parent_updates = _sync_parent_progress_conn(
                conn,
                row,
                became_undone=True,
                day_date=(parent_before["completed_at"][:10] if parent_before and parent_before["completed_at"] else today_str()),
            )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        enriched = _enrich_tasks_conn(conn, [row])[0]
        if parent_updates:
            enriched["parent_updates"] = parent_updates
        return enriched


def delete_task_item(task_id: int, item_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM task_items WHERE id = ? AND task_id = ?",
            (item_id, task_id),
        )
        if cur.rowcount == 0:
            return None
        _recompute_task_rollup(conn, task_id)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return _enrich_tasks_conn(conn, [row])[0]


def _match_task_item_rows(
    rows: list[sqlite3.Row], targets: list[Any]
) -> list[int]:
    matched_ids: list[int] = []
    for t in targets:
        if isinstance(t, int) or (isinstance(t, str) and t.strip().isdigit()):
            tid = int(t)
            for r in rows:
                if r["id"] == tid and r["id"] not in matched_ids:
                    matched_ids.append(r["id"])
        else:
            needle = str(t).strip().lower()
            if not needle:
                continue
            for r in rows:
                if needle in r["text"].lower() and r["id"] not in matched_ids:
                    matched_ids.append(r["id"])
    return matched_ids


def bulk_manage_task_items(
    task_id: int,
    add_items: list[str] | None = None,
    check_items: list[Any] | None = None,
    uncheck_items: list[Any] | None = None,
    remove_items: list[Any] | None = None,
) -> dict[str, Any] | None:
    """Add, check, uncheck, or remove task checklist items in a single transaction."""
    with get_conn() as conn:
        existing_task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing_task:
            return None
        was_done = bool(existing_task["done"])

        if add_items:
            max_pos_row = conn.execute(
                "SELECT COALESCE(MAX(position), -1) AS mp FROM task_items WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            pos = int(max_pos_row["mp"]) + 1
            for raw_text in add_items:
                clean_item = str(raw_text).strip()
                if clean_item:
                    conn.execute(
                        "INSERT INTO task_items (task_id, text, done, position) VALUES (?, ?, 0, ?)",
                        (task_id, clean_item, pos),
                    )
                    pos += 1

        rows = conn.execute(
            "SELECT * FROM task_items WHERE task_id = ? ORDER BY position ASC, id ASC",
            (task_id,),
        ).fetchall()

        if check_items:
            for item_id in _match_task_item_rows(rows, check_items):
                conn.execute("UPDATE task_items SET done = 1 WHERE id = ?", (item_id,))

        if uncheck_items:
            for item_id in _match_task_item_rows(rows, uncheck_items):
                conn.execute("UPDATE task_items SET done = 0 WHERE id = ?", (item_id,))

        if remove_items:
            for item_id in _match_task_item_rows(rows, remove_items):
                conn.execute("DELETE FROM task_items WHERE id = ?", (item_id,))

        _recompute_task_rollup(conn, task_id)
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        now_done = bool(row["done"])
        parent_updates: list[dict[str, Any]] = []
        if not was_done and now_done:
            parent_updates = _sync_parent_progress_conn(conn, row, became_done=True)
        elif was_done and not now_done:
            parent_updates = _sync_parent_progress_conn(
                conn,
                row,
                became_undone=True,
                day_date=(existing_task["completed_at"][:10] if existing_task["completed_at"] else today_str()),
            )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        enriched = _enrich_tasks_conn(conn, [row])[0]
        if parent_updates:
            enriched["parent_updates"] = parent_updates
        return enriched


def toggle_task_done_today(
    task_id: int,
    day_date: str | None = None,
    done: bool | None = None,
    note: str = "",
) -> dict[str, Any] | None:
    """Toggle or set a recurring/daily goal's completion log for `day_date` (default today)."""
    target_date = (day_date or today_str()).strip()
    dt_date.fromisoformat(target_date)
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        exists = conn.execute(
            "SELECT id FROM task_daily_logs WHERE task_id = ? AND date = ?",
            (task_id, target_date),
        ).fetchone()
        want_done = (not bool(exists)) if done is None else bool(done)
        if want_done:
            if not exists:
                conn.execute(
                    """
                    INSERT INTO task_daily_logs (task_id, date, note, completed_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (task_id, target_date, (note or "").strip(), now_iso()),
                )
            conn.execute("UPDATE tasks SET is_recurring = 1 WHERE id = ?", (task_id,))
        else:
            if exists:
                conn.execute("DELETE FROM task_daily_logs WHERE id = ?", (exists["id"],))
        _recompute_recurring_parent_status_conn(conn, task_id)
        updated = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _enrich_tasks_conn(conn, [updated])[0]


def auto_link_existing_tasks() -> dict[str, Any]:
    """Scan unlinked today-scoped tasks and connect strong matches to their parent goals."""
    linked: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE list_type = 'daily' AND parent_task_id IS NULL ORDER BY id DESC"
        ).fetchall()
        enriched = _enrich_tasks_conn(conn, rows)
        for task in enriched:
            if not task["is_today_task"]:
                continue
            match_res = _analyze_parent_candidates_conn(
                conn,
                task["title"],
                timeline=task["timeline"],
                frequency=task["frequency"],
                due_date=task["due_date"],
                exclude_task_id=task["id"],
            )
            pid = match_res["parent_task_id"]
            piid = match_res["parent_item_id"]
            if pid is not None:
                conn.execute(
                    "UPDATE tasks SET parent_task_id = ?, parent_item_id = ? WHERE id = ?",
                    (pid, piid, task["id"]),
                )
                child_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task["id"],)).fetchone()
                if child_row["done"]:
                    _sync_parent_progress_conn(conn, child_row, became_done=True)
                p_row = conn.execute("SELECT title FROM tasks WHERE id = ?", (pid,)).fetchone()
                linked.append(
                    {
                        "task_id": task["id"],
                        "task_title": task["title"],
                        "parent_task_id": pid,
                        "parent_title": p_row["title"] if p_row else "",
                    }
                )
            elif match_res["possible_parents"]:
                ambiguous.append(
                    {
                        "task_id": task["id"],
                        "task_title": task["title"],
                        "possible_parents": match_res["possible_parents"],
                    }
                )
    return {"linked": linked, "ambiguous": ambiguous, "count": len(linked)}


def record_task_progress(
    task_id: int,
    progress_percent: int | None = None,
    delta_percent: int | None = None,
    note: str = "",
) -> dict[str, Any] | None:
    """Record a progress % update & optional improvement note on a non-countable task."""
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            return None
        rk = existing.keys()
        cur_pct = max(0, min(100, int(existing["progress_percent"] or 0))) if "progress_percent" in rk else (
            100 if existing["done"] else 0
        )
        if progress_percent is not None:
            new_pct = max(0, min(100, int(progress_percent)))
        elif delta_percent is not None:
            new_pct = max(0, min(100, cur_pct + int(delta_percent)))
        else:
            new_pct = cur_pct

        clean_note = (note or "").strip()
        ts = now_iso()
        stored_mode = (
            existing["progress_mode"]
            if "progress_mode" in rk and existing["progress_mode"] in TASK_PROGRESS_MODES
            else "auto"
        )
        new_mode = "percent" if stored_mode == "auto" else stored_mode
        was_done = bool(existing["done"])

        if new_pct >= 100:
            status, done_int, comp_at = "completed", 1, existing["completed_at"] or ts
        elif new_pct > 0:
            status, done_int, comp_at = "partially_completed", 0, None
        else:
            status, done_int, comp_at = "not_started", 0, None

        conn.execute(
            """
            INSERT INTO task_progress_logs (task_id, progress_percent, note, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, new_pct, clean_note, ts),
        )
        conn.execute(
            """
            UPDATE tasks
            SET progress_mode = ?, progress_percent = ?, status = ?, done = ?, completed_at = ?,
                progress_note = CASE WHEN ? != '' THEN ? ELSE progress_note END
            WHERE id = ?
            """,
            (new_mode, new_pct, status, done_int, comp_at, clean_note, clean_note, task_id),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        parent_updates: list[dict[str, Any]] = []
        if done_int and not was_done:
            parent_updates = _sync_parent_progress_conn(conn, row, became_done=True)
        elif was_done and not done_int:
            parent_updates = _sync_parent_progress_conn(
                conn,
                row,
                became_undone=True,
                day_date=(existing["completed_at"][:10] if existing["completed_at"] else today_str()),
            )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        enriched = _enrich_tasks_conn(conn, [row])[0]
        if parent_updates:
            enriched["parent_updates"] = parent_updates
        return enriched


def ensure_task_note(task_id: int) -> dict[str, Any] | None:
    """Return the linked Markdown note for a task, creating one cleanly if missing."""
    task = get_task(task_id)
    if not task:
        return None
    if task.get("note_id") is not None:
        existing_note = get_note(int(task["note_id"]))
        if existing_note:
            return {"task": task, "note": existing_note, "created": False}

    hay = f"{task['title']} {task['category']} {task['timeline']}".lower()
    folder = (
        "Study"
        if any(k in hay for k in ("study", "lecture", "leetcode", "course", "read", "paper", "survey"))
        else "Projects"
    )
    meta_bits = [b for b in (task["timeline"], task["frequency"], task["category"]) if b]
    goal_line = f"- Timeline / Target: {' · '.join(meta_bits)}\n" if meta_bits else ""
    content = (
        f"## Goal & Context\n{goal_line}- Task Status: `{task['status']}`\n\n"
        "## Working Notes\n- \n\n"
        "## Progress & Follow-Ups\n- \n"
    )
    new_note = create_note(
        title=task["title"],
        content=content,
        folder=folder,
        summary=f"Working notes for task: {task['title']}",
        tags=_clean_tags(["task", task["category"] or folder.lower()]),
    )
    updated_task = update_task(task_id, {"note_id": new_note["id"]}) or task
    refreshed_note = get_note(new_note["id"]) or new_note
    return {"task": updated_task, "note": refreshed_note, "created": True}


def append_task_note(task_id: int, text: str, heading: str = "") -> dict[str, Any] | None:
    """Append a note/remark onto the task's linked note (creating the linked note first if needed)."""
    clean_text = (text or "").strip()
    if not clean_text:
        raise ValueError("Note text cannot be empty.")
    ensured = ensure_task_note(task_id)
    if not ensured:
        return None
    note_id = int(ensured["note"]["id"])
    updated_note = append_to_note(note_id, text=clean_text, heading=heading) or ensured["note"]
    refreshed_note = get_note(note_id) or updated_note
    refreshed_task = get_task(task_id) or ensured["task"]
    return {"task": refreshed_task, "note": refreshed_note, "created": ensured["created"]}


def delete_task(task_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def _clean_folder(folder: str | None) -> str:
    return (folder or "").strip()[:60]


def _attach_linked_tasks_to_notes_conn(
    conn: sqlite3.Connection, notes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not notes:
        return notes
    note_ids = [int(n["id"]) for n in notes]
    ph = ",".join("?" for _ in note_ids)
    by_note: dict[int, list[dict[str, Any]]] = {nid: [] for nid in note_ids}
    try:
        t_rows = conn.execute(
            f"SELECT * FROM tasks WHERE note_id IN ({ph}) ORDER BY done ASC, id DESC",
            note_ids,
        ).fetchall()
        for tr in t_rows:
            parsed = _row_to_task(tr, [])
            by_note.setdefault(int(tr["note_id"]), []).append(
                {
                    "id": parsed["id"],
                    "title": parsed["title"],
                    "status": parsed["status"],
                    "done": parsed["done"],
                    "list_type": parsed["list_type"],
                    "is_today_task": parsed["is_today_task"],
                }
            )
    except sqlite3.OperationalError:
        pass
    for n in notes:
        n["linked_tasks"] = by_note.get(int(n["id"]), [])
    return notes


def list_notes(
    query: str = "",
    tag: str = "",
    folder: str = "",
    pinned_only: bool = False,
    sort: str = "updated",
) -> list[dict[str, Any]]:
    order_key = sort if sort in NOTE_SORTS else "updated"
    if order_key == "created":
        order_sql = "ORDER BY pinned DESC, created_at DESC, id DESC"
    elif order_key == "title":
        order_sql = "ORDER BY pinned DESC, LOWER(title) ASC, id DESC"
    else:
        order_sql = "ORDER BY pinned DESC, updated_at DESC, id DESC"

    with get_conn() as conn:
        rows = conn.execute(f"SELECT * FROM notes {order_sql}").fetchall()
        notes = _attach_linked_tasks_to_notes_conn(conn, [_row_to_note(r) for r in rows])

    if pinned_only:
        notes = [n for n in notes if n["pinned"]]

    f_raw = (folder or "").strip()
    if f_raw and f_raw.lower() != "all":
        if f_raw == "__unfiled__":
            notes = [n for n in notes if not n["folder"].strip()]
        else:
            f_low = f_raw.lower()
            notes = [n for n in notes if n["folder"].strip().lower() == f_low]

    q = query.strip().lower()
    t = tag.strip().lstrip("#").lower()
    if t:
        notes = [n for n in notes if any(nt.lower() == t for nt in n["tags"])]
    if q:
        tokens = [tok for tok in re.split(r"\s+", q) if tok]
        filtered = []
        for n in notes:
            hay = (
                f"{n['title']} {n['summary']} {n['content']} "
                f"{n['folder']} {' '.join(n['tags'])}"
            ).lower()
            if all(tok in hay for tok in tokens):
                filtered.append(n)
        notes = filtered
    return notes


def list_note_folders() -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute("SELECT folder, pinned FROM notes").fetchall()

    counts: dict[str, int] = {}
    display_names: dict[str, str] = {}
    for preset in DEFAULT_NOTE_FOLDERS:
        counts[preset.lower()] = 0
        display_names[preset.lower()] = preset

    pinned_count = 0
    unfiled_count = 0
    for r in rows:
        if r["pinned"]:
            pinned_count += 1
        raw_folder = (r["folder"] or "").strip()
        if not raw_folder:
            unfiled_count += 1
            continue
        low = raw_folder.lower()
        if low not in display_names:
            display_names[low] = raw_folder
        counts[low] = counts.get(low, 0) + 1

    preset_lows = [p.lower() for p in DEFAULT_NOTE_FOLDERS]
    folders: list[dict[str, Any]] = [
        {"name": display_names[low], "count": counts[low]} for low in preset_lows
    ]
    custom_lows = sorted(k for k in counts if k not in preset_lows)
    for low in custom_lows:
        folders.append({"name": display_names[low], "count": counts[low]})

    return {
        "total": len(rows),
        "pinned": pinned_count,
        "unfiled": unfiled_count,
        "folders": folders,
    }


def list_note_tags() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT tags FROM notes").fetchall()

    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for r in rows:
        for tag in _parse_json_list(r["tags"]):
            clean = str(tag).strip()
            if not clean:
                continue
            low = clean.lower()
            if low not in display:
                display[low] = clean
            counts[low] = counts.get(low, 0) + 1

    return [
        {"name": display[low], "count": cnt}
        for low, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def get_note(note_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            return None
        return _attach_linked_tasks_to_notes_conn(conn, [_row_to_note(row)])[0]


def get_note_by_source_path(source_path: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM notes WHERE source_path = ?", (source_path,)
        ).fetchone()
        return _row_to_note(row) if row else None


def create_note(
    title: str = "",
    content: str = "",
    tags: list[str] | str | None = None,
    pinned: bool = False,
    source_path: str | None = None,
    file_mtime: float | None = None,
    folder: str = "",
    summary: str = "",
) -> dict[str, Any]:
    clean_title = title.strip()
    clean_content = content.strip("\r\n")
    if not clean_title and not clean_content:
        raise ValueError("Note title and content cannot both be empty.")
    if not clean_title:
        first_line = clean_content.splitlines()[0].lstrip("#").strip()
        clean_title = first_line[:80] or "Untitled Note"
    cleaned_tags = _clean_tags(tags)
    clean_folder = _clean_folder(folder)
    clean_summary = (summary or "").strip()
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO notes (title, content, folder, summary, tags, pinned, source_path, file_mtime, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_title,
                clean_content,
                clean_folder,
                clean_summary,
                json.dumps(cleaned_tags),
                int(bool(pinned)),
                source_path,
                file_mtime,
                ts,
                ts,
            ),
        )
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_note(row)


def update_note(note_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not existing:
            return None
        title = str(patch.get("title", existing["title"])).strip()
        content = str(patch.get("content", existing["content"])).strip("\r\n")
        if not title and content:
            title = content.splitlines()[0].lstrip("#").strip()[:80] or "Untitled Note"
        folder = (
            _clean_folder(str(patch["folder"]))
            if "folder" in patch and patch["folder"] is not None
            else (existing["folder"] or "")
        )
        summary = (
            str(patch["summary"]).strip()
            if "summary" in patch and patch["summary"] is not None
            else (existing["summary"] or "")
        )
        tags = (
            _clean_tags(patch["tags"])
            if "tags" in patch
            else _parse_json_list(existing["tags"])
        )
        pinned = bool(patch.get("pinned", bool(existing["pinned"])))
        source_path = patch.get("source_path", existing["source_path"])
        file_mtime = patch.get("file_mtime", existing["file_mtime"])
        updated_at = now_iso()
        conn.execute(
            """
            UPDATE notes
            SET title = ?, content = ?, folder = ?, summary = ?, tags = ?, pinned = ?,
                source_path = ?, file_mtime = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                title,
                content,
                folder,
                summary,
                json.dumps(tags),
                int(pinned),
                source_path,
                file_mtime,
                updated_at,
                note_id,
            ),
        )
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(row)


def append_to_note(
    note_id: int,
    text: str = "",
    checklist_items: list[str] | None = None,
    add_tags: list[str] | str | None = None,
    heading: str = "",
    folder: str | None = None,
) -> dict[str, Any] | None:
    """Append text, optional Markdown heading, checklist lines, folder, and/or tags to a note."""
    existing = get_note(note_id)
    if not existing:
        return None
    parts: list[str] = []
    if existing["content"].strip():
        parts.append(existing["content"].rstrip())

    new_block_parts: list[str] = []
    clean_heading = (heading or "").strip()
    if clean_heading:
        if not clean_heading.startswith("#"):
            clean_heading = f"## {clean_heading}"
        new_block_parts.append(clean_heading)

    clean_text = (text or "").strip("\r\n")
    if clean_text:
        new_block_parts.append(clean_text)
    if checklist_items:
        checklist_lines = [
            f"- [ ] {str(item).strip()}"
            for item in checklist_items
            if str(item).strip()
        ]
        if checklist_lines:
            new_block_parts.append("\n".join(checklist_lines))

    if new_block_parts:
        parts.append("\n\n".join(new_block_parts))

    merged_content = "\n\n".join(parts) if parts else existing["content"]
    merged_tags = list(existing["tags"])
    for tag in _clean_tags(add_tags):
        if tag.lower() not in {t.lower() for t in merged_tags}:
            merged_tags.append(tag)
    patch: dict[str, Any] = {"content": merged_content, "tags": merged_tags}
    if folder is not None and folder.strip():
        patch["folder"] = _clean_folder(folder)
    return update_note(note_id, patch)


def toggle_note_checklist_line(
    note_id: int,
    item_index: int | None = None,
    match_text: str | None = None,
    done: bool | None = None,
) -> dict[str, Any] | None:
    """Toggle or set `- [ ]` / `- [x]` on one or more checklist lines inside a note."""
    existing = get_note(note_id)
    if not existing:
        return None
    lines = existing["content"].splitlines()
    checklist_counter = 0
    changed = False
    needle = (match_text or "").strip().lower()

    for idx, line in enumerate(lines):
        m = _CHECKLIST_LINE_RE.match(line)
        if not m:
            continue
        cur_done = m.group(2).lower() == "x"
        item_text = m.group(4).strip()
        matched = False
        if item_index is not None and checklist_counter == item_index:
            matched = True
        elif needle and needle in item_text.lower():
            matched = True
        if matched:
            new_done = (not cur_done) if done is None else bool(done)
            mark = "x" if new_done else " "
            lines[idx] = f"{m.group(1)}[{mark}]{m.group(3)}{m.group(4)}"
            changed = True
        checklist_counter += 1

    if not changed:
        return existing
    return update_note(note_id, {"content": "\n".join(lines)})


def delete_note(note_id: int) -> bool:
    with get_conn() as conn:
        try:
            conn.execute("UPDATE tasks SET note_id = NULL WHERE note_id = ?", (note_id,))
        except sqlite3.OperationalError:
            pass
        cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Activity Days (Gym & Art consistency calendars)
# ---------------------------------------------------------------------------

def _calculate_streak(all_dates_desc: list[str], reference_today: str | None = None) -> int:
    if not all_dates_desc:
        return 0
    date_set = set(all_dates_desc)
    today = dt_date.fromisoformat(reference_today or today_str())
    cursor = today if today.isoformat() in date_set else today - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in date_set:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def get_activity_summary(domain: str, month: str | None = None) -> dict[str, Any]:
    if domain not in ACTIVITY_DOMAINS:
        raise ValueError(f"Unsupported activity domain: {domain}")
    target_month = (month or today_str()[:7]).strip()
    with get_conn() as conn:
        all_rows = conn.execute(
            "SELECT date, note FROM activity_days WHERE domain = ? ORDER BY date DESC",
            (domain,),
        ).fetchall()
    all_dates = [r["date"] for r in all_rows]
    month_days = [
        {"date": r["date"], "note": r["note"]}
        for r in all_rows
        if r["date"].startswith(target_month)
    ]
    today = today_str()
    return {
        "domain": domain,
        "month": target_month,
        "today_active": today in set(all_dates),
        "month_count": len(month_days),
        "total_count": len(all_dates),
        "streak": _calculate_streak(all_dates, today),
        "days": month_days,
        "all_dates": all_dates,
    }


def set_activity_day(
    domain: str,
    day_date: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    if domain not in ACTIVITY_DOMAINS:
        raise ValueError(f"Unsupported activity domain: {domain}")
    target_date = (day_date or today_str()).strip()
    dt_date.fromisoformat(target_date)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO activity_days (domain, date, note)
            VALUES (?, ?, ?)
            ON CONFLICT(domain, date) DO UPDATE SET
                note = CASE WHEN excluded.note != '' THEN excluded.note ELSE activity_days.note END
            """,
            (domain, target_date, note.strip()),
        )
    return get_activity_summary(domain, target_date[:7])


def delete_activity_day(domain: str, day_date: str | None = None) -> dict[str, Any]:
    if domain not in ACTIVITY_DOMAINS:
        raise ValueError(f"Unsupported activity domain: {domain}")
    target_date = (day_date or today_str()).strip()
    dt_date.fromisoformat(target_date)
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM activity_days WHERE domain = ? AND date = ?",
            (domain, target_date),
        )
    return get_activity_summary(domain, target_date[:7])


def toggle_activity_day(
    domain: str,
    day_date: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    if domain not in ACTIVITY_DOMAINS:
        raise ValueError(f"Unsupported activity domain: {domain}")
    target_date = (day_date or today_str()).strip()
    dt_date.fromisoformat(target_date)
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM activity_days WHERE domain = ? AND date = ?",
            (domain, target_date),
        ).fetchone()
    if existing:
        summary = delete_activity_day(domain, target_date)
        summary["active"] = False
    else:
        summary = set_activity_day(domain, target_date, note)
        summary["active"] = True
    summary["toggled_date"] = target_date
    return summary


# ---------------------------------------------------------------------------
# Gym Logs & Personal Bests
# ---------------------------------------------------------------------------

def list_gym_logs(
    day_date: str | None = None,
    exercise: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if day_date:
        clauses.append("date = ?")
        params.append(day_date.strip())
    if exercise:
        clauses.append("LOWER(exercise) LIKE ?")
        params.append(f"%{exercise.strip().lower()}%")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM gym_logs {where_sql} ORDER BY date DESC, id DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_gym_log(r) for r in rows]


def create_gym_log(
    exercise: str,
    sets: int = 1,
    reps: int = 0,
    weight: float | None = None,
    unit: str = "kg",
    notes: str = "",
    day_date: str | None = None,
) -> dict[str, Any]:
    clean_ex = exercise.strip()
    if not clean_ex:
        raise ValueError("Exercise name cannot be empty.")
    target_date = (day_date or today_str()).strip()
    dt_date.fromisoformat(target_date)
    clean_unit = unit if unit in ("kg", "lbs") else "kg"
    clean_sets = max(1, int(sets or 1))
    clean_reps = max(0, int(reps or 0))
    clean_weight = float(weight) if weight is not None and str(weight) != "" else None
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO gym_logs (date, exercise, sets, reps, weight, unit, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (target_date, clean_ex, clean_sets, clean_reps, clean_weight, clean_unit, notes.strip(), ts),
        )
        # Logging an exercise automatically marks that date as a Gym Day.
        conn.execute(
            "INSERT OR IGNORE INTO activity_days (domain, date, note) VALUES ('gym', ?, '')",
            (target_date,),
        )
        row = conn.execute("SELECT * FROM gym_logs WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_gym_log(row)


def update_gym_log(log_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM gym_logs WHERE id = ?", (log_id,)).fetchone()
        if not existing:
            return None
        exercise = str(patch.get("exercise", existing["exercise"])).strip()
        if not exercise:
            raise ValueError("Exercise name cannot be empty.")
        day_date = str(patch.get("date", existing["date"])).strip()
        dt_date.fromisoformat(day_date)
        sets = max(1, int(patch.get("sets", existing["sets"])))
        reps = max(0, int(patch.get("reps", existing["reps"])))
        weight = patch.get("weight", existing["weight"])
        weight = float(weight) if weight is not None and str(weight) != "" else None
        unit = patch.get("unit", existing["unit"])
        if unit not in ("kg", "lbs"):
            unit = existing["unit"]
        notes = str(patch.get("notes", existing["notes"])).strip()
        conn.execute(
            """
            UPDATE gym_logs
            SET date = ?, exercise = ?, sets = ?, reps = ?, weight = ?, unit = ?, notes = ?
            WHERE id = ?
            """,
            (day_date, exercise, sets, reps, weight, unit, notes, log_id),
        )
        row = conn.execute("SELECT * FROM gym_logs WHERE id = ?", (log_id,)).fetchone()
        return _row_to_gym_log(row)


def delete_gym_log(log_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM gym_logs WHERE id = ?", (log_id,))
        return cur.rowcount > 0


def get_gym_exercise_stats() -> list[dict[str, Any]]:
    """Return distinct exercises with last logged values and personal bests."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM gym_logs ORDER BY date DESC, id DESC"
        ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r["exercise"].strip().lower()
        if not key:
            continue
        if key not in grouped:
            grouped[key] = {
                "exercise": r["exercise"].strip(),
                "last_date": r["date"],
                "last_sets": r["sets"],
                "last_reps": r["reps"],
                "last_weight": r["weight"],
                "last_unit": r["unit"],
                "max_weight": r["weight"],
                "max_weight_unit": r["unit"],
                "max_reps": r["reps"],
                "entries": 1,
            }
        else:
            item = grouped[key]
            item["entries"] += 1
            if r["weight"] is not None:
                if item["max_weight"] is None or r["weight"] > item["max_weight"]:
                    item["max_weight"] = r["weight"]
                    item["max_weight_unit"] = r["unit"]
            if r["reps"] > item["max_reps"]:
                item["max_reps"] = r["reps"]
    return sorted(grouped.values(), key=lambda x: (x["last_date"], x["exercise"]), reverse=True)


# ---------------------------------------------------------------------------
# Art Items & Reference Media
# ---------------------------------------------------------------------------

def list_art_items(status: str | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if status in ART_STATUSES:
            rows = conn.execute(
                "SELECT * FROM art_items WHERE status = ? ORDER BY updated_at DESC, id DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM art_items
                ORDER BY CASE status WHEN 'wip' THEN 0 WHEN 'idea' THEN 1 ELSE 2 END ASC,
                         updated_at DESC, id DESC
                """
            ).fetchall()
        return [_row_to_art(r) for r in rows]


def get_art_item(item_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM art_items WHERE id = ?", (item_id,)).fetchone()
        return _row_to_art(row) if row else None


def find_art_item_by_title(title: str) -> dict[str, Any] | None:
    clean = title.strip()
    if not clean:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM art_items WHERE LOWER(title) = LOWER(?) LIMIT 1",
            (clean,),
        ).fetchone()
        return _row_to_art(row) if row else None


def create_art_item(
    title: str,
    status: str = "idea",
    notes: str = "",
    image_files: list[str] | None = None,
) -> dict[str, Any]:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Art title cannot be empty.")
    st = status if status in ART_STATUSES else "idea"
    files = [Path(f).name for f in (image_files or []) if f]
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO art_items (title, status, notes, image_files, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (clean_title, st, notes.strip(), json.dumps(files), ts, ts),
        )
        row = conn.execute("SELECT * FROM art_items WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_art(row)


def update_art_item(item_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM art_items WHERE id = ?", (item_id,)).fetchone()
        if not existing:
            return None
        title = str(patch.get("title", existing["title"])).strip()
        if not title:
            raise ValueError("Art title cannot be empty.")
        status = patch.get("status", existing["status"])
        if status not in ART_STATUSES:
            status = existing["status"]
        notes = str(patch.get("notes", existing["notes"])).strip()
        files = (
            [Path(f).name for f in patch["image_files"] if f]
            if "image_files" in patch and isinstance(patch["image_files"], list)
            else _parse_json_list(existing["image_files"])
        )
        conn.execute(
            """
            UPDATE art_items
            SET title = ?, status = ?, notes = ?, image_files = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, status, notes, json.dumps(files), now_iso(), item_id),
        )
        row = conn.execute("SELECT * FROM art_items WHERE id = ?", (item_id,)).fetchone()
        return _row_to_art(row)


def add_art_image(item_id: int, filename: str) -> dict[str, Any] | None:
    safe_name = Path(filename).name
    item = get_art_item(item_id)
    if not item:
        return None
    files = list(item["image_files"])
    if safe_name not in files:
        files.append(safe_name)
    return update_art_item(item_id, {"image_files": files})


def _cleanup_unreferenced_media(filenames: list[str]) -> None:
    if not filenames:
        return
    with get_conn() as conn:
        rows = conn.execute("SELECT image_files FROM art_items").fetchall()
    referenced: set[str] = set()
    for r in rows:
        for f in _parse_json_list(r["image_files"]):
            referenced.add(f)
    media_dir = get_media_dir()
    for name in filenames:
        if name not in referenced:
            target = media_dir / Path(name).name
            if target.exists() and target.is_file():
                target.unlink(missing_ok=True)


def remove_art_image(item_id: int, filename: str) -> dict[str, Any] | None:
    safe_name = Path(filename).name
    item = get_art_item(item_id)
    if not item:
        return None
    files = [f for f in item["image_files"] if f != safe_name]
    updated = update_art_item(item_id, {"image_files": files})
    _cleanup_unreferenced_media([safe_name])
    return updated


def delete_art_item(item_id: int) -> bool:
    item = get_art_item(item_id)
    if not item:
        return False
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM art_items WHERE id = ?", (item_id,))
    if cur.rowcount > 0:
        _cleanup_unreferenced_media(item["image_files"])
        return True
    return False


# ---------------------------------------------------------------------------
# Games Tracker
# ---------------------------------------------------------------------------

def list_games(status: str | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn:
        if status in GAME_STATUSES:
            rows = conn.execute(
                "SELECT * FROM games WHERE status = ? ORDER BY updated_at DESC, id DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM games
                ORDER BY CASE status
                    WHEN 'playing' THEN 0
                    WHEN 'want_to_play' THEN 1
                    WHEN 'on_hold' THEN 2
                    ELSE 3
                END ASC, updated_at DESC, id DESC
                """
            ).fetchall()
        return [_row_to_game(r) for r in rows]


def get_game(game_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        return _row_to_game(row) if row else None


def find_game_by_title(title: str) -> dict[str, Any] | None:
    clean = title.strip()
    if not clean:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM games WHERE LOWER(title) = LOWER(?) LIMIT 1",
            (clean,),
        ).fetchone()
        return _row_to_game(row) if row else None


def create_game(
    title: str,
    status: str = "want_to_play",
    platform: str = "",
    rating: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Game title cannot be empty.")
    st = status if status in GAME_STATUSES else "want_to_play"
    clean_rating = int(rating) if rating is not None and str(rating) != "" else None
    if clean_rating is not None:
        clean_rating = max(1, min(10, clean_rating))
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO games (title, status, platform, rating, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (clean_title, st, platform.strip(), clean_rating, notes.strip(), ts, ts),
        )
        row = conn.execute("SELECT * FROM games WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_game(row)


def update_game(game_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        if not existing:
            return None
        title = str(patch.get("title", existing["title"])).strip()
        if not title:
            raise ValueError("Game title cannot be empty.")
        status = patch.get("status", existing["status"])
        if status not in GAME_STATUSES:
            status = existing["status"]
        platform = str(patch.get("platform", existing["platform"])).strip()
        if "rating" in patch:
            raw_rating = patch["rating"]
            rating = int(raw_rating) if raw_rating is not None and str(raw_rating) != "" else None
            if rating is not None:
                rating = max(1, min(10, rating))
        else:
            rating = existing["rating"]
        notes = str(patch.get("notes", existing["notes"])).strip()
        conn.execute(
            """
            UPDATE games
            SET title = ?, status = ?, platform = ?, rating = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (title, status, platform, rating, notes, now_iso(), game_id),
        )
        row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
        return _row_to_game(row)


def delete_game(game_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Capture History, Search, Dashboard & Export
# ---------------------------------------------------------------------------

def record_capture(
    raw_text: str,
    source: str,
    summary: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO capture_history (raw_text, source, summary, actions_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (raw_text.strip(), source, summary.strip(), json.dumps(actions), ts),
        )
        row = conn.execute(
            "SELECT * FROM capture_history WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return {
            "id": row["id"],
            "raw_text": row["raw_text"],
            "source": row["source"],
            "summary": row["summary"],
            "actions": _parse_json_list(row["actions_json"]),
            "created_at": row["created_at"],
        }


def list_captures(limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM capture_history ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "raw_text": r["raw_text"],
            "source": r["source"],
            "summary": r["summary"],
            "actions": _parse_json_list(r["actions_json"]),
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _score_match(query_lower: str, tokens: list[str], text: str, boost: float = 0.0) -> float:
    hay = text.lower()
    score = 0.0
    if query_lower and query_lower in hay:
        score += 4.0
    for tok in tokens:
        if tok in hay:
            score += 1.5
    if score > 0:
        score += boost
    return score


def search_everything(query: str, limit: int = 25) -> list[dict[str, Any]]:
    """Search across notes, tasks, gym logs, art items, and games."""
    clean = query.strip()
    if not clean:
        return []
    q_lower = clean.lower()
    tokens = [tok for tok in re.split(r"[\s,?!.;:]+", q_lower) if len(tok) >= 2]
    if not tokens:
        tokens = [q_lower]

    results: list[dict[str, Any]] = []
    with get_conn() as conn:
        note_rows = conn.execute("SELECT * FROM notes ORDER BY pinned DESC, updated_at DESC").fetchall()
        task_rows = conn.execute("SELECT * FROM tasks ORDER BY done ASC, id DESC").fetchall()
        task_items_map = _fetch_task_items_map(conn, [r["id"] for r in task_rows])
        gym_rows = conn.execute("SELECT * FROM gym_logs ORDER BY date DESC, id DESC").fetchall()
        art_rows = conn.execute("SELECT * FROM art_items ORDER BY updated_at DESC").fetchall()
        game_rows = conn.execute("SELECT * FROM games ORDER BY updated_at DESC").fetchall()
        study_rows = conn.execute("SELECT * FROM study_sessions ORDER BY date DESC, id DESC").fetchall()
        watch_rows = conn.execute("SELECT * FROM watchlist_items ORDER BY updated_at DESC").fetchall()

    for r in note_rows:
        tags = _parse_json_list(r["tags"])
        r_keys = r.keys()
        folder = r["folder"] if "folder" in r_keys else ""
        summary = r["summary"] if "summary" in r_keys else ""
        text = f"{r['title']} {folder} {summary} {r['content']} {' '.join(tags)}"
        score = _score_match(q_lower, tokens, text, boost=1.0 if r["pinned"] else 0.3)
        if score > 0:
            meta_parts = []
            if folder:
                meta_parts.append(f"📁 {folder}")
            if tags:
                meta_parts.append(", ".join(f"#{t}" for t in tags))
            results.append(
                {
                    "type": "note",
                    "id": r["id"],
                    "title": r["title"] or "Untitled Note",
                    "snippet": (summary or r["content"] or "")[:240],
                    "meta": " · ".join(meta_parts) if meta_parts else r["updated_at"][:10],
                    "score": score,
                }
            )

    for r in task_rows:
        sub_items = task_items_map.get(r["id"], [])
        sub_text = " ".join(it["text"] for it in sub_items)
        t_obj = _row_to_task(r, sub_items)
        text = f"{r['title']} {r['category']} {r['list_type']} {t_obj['timeline']} {t_obj['frequency']} {t_obj['progress_note']} {sub_text}"
        score = _score_match(q_lower, tokens, text, boost=0.2 if not t_obj["done"] else 0.0)
        if score > 0:
            label = "Active Task" if r["list_type"] == "daily" else "Want to Do"
            st_label = t_obj["status"].replace("_", " ").title()
            prog = (
                f" · {t_obj['checklist_done']}/{t_obj['checklist_total']}"
                if t_obj["checklist_total"]
                else ""
            )
            results.append(
                {
                    "type": "task",
                    "id": r["id"],
                    "title": r["title"],
                    "snippet": f"{label} ({st_label}{prog})" + (f" · {r['category']}" if r["category"] else ""),
                    "meta": r["due_date"] or r["created_at"][:10],
                    "score": score,
                }
            )

    for r in gym_rows:
        weight_txt = f"{r['weight']}{r['unit']}" if r["weight"] is not None else "bodyweight"
        text = f"gym {r['exercise']} {r['notes']} {r['date']} {weight_txt}"
        score = _score_match(q_lower, tokens, text, boost=0.2)
        if score > 0:
            results.append(
                {
                    "type": "gym",
                    "id": r["id"],
                    "title": f"{r['exercise']} ({r['sets']}x{r['reps']} @ {weight_txt})",
                    "snippet": r["notes"] or f"Logged on {r['date']}",
                    "meta": r["date"],
                    "score": score,
                }
            )

    for r in art_rows:
        text = f"art {r['title']} {r['status']} {r['notes']}"
        score = _score_match(q_lower, tokens, text, boost=0.2)
        if score > 0:
            results.append(
                {
                    "type": "art",
                    "id": r["id"],
                    "title": r["title"],
                    "snippet": r["notes"] or f"Status: {r['status'].upper()}",
                    "meta": r["status"].upper(),
                    "score": score,
                }
            )

    for r in game_rows:
        text = f"game gaming {r['title']} {r['platform']} {r['status']} {r['notes']}"
        score = _score_match(q_lower, tokens, text, boost=0.2)
        if score > 0:
            rating_txt = f" · {r['rating']}/10" if r["rating"] else ""
            results.append(
                {
                    "type": "game",
                    "id": r["id"],
                    "title": r["title"],
                    "snippet": r["notes"] or f"{r['status'].replace('_', ' ').title()}{rating_txt}",
                    "meta": f"{r['platform'] or 'Game'}{rating_txt}",
                    "score": score,
                }
            )

    for r in study_rows:
        mins = max(1, round(int(r["duration_seconds"]) / 60))
        text = f"study focus timer {r['subject']} {r['topic']} {r['notes']} {r['date']}"
        score = _score_match(q_lower, tokens, text, boost=0.2)
        if score > 0:
            title_lbl = f"{r['subject']}: {r['topic']}" if r["topic"] else f"{r['subject']} Study Session"
            results.append(
                {
                    "type": "study",
                    "id": r["id"],
                    "title": f"{title_lbl} ({mins}m)",
                    "snippet": r["notes"] or f"{r['mode'].title()} session on {r['date']}",
                    "meta": r["date"],
                    "score": score,
                }
            )

    for r in watch_rows:
        tags = _parse_json_list(r["tags"])
        text = f"watch watchlist playlist {r['title']} {r['url']} {r['content_type']} {r['source']} {r['category']} {r['notes']} {' '.join(tags)}"
        score = _score_match(q_lower, tokens, text, boost=0.25 if r["status"] == "watching" else 0.1)
        if score > 0:
            prog = f" · {r['progress_current']}/{r['progress_total']}" if r["progress_total"] > 0 else ""
            results.append(
                {
                    "type": "watchlist",
                    "id": r["id"],
                    "title": r["title"],
                    "snippet": r["notes"] or r["url"] or f"{r['content_type'].title()}{prog}",
                    "meta": f"{r['status'].replace('_', ' ').title()}{prog}",
                    "score": score,
                }
            )

    results.sort(key=lambda item: item["score"], reverse=True)
    return results[: max(1, min(limit, 50))]


def get_dashboard() -> dict[str, Any]:
    today = today_str()
    gym_summary = get_activity_summary("gym", today[:7])
    art_summary = get_activity_summary("art", today[:7])
    daily_tasks = list_tasks(list_type="daily", include_done=True)
    open_daily = [t for t in daily_tasks if not t["done"]]
    done_today = [
        t for t in daily_tasks if t["done"] and (t["completed_at"] or "").startswith(today)
    ]
    want_to_do = list_tasks(list_type="want_to_do", include_done=False)[:8]
    notes = list_notes()[:6]
    art_wips = list_art_items(status="wip")[:4]
    art_ideas = list_art_items(status="idea")[:4]
    playing_games = list_games(status="playing")[:5]
    recent_gym = list_gym_logs(limit=6)
    recent_captures = list_captures(limit=8)
    study_stats = get_study_stats()
    timer_state = get_timer_state()
    watchlist_all = list_watchlist()
    watchlist_active = [w for w in watchlist_all if w["status"] == "watching"][:4]

    return {
        "today": today,
        "gym": {
            "today_active": gym_summary["today_active"],
            "month_count": gym_summary["month_count"],
            "streak": gym_summary["streak"],
        },
        "art": {
            "today_active": art_summary["today_active"],
            "month_count": art_summary["month_count"],
            "streak": art_summary["streak"],
        },
        "study": study_stats,
        "timer": timer_state,
        "watchlist_active": watchlist_active,
        "watchlist_count": len(watchlist_all),
        "daily_tasks": open_daily[:12],
        "daily_done_today_count": len(done_today),
        "daily_open_count": len(open_daily),
        "want_to_do": want_to_do,
        "recent_notes": notes,
        "art_wips": art_wips,
        "art_ideas": art_ideas,
        "playing_games": playing_games,
        "recent_gym_logs": recent_gym,
        "recent_captures": recent_captures,
    }


# ---------------------------------------------------------------------------
# Agent Sessions & Multi-Turn History
# ---------------------------------------------------------------------------

def _row_to_agent_session(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _row_to_agent_message(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "role": row["role"],
        "content": row["content"],
        "actions": _parse_json_list(row["actions_json"]),
        "source": row["source"],
        "created_at": row["created_at"],
    }


def create_agent_session(title: str = "New Session") -> dict[str, Any]:
    clean = (title or "New Session").strip()[:80] or "New Session"
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO agent_sessions (title, created_at, updated_at) VALUES (?, ?, ?)",
            (clean, ts, ts),
        )
        row = conn.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _row_to_agent_session(row)


def get_or_create_active_session(session_id: int | None = None) -> dict[str, Any]:
    with get_conn() as conn:
        if session_id is not None:
            row = conn.execute(
                "SELECT * FROM agent_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row:
                return _row_to_agent_session(row)
        latest = conn.execute(
            "SELECT * FROM agent_sessions ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if latest:
            return _row_to_agent_session(latest)
    return create_agent_session("Voice Session")


def list_agent_sessions(limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_sessions ORDER BY updated_at DESC, id DESC LIMIT ?",
            (max(1, min(limit, 50)),),
        ).fetchall()
        return [_row_to_agent_session(r) for r in rows]


def update_agent_session_title(session_id: int, title: str) -> None:
    clean = (title or "").strip()[:80]
    if not clean:
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE agent_sessions SET title = ?, updated_at = ? WHERE id = ?",
            (clean, now_iso(), session_id),
        )


def add_agent_message(
    session_id: int,
    role: str,
    content: str,
    actions: list[dict[str, Any]] | None = None,
    source: str = "text",
) -> dict[str, Any]:
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO agent_messages (session_id, role, content, actions_json, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, role, content.strip(), json.dumps(actions or []), source, ts),
        )
        conn.execute(
            "UPDATE agent_sessions SET updated_at = ? WHERE id = ?",
            (ts, session_id),
        )
        row = conn.execute(
            "SELECT * FROM agent_messages WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _row_to_agent_message(row)


def get_agent_session_detail(
    session_id: int, message_limit: int = 40
) -> dict[str, Any] | None:
    with get_conn() as conn:
        s_row = conn.execute(
            "SELECT * FROM agent_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not s_row:
            return None
        m_rows = conn.execute(
            """
            SELECT * FROM (
                SELECT * FROM agent_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?
            ) sub ORDER BY id ASC
            """,
            (session_id, max(1, min(message_limit, 200))),
        ).fetchall()
        session = _row_to_agent_session(s_row)
        session["messages"] = [_row_to_agent_message(r) for r in m_rows]
        return session


def delete_agent_session(session_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM agent_sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


def export_all_data() -> dict[str, Any]:
    """Return a full JSON-serializable backup of all user records."""
    with get_conn() as conn:
        dl_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id, task_id, date, note, completed_at FROM task_daily_logs ORDER BY date DESC, id DESC"
            ).fetchall()
        ]
        pl_rows = [
            dict(r)
            for r in conn.execute(
                "SELECT id, task_id, progress_percent, note, created_at FROM task_progress_logs ORDER BY id DESC"
            ).fetchall()
        ]
    return {
        "exported_at": now_iso(),
        "tasks": list_tasks(include_done=True),
        "task_daily_logs": dl_rows,
        "task_progress_logs": pl_rows,
        "notes": list_notes(),
        "gym_activity": get_activity_summary("gym"),
        "art_activity": get_activity_summary("art"),
        "gym_logs": list_gym_logs(limit=500),
        "art_items": list_art_items(),
        "games": list_games(),
        "study_sessions": list_study_sessions(limit=500),
        "study_stats": get_study_stats(),
        "watchlist": list_watchlist(),
        "captures": list_captures(limit=100),
        "agent_sessions": list_agent_sessions(limit=50),
    }


# ---------------------------------------------------------------------------
# Study Sessions & Live Focus Timer State Machine
# ---------------------------------------------------------------------------

def _validate_fk_ids(
    conn: sqlite3.Connection, task_id: Any, note_id: Any
) -> tuple[int | None, int | None]:
    valid_task: int | None = None
    valid_note: int | None = None
    if task_id is not None and str(task_id).strip() and str(task_id).lower() != "null":
        try:
            tid = int(task_id)
            if conn.execute("SELECT id FROM tasks WHERE id = ?", (tid,)).fetchone():
                valid_task = tid
        except (TypeError, ValueError):
            pass
    if note_id is not None and str(note_id).strip() and str(note_id).lower() != "null":
        try:
            nid = int(note_id)
            if conn.execute("SELECT id FROM notes WHERE id = ?", (nid,)).fetchone():
                valid_note = nid
        except (TypeError, ValueError):
            pass
    return valid_task, valid_note


def _row_to_study_session(row: sqlite3.Row) -> dict[str, Any]:
    row_keys = row.keys()
    secs = int(row["duration_seconds"])
    return {
        "id": row["id"],
        "subject": row["subject"],
        "topic": row["topic"],
        "mode": row["mode"],
        "duration_seconds": secs,
        "duration_minutes": round(secs / 60.0, 1),
        "planned_seconds": row["planned_seconds"],
        "date": row["date"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "task_id": row["task_id"],
        "task_title": (row["task_title"] if "task_title" in row_keys and row["task_title"] else ""),
        "note_id": row["note_id"],
        "note_title": (row["note_title"] if "note_title" in row_keys and row["note_title"] else ""),
        "notes": row["notes"],
        "created_at": row["created_at"],
    }


_STUDY_JOIN_SELECT = """
    SELECT s.*, t.title AS task_title, n.title AS note_title
    FROM study_sessions s
    LEFT JOIN tasks t ON t.id = s.task_id
    LEFT JOIN notes n ON n.id = s.note_id
"""


def _insert_study_session_conn(
    conn: sqlite3.Connection,
    *,
    subject: str = "Study",
    topic: str = "",
    duration_seconds: int,
    mode: str = "manual",
    planned_seconds: int | None = None,
    day_date: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    task_id: int | None = None,
    note_id: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    clean_secs = int(duration_seconds)
    if clean_secs <= 0:
        raise ValueError("Study session duration must be greater than zero.")
    target_date = (day_date or today_str()).strip()
    dt_date.fromisoformat(target_date)
    clean_subject = (subject or "Study").strip()[:60] or "Study"
    clean_mode = mode if mode in STUDY_MODES else "manual"
    valid_task, valid_note = _validate_fk_ids(conn, task_id, note_id)
    ts = now_iso()
    end_iso = (ended_at or ts).strip()
    cur = conn.execute(
        """
        INSERT INTO study_sessions (
            subject, topic, mode, duration_seconds, planned_seconds,
            date, started_at, ended_at, task_id, note_id, notes, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_subject,
            (topic or "").strip()[:160],
            clean_mode,
            clean_secs,
            int(planned_seconds) if planned_seconds is not None else None,
            target_date,
            started_at,
            end_iso,
            valid_task,
            valid_note,
            (notes or "").strip(),
            ts,
        ),
    )
    row = conn.execute(
        f"{_STUDY_JOIN_SELECT} WHERE s.id = ?", (cur.lastrowid,)
    ).fetchone()
    if valid_task is not None and target_date == today_str():
        t_row = conn.execute(
            "SELECT id, parent_task_id, title, timeline, frequency, start_date, due_date, is_recurring, target_days, daily_target_minutes FROM tasks WHERE id = ?",
            (valid_task,),
        ).fetchone()
        if t_row:
            candidate_ids = [int(t_row["id"])]
            if t_row["parent_task_id"] is not None:
                candidate_ids.append(int(t_row["parent_task_id"]))
            for gid in candidate_ids:
                g_row = conn.execute("SELECT * FROM tasks WHERE id = ?", (gid,)).fetchone()
                if not g_row:
                    continue
                rk = g_row.keys()
                inf_rec, _, inf_mins = infer_recurrence_fields(
                    g_row["title"],
                    timeline=g_row["timeline"] if "timeline" in rk else "",
                    frequency=g_row["frequency"] if "frequency" in rk else "",
                    start_date=g_row["start_date"] if "start_date" in rk else None,
                    due_date=g_row["due_date"],
                )
                is_rec = (bool(g_row["is_recurring"]) if "is_recurring" in rk else False) or inf_rec
                stored_mins = int(g_row["daily_target_minutes"] or 0) if "daily_target_minutes" in rk else 0
                daily_mins = stored_mins if stored_mins > 0 else (inf_mins if is_rec else 0)
                if is_rec and daily_mins > 0:
                    ch_rows = conn.execute(
                        "SELECT id FROM tasks WHERE parent_task_id = ?", (gid,)
                    ).fetchall()
                    rel_ids = [gid, *[int(c["id"]) for c in ch_rows]]
                    ph = ",".join("?" for _ in rel_ids)
                    studied_today = int(
                        conn.execute(
                            f"SELECT COALESCE(SUM(duration_seconds), 0) FROM study_sessions WHERE date = ? AND task_id IN ({ph})",
                            [target_date, *rel_ids],
                        ).fetchone()[0]
                    )
                    if studied_today >= max(30, daily_mins * 60 - 30):
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO task_daily_logs (task_id, date, note, completed_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (gid, target_date, f"Logged {round(studied_today / 60, 1):g}m focus study", ts),
                        )
                        _recompute_recurring_parent_status_conn(conn, gid)
    return _row_to_study_session(row)


def create_study_session(
    subject: str = "Study",
    topic: str = "",
    duration_seconds: int | None = None,
    duration_minutes: float | None = None,
    mode: str = "manual",
    planned_seconds: int | None = None,
    day_date: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    task_id: int | None = None,
    note_id: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    if duration_seconds is not None:
        secs = int(duration_seconds)
    elif duration_minutes is not None:
        secs = int(round(float(duration_minutes) * 60))
    else:
        secs = 0
    if secs <= 0:
        raise ValueError("Study session duration must be greater than zero.")
    with get_conn() as conn:
        return _insert_study_session_conn(
            conn,
            subject=subject,
            topic=topic,
            duration_seconds=secs,
            mode=mode,
            planned_seconds=planned_seconds,
            day_date=day_date,
            started_at=started_at,
            ended_at=ended_at,
            task_id=task_id,
            note_id=note_id,
            notes=notes,
        )


def list_study_sessions(
    subject: str | None = None,
    day_date: str | None = None,
    task_id: int | None = None,
    note_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if subject and subject.strip():
        clauses.append("LOWER(s.subject) = LOWER(?)")
        params.append(subject.strip())
    if day_date and day_date.strip():
        clauses.append("s.date = ?")
        params.append(day_date.strip())
    if task_id is not None:
        clauses.append("s.task_id = ?")
        params.append(int(task_id))
    if note_id is not None:
        clauses.append("s.note_id = ?")
        params.append(int(note_id))
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"{_STUDY_JOIN_SELECT} {where_sql} ORDER BY s.date DESC, s.id DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_study_session(r) for r in rows]


def update_study_session(session_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM study_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not existing:
            return None
        subject = (str(patch.get("subject", existing["subject"])) or "Study").strip()[:60] or "Study"
        topic = str(patch.get("topic", existing["topic"])).strip()[:160]
        if "duration_seconds" in patch and patch["duration_seconds"] is not None:
            secs = int(patch["duration_seconds"])
        elif "duration_minutes" in patch and patch["duration_minutes"] is not None:
            secs = int(round(float(patch["duration_minutes"]) * 60))
        else:
            secs = int(existing["duration_seconds"])
        if secs <= 0:
            raise ValueError("Study session duration must be greater than zero.")
        day_date = str(patch.get("date", existing["date"])).strip()
        dt_date.fromisoformat(day_date)

        raw_task = patch["task_id"] if "task_id" in patch else existing["task_id"]
        raw_note = patch["note_id"] if "note_id" in patch else existing["note_id"]
        valid_task, valid_note = _validate_fk_ids(conn, raw_task, raw_note)
        notes = str(patch.get("notes", existing["notes"])).strip()

        conn.execute(
            """
            UPDATE study_sessions
            SET subject = ?, topic = ?, duration_seconds = ?, date = ?,
                task_id = ?, note_id = ?, notes = ?
            WHERE id = ?
            """,
            (subject, topic, secs, day_date, valid_task, valid_note, notes, session_id),
        )
        row = conn.execute(
            f"{_STUDY_JOIN_SELECT} WHERE s.id = ?", (session_id,)
        ).fetchone()
        return _row_to_study_session(row)


def delete_study_session(session_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM study_sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


def get_study_stats() -> dict[str, Any]:
    today = today_str()
    today_dt = dt_date.fromisoformat(today)
    week_dates = [(today_dt - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
    week_set = set(week_dates)
    month_prefix = today[:7]

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT subject, duration_seconds, date FROM study_sessions ORDER BY date DESC, id DESC"
        ).fetchall()
        today_sess_rows = conn.execute(
            f"{_STUDY_JOIN_SELECT} WHERE s.date = ? ORDER BY s.id DESC LIMIT 20",
            (today,),
        ).fetchall()
        timer_row = conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()
        live_focus_secs = 0
        live_subj = ""
        if timer_row and timer_row["active"] and timer_row["phase"] == "focus":
            t_fmt = _format_timer_row(conn, timer_row)
            live_focus_secs = max(0, int(t_fmt["elapsed_seconds"] or 0))
            live_subj = (t_fmt["subject"] or "Study").strip() or "Study"

    today_secs = 0
    today_count = 0
    week_secs = 0
    month_secs = 0
    total_secs = 0
    distinct_dates: list[str] = []
    seen_dates: set[str] = set()
    day_totals: dict[str, int] = {d: 0 for d in week_dates}
    subj_map: dict[str, dict[str, Any]] = {}

    for r in rows:
        secs = int(r["duration_seconds"])
        d = r["date"]
        subj = (r["subject"] or "Study").strip() or "Study"
        total_secs += secs
        if d == today:
            today_secs += secs
            today_count += 1
        if d in week_set:
            week_secs += secs
            day_totals[d] = day_totals.get(d, 0) + secs
        if d.startswith(month_prefix):
            month_secs += secs
        if d not in seen_dates:
            seen_dates.add(d)
            distinct_dates.append(d)

        key = subj.lower()
        if key not in subj_map:
            subj_map[key] = {"subject": subj, "seconds": 0, "sessions": 0}
        subj_map[key]["seconds"] += secs
        subj_map[key]["sessions"] += 1

    if live_focus_secs > 0:
        day_totals[today] = day_totals.get(today, 0) + live_focus_secs
        l_key = live_subj.lower()
        if l_key not in subj_map:
            subj_map[l_key] = {"subject": live_subj, "seconds": 0, "sessions": 0}
        subj_map[l_key]["seconds"] += live_focus_secs

    today_effective = today_secs + live_focus_secs
    week_effective = week_secs + live_focus_secs
    month_effective = month_secs + live_focus_secs
    total_effective = total_secs + live_focus_secs

    by_subject = []
    for entry in sorted(subj_map.values(), key=lambda x: (-x["seconds"], x["subject"].lower())):
        by_subject.append(
            {
                "subject": entry["subject"],
                "seconds": entry["seconds"],
                "minutes": round(entry["seconds"] / 60.0, 1),
                "hours": round(entry["seconds"] / 3600.0, 2),
                "sessions": entry["sessions"],
            }
        )

    subjects_list: list[str] = list(DEFAULT_STUDY_SUBJECTS)
    seen_subj_lows = {s.lower() for s in subjects_list}
    for entry in by_subject:
        if entry["subject"].lower() not in seen_subj_lows:
            seen_subj_lows.add(entry["subject"].lower())
            subjects_list.append(entry["subject"])

    daily_breakdown = [
        {"date": d, "seconds": day_totals[d], "minutes": round(day_totals[d] / 60.0, 1)}
        for d in week_dates
    ]

    return {
        "today_seconds": today_effective,
        "today_logged_seconds": today_secs,
        "live_focus_seconds": live_focus_secs,
        "today_minutes": round(today_effective / 60.0, 1),
        "today_sessions_count": today_count,
        "today_sessions": [_row_to_study_session(r) for r in today_sess_rows],
        "week_seconds": week_effective,
        "week_minutes": round(week_effective / 60.0, 1),
        "week_hours": round(week_effective / 3600.0, 2),
        "month_seconds": month_effective,
        "month_hours": round(month_effective / 3600.0, 2),
        "total_seconds": total_effective,
        "total_hours": round(total_effective / 3600.0, 2),
        "total_sessions": len(rows),
        "streak_days": _calculate_streak(distinct_dates, today),
        "by_subject": by_subject,
        "subjects": subjects_list,
        "daily_breakdown": daily_breakdown,
    }


def _ensure_active_timer_row(conn: sqlite3.Connection) -> sqlite3.Row:
    conn.execute("INSERT OR IGNORE INTO active_timer (id) VALUES (1)")
    return conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()


def _format_timer_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    active = bool(row["active"])
    paused = bool(row["paused"])
    mode = row["mode"] if row["mode"] in TIMER_MODES else "countdown"
    phase = row["phase"] if row["phase"] in TIMER_PHASES else "focus"
    accumulated = int(row["accumulated_seconds"] or 0)
    started_epoch = row["started_at_epoch"]
    now_ep = time.time()

    if active and not paused and started_epoch is not None:
        elapsed = accumulated + max(0, int(now_ep - float(started_epoch)))
    else:
        elapsed = accumulated

    target_secs = int(row["target_seconds"] or 0)
    remaining: int | None = max(0, target_secs - elapsed) if mode == "countdown" else None

    task_title = ""
    if row["task_id"] is not None:
        t_row = conn.execute("SELECT title FROM tasks WHERE id = ?", (row["task_id"],)).fetchone()
        if t_row:
            task_title = t_row["title"]

    note_title = ""
    if row["note_id"] is not None:
        n_row = conn.execute("SELECT title FROM notes WHERE id = ?", (row["note_id"],)).fetchone()
        if n_row:
            note_title = n_row["title"]

    return {
        "active": active,
        "paused": paused,
        "mode": mode,
        "phase": phase,
        "subject": row["subject"] or "Study",
        "topic": row["topic"] or "",
        "task_id": row["task_id"],
        "task_title": task_title,
        "note_id": row["note_id"],
        "note_title": note_title,
        "target_seconds": target_secs,
        "break_seconds": int(row["break_seconds"] or 0),
        "accumulated_seconds": accumulated,
        "started_at_epoch": started_epoch,
        "started_at_iso": row["started_at_iso"],
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "server_epoch": now_ep,
        "notes": row["notes"] or "",
    }


def _reset_timer_conn(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE active_timer
        SET active = 0, paused = 0, phase = 'focus',
            accumulated_seconds = 0, started_at_epoch = NULL, started_at_iso = NULL,
            notes = ''
        WHERE id = 1
        """
    )


def get_timer_state() -> dict[str, Any]:
    with get_conn() as conn:
        row = _ensure_active_timer_row(conn)
        return _format_timer_row(conn, row)


def _match_task_for_study_topic_conn(
    conn: sqlite3.Connection, topic: str, subject: str = ""
) -> int | None:
    """Auto-match an open active task from timer topic/subject when task_id wasn't specified."""
    query_tokens = _tokenize_task_text(f"{topic} {subject}")
    if not query_tokens:
        return None
    rows = conn.execute(
        "SELECT * FROM tasks WHERE list_type = 'daily' AND done = 0 ORDER BY id DESC"
    ).fetchall()
    if not rows:
        return None
    today = today_str()
    best_id: int | None = None
    best_score = 0.0
    for r in rows:
        rk = r.keys()
        t_tokens = _tokenize_task_text(f"{r['title']} {r['category']}")
        overlap = query_tokens & t_tokens
        if not overlap:
            continue
        spec = [tok for tok in overlap if len(tok) >= 5 and tok not in _GENERIC_TOPIC_TOKENS]
        containment = len(overlap) / max(1, min(len(query_tokens), len(t_tokens)))
        score = 0.0
        if len(overlap) >= 2 and containment >= 0.45:
            score = 0.85
        elif spec and containment >= 0.4:
            score = 0.80
        if score >= 0.80:
            is_today = (r["due_date"] == today) or ("parent_task_id" in rk and r["parent_task_id"] is not None)
            if is_today:
                score += 0.05
            if score > best_score:
                best_score = score
                best_id = int(r["id"])
    return best_id


def start_timer_and_autosave(
    mode: str = "countdown",
    duration_minutes: float = 25,
    break_minutes: float = 5,
    subject: str = "Study",
    topic: str = "",
    task_id: int | None = None,
    note_id: int | None = None,
    notes: str = "",
    save_previous: bool = True,
) -> dict[str, Any]:
    """Start a timer, auto-saving any active running focus timer >=1s to today's study log."""
    clean_mode = mode if mode in TIMER_MODES else "countdown"
    target_secs = (
        max(5, int(round(float(duration_minutes or 25) * 60)))
        if clean_mode == "countdown"
        else 0
    )
    break_secs = max(0, int(round(float(break_minutes if break_minutes is not None else 5) * 60)))
    clean_subject = (subject or "Study").strip()[:60] or "Study"
    now_ep = time.time()
    started_iso = now_iso()
    previous_logged: dict[str, Any] | None = None

    with get_conn() as conn:
        prev_row = _ensure_active_timer_row(conn)
        prev_state = _format_timer_row(conn, prev_row)
        if (
            save_previous
            and prev_state["active"]
            and prev_state["phase"] == "focus"
            and prev_state["elapsed_seconds"] >= 1
        ):
            planned = (
                prev_state["target_seconds"]
                if prev_state["mode"] == "countdown" and prev_state["target_seconds"] > 0
                else None
            )
            previous_logged = _insert_study_session_conn(
                conn,
                subject=prev_state["subject"],
                topic=prev_state["topic"],
                duration_seconds=prev_state["elapsed_seconds"],
                mode=prev_state["mode"],
                planned_seconds=planned,
                day_date=today_str(),
                started_at=prev_state["started_at_iso"],
                ended_at=started_iso,
                task_id=prev_state["task_id"],
                note_id=prev_state["note_id"],
                notes=prev_state["notes"],
            )

        valid_task, valid_note = _validate_fk_ids(conn, task_id, note_id)
        if valid_task is None and topic.strip():
            valid_task = _match_task_for_study_topic_conn(conn, topic, clean_subject)

        conn.execute(
            """
            UPDATE active_timer
            SET active = 1, paused = 0, mode = ?, phase = 'focus',
                subject = ?, topic = ?, task_id = ?, note_id = ?,
                target_seconds = ?, break_seconds = ?,
                accumulated_seconds = 0, started_at_epoch = ?, started_at_iso = ?,
                notes = ?
            WHERE id = 1
            """,
            (
                clean_mode,
                clean_subject,
                (topic or "").strip()[:160],
                valid_task,
                valid_note,
                target_secs,
                break_secs,
                now_ep,
                started_iso,
                (notes or "").strip(),
            ),
        )
        row = conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()
        return {
            "state": _format_timer_row(conn, row),
            "previous_logged_session": previous_logged,
        }


def start_timer(
    mode: str = "countdown",
    duration_minutes: float = 25,
    break_minutes: float = 5,
    subject: str = "Study",
    topic: str = "",
    task_id: int | None = None,
    note_id: int | None = None,
    notes: str = "",
    save_previous: bool = True,
) -> dict[str, Any]:
    return start_timer_and_autosave(
        mode=mode,
        duration_minutes=duration_minutes,
        break_minutes=break_minutes,
        subject=subject,
        topic=topic,
        task_id=task_id,
        note_id=note_id,
        notes=notes,
        save_previous=save_previous,
    )["state"]


def pause_timer() -> dict[str, Any]:
    with get_conn() as conn:
        row = _ensure_active_timer_row(conn)
        state = _format_timer_row(conn, row)
        if state["active"] and not state["paused"]:
            conn.execute(
                """
                UPDATE active_timer
                SET paused = 1, accumulated_seconds = ?, started_at_epoch = NULL
                WHERE id = 1
                """,
                (state["elapsed_seconds"],),
            )
        row = conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()
        return _format_timer_row(conn, row)


def resume_timer() -> dict[str, Any]:
    now_ep = time.time()
    with get_conn() as conn:
        row = _ensure_active_timer_row(conn)
        if row["active"] and row["paused"]:
            conn.execute(
                "UPDATE active_timer SET paused = 0, started_at_epoch = ? WHERE id = 1",
                (now_ep,),
            )
        row = conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()
        return _format_timer_row(conn, row)


def update_active_timer_meta(patch: dict[str, Any]) -> dict[str, Any]:
    with get_conn() as conn:
        row = _ensure_active_timer_row(conn)
        subject = (
            (str(patch.get("subject", row["subject"])) or "Study").strip()[:60] or "Study"
        )
        topic = str(patch.get("topic", row["topic"])).strip()[:160]
        raw_task = patch["task_id"] if "task_id" in patch else row["task_id"]
        raw_note = patch["note_id"] if "note_id" in patch else row["note_id"]
        valid_task, valid_note = _validate_fk_ids(conn, raw_task, raw_note)
        notes = str(patch.get("notes", row["notes"])).strip()
        conn.execute(
            """
            UPDATE active_timer
            SET subject = ?, topic = ?, task_id = ?, note_id = ?, notes = ?
            WHERE id = 1
            """,
            (subject, topic, valid_task, valid_note, notes),
        )
        updated = conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()
        return _format_timer_row(conn, updated)


def stop_timer(save_session: bool = True, notes: str | None = None) -> dict[str, Any]:
    with get_conn() as conn:
        row = _ensure_active_timer_row(conn)
        state = _format_timer_row(conn, row)
        logged_session: dict[str, Any] | None = None
        if (
            state["active"]
            and save_session
            and state["phase"] == "focus"
            and state["elapsed_seconds"] >= 1
        ):
            session_notes = (
                notes.strip() if isinstance(notes, str) else state["notes"]
            )
            planned = state["target_seconds"] if state["mode"] == "countdown" and state["target_seconds"] > 0 else None
            logged_session = _insert_study_session_conn(
                conn,
                subject=state["subject"],
                topic=state["topic"],
                duration_seconds=state["elapsed_seconds"],
                mode=state["mode"],
                planned_seconds=planned,
                day_date=today_str(),
                started_at=state["started_at_iso"],
                ended_at=now_iso(),
                task_id=state["task_id"],
                note_id=state["note_id"],
                notes=session_notes,
            )
        _reset_timer_conn(conn)
        updated_row = conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()
        return {
            "state": _format_timer_row(conn, updated_row),
            "logged_session": logged_session,
        }


def complete_countdown_phase(start_break: bool = True) -> dict[str, Any]:
    with get_conn() as conn:
        row = _ensure_active_timer_row(conn)
        state = _format_timer_row(conn, row)
        logged_session: dict[str, Any] | None = None
        if not state["active"]:
            return {"state": state, "logged_session": None}

        if state["phase"] == "focus":
            dur_secs = (
                state["target_seconds"]
                if state["target_seconds"] > 0
                else max(1, state["elapsed_seconds"])
            )
            logged_session = _insert_study_session_conn(
                conn,
                subject=state["subject"],
                topic=state["topic"],
                duration_seconds=dur_secs,
                mode=state["mode"],
                planned_seconds=state["target_seconds"] or None,
                day_date=today_str(),
                started_at=state["started_at_iso"],
                ended_at=now_iso(),
                task_id=state["task_id"],
                note_id=state["note_id"],
                notes=state["notes"],
            )
            if start_break and state["break_seconds"] > 0:
                now_ep = time.time()
                conn.execute(
                    """
                    UPDATE active_timer
                    SET active = 1, paused = 0, mode = 'countdown', phase = 'break',
                        target_seconds = ?, accumulated_seconds = 0,
                        started_at_epoch = ?, started_at_iso = ?
                    WHERE id = 1
                    """,
                    (state["break_seconds"], now_ep, now_iso()),
                )
            else:
                _reset_timer_conn(conn)
        else:
            # Break phase finished — return cleanly to idle without inflating focus hours
            _reset_timer_conn(conn)

        updated_row = conn.execute("SELECT * FROM active_timer WHERE id = 1").fetchone()
        return {
            "state": _format_timer_row(conn, updated_row),
            "logged_session": logged_session,
        }


# ---------------------------------------------------------------------------
# Watchlist & Playlists
# ---------------------------------------------------------------------------

def _row_to_watchlist(row: sqlite3.Row) -> dict[str, Any]:
    row_keys = row.keys()
    return {
        "id": row["id"],
        "title": row["title"],
        "url": row["url"],
        "content_type": row["content_type"],
        "source": row["source"],
        "category": row["category"],
        "status": row["status"],
        "progress_current": int(row["progress_current"] or 0),
        "progress_total": int(row["progress_total"] or 0),
        "tags": _parse_json_list(row["tags"]),
        "notes": row["notes"],
        "note_id": row["note_id"],
        "note_title": (row["note_title"] if "note_title" in row_keys and row["note_title"] else ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


_WATCHLIST_SELECT = """
    SELECT w.*, n.title AS note_title
    FROM watchlist_items w
    LEFT JOIN notes n ON n.id = w.note_id
"""


def list_watchlist(
    status: str | None = None,
    content_type: str | None = None,
    category: str = "",
    query: str = "",
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status in WATCHLIST_STATUSES:
        clauses.append("w.status = ?")
        params.append(status)
    if content_type in WATCHLIST_TYPES:
        clauses.append("w.content_type = ?")
        params.append(content_type)
    if category and category.strip():
        clauses.append("LOWER(w.category) = LOWER(?)")
        params.append(category.strip())
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"{_WATCHLIST_SELECT} {where_sql} "
        "ORDER BY CASE w.status WHEN 'watching' THEN 0 WHEN 'to_watch' THEN 1 ELSE 2 END ASC, "
        "w.updated_at DESC, w.id DESC"
    )
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = [_row_to_watchlist(r) for r in rows]
    q = (query or "").strip().lower()
    if q:
        tokens = [tok for tok in re.split(r"\s+", q) if tok]
        items = [
            it
            for it in items
            if all(
                tok
                in (
                    f"{it['title']} {it['url']} {it['source']} {it['category']} "
                    f"{it['notes']} {' '.join(it['tags'])}"
                ).lower()
                for tok in tokens
            )
        ]
    return items


def get_watchlist_item(item_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            f"{_WATCHLIST_SELECT} WHERE w.id = ?", (item_id,)
        ).fetchone()
        return _row_to_watchlist(row) if row else None


def find_watchlist_by_url_or_title(url: str = "", title: str = "") -> dict[str, Any] | None:
    clean_url = (url or "").strip()
    clean_title = (title or "").strip()
    with get_conn() as conn:
        if clean_url:
            row = conn.execute(
                f"{_WATCHLIST_SELECT} WHERE LOWER(w.url) = LOWER(?) LIMIT 1",
                (clean_url,),
            ).fetchone()
            if row:
                return _row_to_watchlist(row)
        if clean_title:
            row = conn.execute(
                f"{_WATCHLIST_SELECT} WHERE LOWER(w.title) = LOWER(?) LIMIT 1",
                (clean_title,),
            ).fetchone()
            if row:
                return _row_to_watchlist(row)
    return None


def _rollup_watchlist_status(
    current: int, total: int, fallback_status: str
) -> str:
    if total > 0:
        if current >= total:
            return "watched"
        if current > 0:
            return "watching"
        return "to_watch" if fallback_status not in WATCHLIST_STATUSES else fallback_status
    return fallback_status if fallback_status in WATCHLIST_STATUSES else "to_watch"


def create_watchlist_item(
    title: str = "",
    url: str = "",
    content_type: str = "playlist",
    source: str = "",
    category: str = "",
    status: str = "to_watch",
    progress_current: int = 0,
    progress_total: int = 0,
    tags: list[str] | str | None = None,
    notes: str = "",
    note_id: int | None = None,
) -> dict[str, Any]:
    clean_url = (url or "").strip()
    clean_title = (title or "").strip()
    if not clean_title and not clean_url:
        raise ValueError("Provide a title or URL for the watchlist item.")
    if not clean_title:
        clean_title = clean_url[:90]
    ct = content_type if content_type in WATCHLIST_TYPES else "playlist"
    p_total = max(0, int(progress_total or 0))
    p_cur = max(0, int(progress_current or 0))
    if p_total > 0 and p_cur > p_total:
        p_cur = p_total
    st = _rollup_watchlist_status(p_cur, p_total, status if status in WATCHLIST_STATUSES else "to_watch")
    cleaned_tags = _clean_tags(tags)
    ts = now_iso()

    with get_conn() as conn:
        _, valid_note = _validate_fk_ids(conn, None, note_id)
        cur = conn.execute(
            """
            INSERT INTO watchlist_items (
                title, url, content_type, source, category, status,
                progress_current, progress_total, tags, notes, note_id,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_title[:160],
                clean_url[:500],
                ct,
                (source or "").strip()[:60],
                (category or "").strip()[:60],
                st,
                p_cur,
                p_total,
                json.dumps(cleaned_tags),
                (notes or "").strip(),
                valid_note,
                ts,
                ts,
            ),
        )
        row = conn.execute(
            f"{_WATCHLIST_SELECT} WHERE w.id = ?", (cur.lastrowid,)
        ).fetchone()
        return _row_to_watchlist(row)


def update_watchlist_item(item_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT * FROM watchlist_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not existing:
            return None
        title = str(patch.get("title", existing["title"])).strip()
        if not title:
            raise ValueError("Watchlist title cannot be empty.")
        url = str(patch.get("url", existing["url"])).strip()
        ct = patch.get("content_type", existing["content_type"])
        if ct not in WATCHLIST_TYPES:
            ct = existing["content_type"]
        source = str(patch.get("source", existing["source"])).strip()
        category = str(patch.get("category", existing["category"])).strip()

        p_total = max(0, int(patch.get("progress_total", existing["progress_total"]) or 0))
        p_cur = max(0, int(patch.get("progress_current", existing["progress_current"]) or 0))
        if p_total > 0 and p_cur > p_total:
            p_cur = p_total

        if "status" in patch and patch["status"] in WATCHLIST_STATUSES:
            st = patch["status"]
        elif ("progress_current" in patch or "progress_total" in patch) and p_total > 0:
            st = _rollup_watchlist_status(p_cur, p_total, existing["status"])
        else:
            st = existing["status"]

        tags = (
            _clean_tags(patch["tags"])
            if "tags" in patch
            else _parse_json_list(existing["tags"])
        )
        notes = str(patch.get("notes", existing["notes"])).strip()
        raw_note = patch["note_id"] if "note_id" in patch else existing["note_id"]
        _, valid_note = _validate_fk_ids(conn, None, raw_note)

        conn.execute(
            """
            UPDATE watchlist_items
            SET title = ?, url = ?, content_type = ?, source = ?, category = ?,
                status = ?, progress_current = ?, progress_total = ?,
                tags = ?, notes = ?, note_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                title[:160],
                url[:500],
                ct,
                source[:60],
                category[:60],
                st,
                p_cur,
                p_total,
                json.dumps(tags),
                notes,
                valid_note,
                now_iso(),
                item_id,
            ),
        )
        row = conn.execute(
            f"{_WATCHLIST_SELECT} WHERE w.id = ?", (item_id,)
        ).fetchone()
        return _row_to_watchlist(row)


def delete_watchlist_item(item_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM watchlist_items WHERE id = ?", (item_id,))
        return cur.rowcount > 0


def ensure_watchlist_note(item_id: int) -> dict[str, Any] | None:
    """Return the linked structured note for a watchlist item, creating it if missing."""
    item = get_watchlist_item(item_id)
    if not item:
        return None
    if item["note_id"] is not None:
        existing_note = get_note(int(item["note_id"]))
        if existing_note:
            return {"item": item, "note": existing_note, "created": False}

    folder = "Lectures" if item["content_type"] in ("playlist", "course", "video") else "Study"
    tags = _clean_tags([*item["tags"], item["content_type"], item["category"] or "watchlist"])
    url_line = f"- Source: [{item['url']}]({item['url']})" if item["url"] else ""
    overview_lines = [ln for ln in (url_line, item["notes"]) if ln]
    content = (
        "## Overview\n"
        + ("\n".join(overview_lines) if overview_lines else f"Tracking {item['content_type']} notes.")
        + "\n\n## Key Notes\n- \n\n## To Research / Follow-Up\n- [ ] Review main takeaways\n"
    )
    summary = (
        f"Notes for {item['content_type']}: {item['title']}"
        + (f" ({item['source']})" if item["source"] else "")
    )
    new_note = create_note(
        title=item["title"],
        content=content,
        folder=folder,
        summary=summary,
        tags=tags,
    )
    updated_item = update_watchlist_item(item_id, {"note_id": new_note["id"]}) or item
    return {"item": updated_item, "note": new_note, "created": True}
