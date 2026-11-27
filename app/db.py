"""SQLite persistence layer for Personal Life Hub."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date as dt_date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app.config import get_db_path, get_media_dir

TASK_LIST_TYPES = ("daily", "want_to_do")
TASK_PRIORITIES = ("normal", "high")
TASK_STATUSES = ("not_started", "partially_completed", "completed")
DEFAULT_NOTE_FOLDERS = ("Lectures", "Projects", "Study", "Personal", "Ideas")
NOTE_SORTS = ("updated", "created", "title")
ACTIVITY_DOMAINS = ("gym", "art")
ART_STATUSES = ("idea", "wip", "finished")
GAME_STATUSES = ("playing", "want_to_play", "finished", "on_hold")


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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")

    note_cols = {r["name"] for r in conn.execute("PRAGMA table_info(notes)").fetchall()}
    if "folder" not in note_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN folder TEXT NOT NULL DEFAULT ''")
    if "summary" not in note_cols:
        conn.execute("ALTER TABLE notes ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_folder ON notes(folder COLLATE NOCASE);")


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


def _row_to_task(
    row: sqlite3.Row,
    checklist_items: list[dict[str, Any]] | None = None,
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
    return {
        "id": row["id"],
        "title": row["title"],
        "list_type": row["list_type"],
        "category": row["category"],
        "priority": row["priority"],
        "start_date": start_date,
        "due_date": row["due_date"],
        "timeline": timeline,
        "frequency": frequency,
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


def _recompute_task_rollup(conn: sqlite3.Connection, task_id: int) -> None:
    rows = conn.execute(
        "SELECT done FROM task_items WHERE task_id = ?", (task_id,)
    ).fetchall()
    if not rows:
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
        items_map = _fetch_task_items_map(conn, [task_id])
        return _row_to_task(row, items_map.get(task_id, []))


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
        items_map = _fetch_task_items_map(conn, [r["id"] for r in rows])
        return [_row_to_task(r, items_map.get(r["id"], [])) for r in rows]


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
) -> dict[str, Any]:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Task title cannot be empty.")
    lt = list_type if list_type in TASK_LIST_TYPES else "daily"
    pr = priority if priority in TASK_PRIORITIES else "normal"
    st = status if status in TASK_STATUSES else "not_started"
    done_int = 1 if st == "completed" else 0
    completed_at = now_iso() if st == "completed" else None
    start = start_date.strip() if isinstance(start_date, str) and start_date.strip() else None
    due = due_date.strip() if isinstance(due_date, str) and due_date.strip() else None
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO tasks (title, list_type, category, priority, start_date, due_date, timeline, frequency, done, status, progress_note, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clean_title,
                lt,
                category.strip(),
                pr,
                start,
                due,
                timeline.strip(),
                frequency.strip(),
                done_int,
                st,
                progress_note.strip(),
                ts,
                completed_at,
            ),
        )
        task_id = int(cur.lastrowid)
        if checklist_items:
            for pos, text in enumerate(checklist_items):
                clean_item = str(text).strip()
                if clean_item:
                    conn.execute(
                        "INSERT INTO task_items (task_id, text, done, position) VALUES (?, ?, 0, ?)",
                        (task_id, clean_item, pos),
                    )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        items_map = _fetch_task_items_map(conn, [task_id])
        return _row_to_task(row, items_map.get(task_id, []))


def update_task(task_id: int, patch: dict[str, Any]) -> dict[str, Any] | None:
    with get_conn() as conn:
        existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing:
            return None
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

        existing_status = (
            existing["status"]
            if existing["status"] in TASK_STATUSES
            else ("completed" if existing["done"] else "not_started")
        )
        completed_at = existing["completed_at"]

        if "status" in patch and patch["status"] in TASK_STATUSES:
            status = patch["status"]
            done = status == "completed"
            completed_at = now_iso() if done else None
        elif "done" in patch:
            done = bool(patch["done"])
            status = "completed" if done else "not_started"
            completed_at = now_iso() if done else None
        else:
            status = existing_status
            done = bool(existing["done"])

        conn.execute(
            """
            UPDATE tasks
            SET title = ?, list_type = ?, category = ?, priority = ?,
                start_date = ?, due_date = ?, timeline = ?, frequency = ?,
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
                int(done),
                status,
                progress_note,
                completed_at,
                task_id,
            ),
        )
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        items_map = _fetch_task_items_map(conn, [task_id])
        return _row_to_task(row, items_map.get(task_id, []))


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
        items_map = _fetch_task_items_map(conn, [task_id])
        return _row_to_task(row, items_map.get(task_id, []))


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
        items_map = _fetch_task_items_map(conn, [task_id])
        return _row_to_task(row, items_map.get(task_id, []))


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
        items_map = _fetch_task_items_map(conn, [task_id])
        return _row_to_task(row, items_map.get(task_id, []))


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
        existing_task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not existing_task:
            return None

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
        items_map = _fetch_task_items_map(conn, [task_id])
        return _row_to_task(row, items_map.get(task_id, []))


def delete_task(task_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def _clean_folder(folder: str | None) -> str:
    return (folder or "").strip()[:60]


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
    notes = [_row_to_note(r) for r in rows]

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
        return _row_to_note(row) if row else None


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
    return {
        "exported_at": now_iso(),
        "tasks": list_tasks(include_done=True),
        "notes": list_notes(),
        "gym_activity": get_activity_summary("gym"),
        "art_activity": get_activity_summary("art"),
        "gym_logs": list_gym_logs(limit=500),
        "art_items": list_art_items(),
        "games": list_games(),
        "captures": list_captures(limit=100),
        "agent_sessions": list_agent_sessions(limit=50),
    }
