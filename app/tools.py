"""Pure-Python tool registry and execution handlers for the Personal Life Hub agent."""

from __future__ import annotations

import json
from typing import Any

from app import cloud_sync, db, notes_sync, watchlist
from app.config import load_settings

ALLOWED_VIEWS = ("hub", "tasks", "notes", "timer", "watchlist", "gym", "art", "gaming", "ask", "agent", "settings")

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_today_overview",
            "description": "Get today's date, gym/art status, open daily tasks, want-to-do items, recent notes, and active games/art.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "List tasks and their nested checklist items. Use list_type='daily' for Active/Scheduled Tasks (today, next N days, meetings, weekly goals) and 'want_to_do' for unscheduled bucket-list ideas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_type": {
                        "type": "string",
                        "enum": ["daily", "want_to_do"],
                        "description": "'daily' = Active/Scheduled Tasks, 'want_to_do' = Unscheduled Someday list",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["not_started", "partially_completed", "completed"],
                    },
                    "query": {"type": "string", "description": "Optional substring filter on task or checklist text."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create one Active Task (list_type='daily', default for any task with a timeline/deadline/plan) or Want-to-Do item, with timeline, frequency, due_date, and optional checklist sub-items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "list_type": {
                        "type": "string",
                        "enum": ["daily", "want_to_do"],
                        "description": "Use 'daily' for Active/Scheduled tasks (including multi-day or 10-day plans). Use 'want_to_do' ONLY for unscheduled someday ideas.",
                    },
                    "checklist_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of concrete checkable steps inside the task.",
                    },
                    "priority": {"type": "string", "enum": ["normal", "high"]},
                    "category": {"type": "string"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD or null"},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD deadline or null"},
                    "timeline": {
                        "type": "string",
                        "description": "Human-readable timeframe e.g. 'Next 10 days' or 'Before Ericsson meeting (10 days)'",
                    },
                    "frequency": {
                        "type": "string",
                        "description": "Target cadence/volume e.g. '4x / week', '1 hour / day', '3 artworks in 10 days'",
                    },
                    "parent_task_id": {
                        "type": "integer",
                        "description": "ID of a larger/recurring parent goal if this today task belongs under it. Omit to auto-detect.",
                    },
                    "is_recurring": {
                        "type": "boolean",
                        "description": "True for daily repeating habits/goals (e.g. LeetCode 1h a day for 10 days).",
                    },
                    "target_days": {
                        "type": "integer",
                        "description": "Total target days for a multi-day daily goal (e.g. 10 for 'next 10 days').",
                    },
                    "daily_target_minutes": {
                        "type": "integer",
                        "description": "Daily target minutes if recurring daily (e.g. 60 for '1 hour a day').",
                    },
                    "progress_mode": {
                        "type": "string",
                        "enum": ["auto", "percent", "days", "checklist"],
                        "description": "Use 'days' for daily countable habits, 'percent' for non-countable progress bars, 'checklist' for explicit steps.",
                    },
                    "progress_percent": {"type": "integer"},
                    "auto_link_parent": {"type": "boolean"},
                    "status": {
                        "type": "string",
                        "enum": ["not_started", "partially_completed", "completed"],
                    },
                    "progress_note": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_tasks_batch",
            "description": "Create multiple separate Active Tasks in one call. Preferred whenever the user speaks a multi-item task list or 10-day plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "list_type": {"type": "string", "enum": ["daily", "want_to_do"]},
                                "checklist_items": {"type": "array", "items": {"type": "string"}},
                                "priority": {"type": "string", "enum": ["normal", "high"]},
                                "category": {"type": "string"},
                                "start_date": {"type": "string"},
                                "due_date": {"type": "string"},
                                "timeline": {"type": "string"},
                                "frequency": {"type": "string"},
                                "parent_task_id": {"type": "integer"},
                                "is_recurring": {"type": "boolean"},
                                "target_days": {"type": "integer"},
                                "daily_target_minutes": {"type": "integer"},
                                "progress_mode": {"type": "string", "enum": ["auto", "percent", "days", "checklist"]},
                                "progress_percent": {"type": "integer"},
                                "auto_link_parent": {"type": "boolean"},
                                "progress_note": {"type": "string"},
                            },
                            "required": ["title"],
                        },
                    }
                },
                "required": ["tasks"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_task",
            "description": "Update task status, progress bar percentage/updates, connected task notes, parent link, or mark a daily recurring goal done today.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["not_started", "partially_completed", "completed"],
                    },
                    "mark_done_today": {
                        "type": "boolean",
                        "description": "Set true to log today's completion on a daily recurring goal (instead of closing all future days).",
                    },
                    "progress_mode": {"type": "string", "enum": ["auto", "percent", "days", "checklist"]},
                    "progress_percent": {
                        "type": "integer",
                        "description": "Overall completion percentage (0..100) for non-countable progress-bar tasks.",
                    },
                    "progress_update": {
                        "type": "string",
                        "description": "Log an improvement/progress entry on a progress-bar task (persisted to progress history).",
                    },
                    "task_note": {
                        "type": "string",
                        "description": "Append a note directly into this task's connected Markdown note.",
                    },
                    "list_type": {"type": "string", "enum": ["daily", "want_to_do"]},
                    "priority": {"type": "string", "enum": ["normal", "high"]},
                    "category": {"type": "string"},
                    "start_date": {"type": "string"},
                    "due_date": {"type": "string"},
                    "timeline": {"type": "string"},
                    "frequency": {"type": "string"},
                    "parent_task_id": {
                        "type": "integer",
                        "description": "Link this task under a larger parent goal (use 0 to unlink).",
                    },
                    "is_recurring": {"type": "boolean"},
                    "target_days": {"type": "integer"},
                    "daily_target_minutes": {"type": "integer"},
                    "progress_note": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_task_checklist",
            "description": "Add, check off, uncheck, or remove sub-items inside a task's checklist. Automatically updates parent task status to partially_completed or completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "add_items": {"type": "array", "items": {"type": "string"}},
                    "check_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Item IDs or substring matches to mark done.",
                    },
                    "uncheck_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Item IDs or substring matches to mark not done.",
                    },
                    "remove_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Item IDs or substring matches to delete.",
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Search notes by keyword query, folder/notebook, pinned state, and/or tag. Use before appending to or organizing a note if note_id is not yet known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "tag": {"type": "string"},
                    "folder": {"type": "string", "description": "Optional notebook/folder filter (e.g. Lectures, Projects, Study, Personal, Ideas)."},
                    "pinned_only": {"type": "boolean"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_note",
            "description": "Fetch full note content, folder, summary, tags, and interactive checklist stats by note_id.",
            "parameters": {
                "type": "object",
                "properties": {"note_id": {"type": "integer"}},
                "required": ["note_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_note",
            "description": "Create a well-structured Markdown note (using ## headings, bullets, > [!NOTE] callouts, and - [ ] follow-ups) with folder, 1-2 sentence summary, tags, and optional checklist items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "folder": {"type": "string", "description": "Notebook folder, e.g. 'Lectures', 'Projects', 'Study', 'Personal', 'Ideas', or custom name."},
                    "summary": {"type": "string", "description": "Concise 1-2 sentence TL;DR summary of the note."},
                    "content": {"type": "string", "description": "Structured Markdown content with ## headings and bullets."},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "checklist_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional action/research checklist lines appended as `- [ ] item`.",
                    },
                    "pinned": {"type": "boolean"},
                    "save_to_folder": {"type": "boolean"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_note",
            "description": "Rewrite or update an existing note's title, structured Markdown content, folder, summary, tags, or pinned status (ideal for organizing raw/messy notes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "content": {"type": "string", "description": "Cleaned/organized Markdown content."},
                    "folder": {"type": "string"},
                    "summary": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "pinned": {"type": "boolean"},
                },
                "required": ["note_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_note",
            "description": "Append structured text (optionally under a new ## heading), checklist items, folder, or tags to an existing note or a task's connected note (pass note_id or task_id).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer"},
                    "task_id": {"type": "integer", "description": "Optional task_id to append directly to that task's connected note."},
                    "heading": {"type": "string", "description": "Optional Markdown section heading above the appended block."},
                    "text": {"type": "string"},
                    "checklist_items": {"type": "array", "items": {"type": "string"}},
                    "add_tags": {"type": "array", "items": {"type": "string"}},
                    "folder": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_note_checklist",
            "description": "Check off or uncheck `- [ ]` checklist lines inside a note by substring match.",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer"},
                    "check_items": {"type": "array", "items": {"type": "string"}},
                    "uncheck_items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["note_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_gym_day",
            "description": "Mark a date (defaults to today) as a day you went to the gym.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "YYYY-MM-DD or null for today"},
                    "note": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_gym_exercise",
            "description": "Log a gym exercise (sets, reps, weight). Also marks that date as a Gym Day automatically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise": {"type": "string"},
                    "sets": {"type": "integer"},
                    "reps": {"type": "integer"},
                    "weight": {"type": "number"},
                    "unit": {"type": "string", "enum": ["kg", "lbs"]},
                    "date": {"type": "string", "description": "YYYY-MM-DD or null for today"},
                    "notes": {"type": "string"},
                },
                "required": ["exercise"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_gym_stats",
            "description": "Fetch recent gym logs, monthly gym days count, streak, and Personal Bests per exercise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "exercise": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upsert_art",
            "description": "Mark an Art Day (drew today) and/or create or update an artwork idea, WIP, or finished piece.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "status": {"type": "string", "enum": ["idea", "wip", "finished"]},
                    "notes": {"type": "string"},
                    "mark_art_day": {"type": "boolean"},
                    "date": {"type": "string", "description": "YYYY-MM-DD or null for today"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upsert_game",
            "description": "Create or update a game in the Gaming tracker (status, platform, rating 1-10, session notes).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["playing", "want_to_play", "finished", "on_hold"],
                    },
                    "platform": {"type": "string"},
                    "rating": {"type": "integer"},
                    "notes": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_study_timer",
            "description": (
                "Control the live Focus/Study Timer (start countdown/Pomodoro or stopwatch, pause, resume, "
                "stop & save session, or reset). Can link an Active Task ID or Note ID."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["start", "pause", "resume", "stop", "reset"],
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["countdown", "stopwatch"],
                        "description": "'countdown' (Pomodoro/timed block) or 'stopwatch' (count up).",
                    },
                    "duration_minutes": {
                        "type": "number",
                        "description": "Focus duration in minutes for countdown mode (default 25).",
                    },
                    "break_minutes": {
                        "type": "number",
                        "description": "Break duration in minutes after countdown completes (default 5).",
                    },
                    "subject": {"type": "string", "description": "Subject tag e.g. Study, Lectures, Coding, Art."},
                    "topic": {"type": "string", "description": "Optional specific topic being studied."},
                    "task_id": {"type": "integer", "description": "Optional Active Task ID to link."},
                    "note_id": {"type": "integer", "description": "Optional Note ID to link."},
                    "notes": {"type": "string", "description": "Optional session reflection/notes when starting or stopping."},
                    "save_session": {
                        "type": "boolean",
                        "description": "For action='stop': whether to save elapsed time as a logged study session (default true).",
                    },
                    "open_timer_view": {
                        "type": "boolean",
                        "description": "Set true to also switch the UI to the Focus Timer screen.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "log_study_session",
            "description": "Log a completed past study/focus session (e.g. 'studied ML for 90 minutes'), optionally linked to a task or note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "e.g. Study, Lectures, Coding, Reading, Research, Art."},
                    "topic": {"type": "string"},
                    "duration_minutes": {"type": "number", "description": "Session length in minutes (> 0)."},
                    "date": {"type": "string", "description": "YYYY-MM-DD or null for today."},
                    "task_id": {"type": "integer"},
                    "note_id": {"type": "integer"},
                    "notes": {"type": "string"},
                },
                "required": ["duration_minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_study_stats",
            "description": "Fetch live timer status, today/week/total focus hours, study streak, breakdown by subject, and recent sessions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "upsert_watchlist_item",
            "description": (
                "Save or update a playlist, video, course, podcast, or article URL/title in the Watchlist tab. "
                "ONLY call when the user explicitly asks to add, save, or update an item in their Watchlist / Playlists tab "
                "(or provides a link to save). NEVER call merely because the user says they will watch or want to watch a lecture, video, course, or show — create a task instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string", "description": "Optional http(s) link to the playlist/video/course."},
                    "content_type": {
                        "type": "string",
                        "enum": ["playlist", "video", "course", "podcast", "article"],
                    },
                    "category": {"type": "string", "description": "e.g. AI, Coding, Art, Gaming, Lecture."},
                    "status": {
                        "type": "string",
                        "enum": ["to_watch", "watching", "watched"],
                    },
                    "progress_current": {"type": "integer"},
                    "progress_total": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_watchlist",
            "description": "List saved playlists, videos, courses, podcasts, or articles in the Watchlist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["to_watch", "watching", "watched"]},
                    "content_type": {"type": "string", "enum": ["playlist", "video", "course", "podcast", "article"]},
                    "category": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate_ui",
            "description": "Switch the desktop app screen to a specific view when the user asks to open or show a section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {
                        "type": "string",
                        "enum": ["hub", "tasks", "notes", "timer", "watchlist", "gym", "art", "gaming", "agent", "settings"],
                    }
                },
                "required": ["view"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_item",
            "description": "Delete a task, note, gym log, art item, game, study_session, or watchlist item. Only set confirmed=true after explicit confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "enum": ["task", "note", "gym_log", "art", "game", "study_session", "watchlist"],
                    },
                    "item_id": {"type": "integer"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["domain", "item_id"],
            },
        },
    },
]


def _enable_nullable_optional_params(schemas: list[dict[str, Any]]) -> None:
    """Allow JSON null on optional non-enum parameters so strict validators (e.g. gpt-oss-20b) never fail with 400."""

    def _patch_object(obj: dict[str, Any]) -> None:
        props = obj.get("properties")
        if not isinstance(props, dict):
            return
        req = set(obj.get("required") or [])
        for pname, pdef in props.items():
            if not isinstance(pdef, dict):
                continue
            if (
                pdef.get("type") == "array"
                and isinstance(pdef.get("items"), dict)
                and pdef["items"].get("type") == "object"
            ):
                _patch_object(pdef["items"])
            elif pdef.get("type") == "object":
                _patch_object(pdef)
            if pname not in req and isinstance(pdef.get("type"), str) and "enum" not in pdef:
                pdef["type"] = [pdef["type"], "null"]

    for entry in schemas:
        fn = entry.get("function") or {}
        params = fn.get("parameters")
        if isinstance(params, dict):
            _patch_object(params)


_enable_nullable_optional_params(TOOL_SCHEMAS)


def _coerce_int(val: Any, default: int | None = None) -> int | None:
    if val is None or val == "" or str(val).lower() == "null":
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _coerce_float(val: Any, default: float | None = None) -> float | None:
    if val is None or val == "" or str(val).lower() == "null":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _coerce_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off", ""):
            return False
    return bool(val)


def _coerce_str_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except json.JSONDecodeError:
                pass
        return [part.strip() for part in s.split(",") if part.strip()]
    return [str(val).strip()]


def _sync_note_if_needed(note: dict[str, Any]) -> dict[str, Any]:
    if note.get("source_path"):
        try:
            return notes_sync.write_note_to_source(note)
        except OSError:
            pass
    return note


def _clean_opt_date(val: Any) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.lower() == "null":
        return None
    return s


def _create_single_task_from_args(args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    title = str(args.get("title") or "").strip()
    checklist = _coerce_str_list(args.get("checklist_items"))
    item = db.create_task(
        title=title,
        list_type=str(args.get("list_type") or "daily"),
        category=str(args.get("category") or ""),
        priority=str(args.get("priority") or "normal"),
        due_date=_clean_opt_date(args.get("due_date")),
        status=str(args.get("status") or "not_started"),
        progress_note=str(args.get("progress_note") or ""),
        checklist_items=checklist,
        start_date=_clean_opt_date(args.get("start_date")),
        timeline=str(args.get("timeline") or ""),
        frequency=str(args.get("frequency") or ""),
        parent_task_id=_coerce_int(args.get("parent_task_id")),
        is_recurring=_coerce_bool(args.get("is_recurring"), False) if args.get("is_recurring") is not None else None,
        target_days=_coerce_int(args.get("target_days")),
        daily_target_minutes=_coerce_int(args.get("daily_target_minutes")),
        progress_mode=str(args.get("progress_mode") or "auto").strip().lower(),
        progress_percent=_coerce_int(args.get("progress_percent")),
        auto_link_parent=_coerce_bool(args.get("auto_link_parent"), True),
    )
    meta_parts: list[str] = []
    if item.get("parent_task_title"):
        meta_parts.append(f"↳ {item['parent_task_title']}")
    if item["is_recurring"]:
        meta_parts.append(f"{item['target_days']}d habit" if item["target_days"] else "Daily")
    if item["checklist_total"]:
        meta_parts.append(f"{item['checklist_total']} steps")
    if item["frequency"]:
        meta_parts.append(item["frequency"])
    if item["due_date"]:
        meta_parts.append(f"Due {item['due_date']}")
    elif item["timeline"]:
        meta_parts.append(item["timeline"])
    suffix = f" ({' · '.join(meta_parts)})" if meta_parts else ""
    prefix = "Task" if item["list_type"] == "daily" else "Want to do"
    badge = {
        "tool": "create_task",
        "domain": "task",
        "item_id": item["id"],
        "label": f"{prefix}: {item['title']}{suffix}",
    }
    return item, badge


def _parent_update_badges(parent_updates: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    badges: list[dict[str, Any]] = []
    for pu in parent_updates or []:
        parts = []
        if pu.get("checked_item"):
            parts.append(f"checked '{pu['checked_item']}'")
        if pu.get("done_today"):
            if pu.get("target_days"):
                parts.append(f"Done Today · {pu['days_completed']}/{pu['target_days']} days")
            else:
                parts.append(f"Done Today · {pu['days_completed']}d total")
        detail = f" ({', '.join(parts)})" if parts else ""
        badges.append(
            {
                "tool": "update_task",
                "domain": "task",
                "item_id": pu["parent_task_id"],
                "label": f"Goal Updated: {pu['parent_title']}{detail}",
            }
        )
    return badges


def execute_tool(name: str, raw_args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute an agent tool call locally and return structured tool result + UI metadata."""
    args = raw_args if isinstance(raw_args, dict) else {}
    settings = load_settings()
    default_unit = settings.get("weight_unit", "kg")

    try:
        if name == "get_today_overview":
            return {"result": db.get_dashboard(), "action_badge": None, "navigate_to": None}

        if name == "list_tasks":
            lt = args.get("list_type")
            st = args.get("status")
            q = str(args.get("query") or "").strip().lower()
            items = db.list_tasks(
                list_type=lt if lt in db.TASK_LIST_TYPES else None,
                include_done=True,
                status=st if st in db.TASK_STATUSES else None,
            )
            if q:
                items = [
                    t
                    for t in items
                    if q in t["title"].lower()
                    or q in t["category"].lower()
                    or q in t["timeline"].lower()
                    or q in t["frequency"].lower()
                    or q in t["progress_note"].lower()
                    or any(q in sub["text"].lower() for sub in t["checklist_items"])
                ]
            return {"result": {"tasks": items[:30]}, "action_badge": None, "navigate_to": None}

        if name == "create_task":
            item, badge = _create_single_task_from_args(args)
            badges = [badge, *_parent_update_badges(item.get("parent_updates"))]
            res_payload: dict[str, Any] = {"task": item}
            if item.get("possible_parents"):
                res_payload["needs_parent_clarification"] = True
                res_payload["possible_parents"] = item["possible_parents"]
            return {
                "result": res_payload,
                "action_badge": badge,
                "action_badges": badges,
                "navigate_to": None,
            }

        if name == "create_tasks_batch":
            raw_list = args.get("tasks")
            if not isinstance(raw_list, list) or not raw_list:
                return {"result": {"error": "tasks array is required."}, "action_badge": None, "navigate_to": None}
            created: list[dict[str, Any]] = []
            badges: list[dict[str, Any]] = []
            clarifications: list[dict[str, Any]] = []
            for entry in raw_list:
                if not isinstance(entry, dict) or not str(entry.get("title") or "").strip():
                    continue
                item, badge = _create_single_task_from_args(entry)
                created.append(item)
                badges.append(badge)
                badges.extend(_parent_update_badges(item.get("parent_updates")))
                if item.get("possible_parents"):
                    clarifications.append(
                        {
                            "task_id": item["id"],
                            "task_title": item["title"],
                            "possible_parents": item["possible_parents"],
                        }
                    )
            batch_res: dict[str, Any] = {"tasks": created, "count": len(created)}
            if clarifications:
                batch_res["needs_parent_clarification"] = True
                batch_res["clarification_candidates"] = clarifications
            return {
                "result": batch_res,
                "action_badge": badges[0] if badges else None,
                "action_badges": badges,
                "navigate_to": None,
            }

        if name == "update_task":
            task_id = _coerce_int(args.get("task_id"))
            if task_id is None:
                return {"result": {"error": "task_id is required."}, "action_badge": None, "navigate_to": None}
            if args.get("mark_done_today") is not None:
                want_dt = _coerce_bool(args.get("mark_done_today"), True)
                item = db.toggle_task_done_today(task_id, done=want_dt)
                if not item:
                    return {"result": {"error": f"Task #{task_id} not found."}, "action_badge": None, "navigate_to": None}
                days_sfx = (
                    f"{item['days_completed']}/{item['target_days']} days"
                    if item["target_days"]
                    else f"{item['days_completed']}d"
                )
                badges = [
                    {
                        "tool": name,
                        "domain": "task",
                        "item_id": item["id"],
                        "label": f"{'Done Today' if item['done_today'] else 'Unmarked Today'}: {item['title']} ({days_sfx})",
                    }
                ]
                task_note_txt = str(args.get("task_note") or "").strip()
                if task_note_txt:
                    nr = db.append_task_note(task_id, text=task_note_txt)
                    if nr and nr.get("note"):
                        item = nr["task"]
                        badges.append(
                            {
                                "tool": name,
                                "domain": "note",
                                "item_id": nr["note"]["id"],
                                "label": f"Task Note: {nr['note']['title']}",
                            }
                        )
                return {
                    "result": {"task": item},
                    "action_badge": badges[0],
                    "action_badges": badges,
                    "navigate_to": None,
                }
            patch: dict[str, Any] = {}
            for key in (
                "title",
                "status",
                "list_type",
                "priority",
                "category",
                "start_date",
                "due_date",
                "timeline",
                "frequency",
                "progress_note",
            ):
                if key in args and args[key] is not None:
                    val = args[key]
                    if key in ("start_date", "due_date"):
                        val = _clean_opt_date(val)
                    patch[key] = val
            if "parent_task_id" in args and args["parent_task_id"] is not None:
                pid_val = _coerce_int(args["parent_task_id"])
                patch["parent_task_id"] = pid_val if pid_val and pid_val > 0 else None
            if "is_recurring" in args and args["is_recurring"] is not None:
                patch["is_recurring"] = _coerce_bool(args["is_recurring"], False)
            if "target_days" in args and args["target_days"] is not None:
                patch["target_days"] = _coerce_int(args["target_days"], 0) or 0
            if "daily_target_minutes" in args and args["daily_target_minutes"] is not None:
                patch["daily_target_minutes"] = _coerce_int(args["daily_target_minutes"], 0) or 0
            if "progress_mode" in args and args["progress_mode"] is not None:
                patch["progress_mode"] = str(args["progress_mode"]).strip().lower()

            has_prog_log = bool(str(args.get("progress_update") or "").strip()) or (
                args.get("progress_percent") is not None and "status" not in patch
            )
            if not has_prog_log and args.get("progress_percent") is not None:
                patch["progress_percent"] = _coerce_int(args["progress_percent"], 0) or 0

            item = db.get_task(task_id)
            if not item:
                return {"result": {"error": f"Task #{task_id} not found."}, "action_badge": None, "navigate_to": None}
            all_parent_updates: list[dict[str, Any]] = []
            if patch:
                item = db.update_task(task_id, patch) or item
                all_parent_updates.extend(item.get("parent_updates") or [])
            if has_prog_log:
                pct_val = _coerce_int(args.get("progress_percent")) if args.get("progress_percent") is not None else None
                item = db.record_task_progress(
                    task_id,
                    progress_percent=pct_val,
                    note=str(args.get("progress_update") or ""),
                ) or item
                all_parent_updates.extend(item.get("parent_updates") or [])

            prog_sfx = (
                f" · {item['progress_percent']}%"
                if item.get("effective_progress_mode") == "percent"
                else ""
            )
            main_badge = {
                "tool": name,
                "domain": "task",
                "item_id": item["id"],
                "label": f"Task ({item['status'].replace('_', ' ')}{prog_sfx}): {item['title']}",
            }
            badges = [main_badge, *_parent_update_badges(all_parent_updates)]

            task_note_txt = str(args.get("task_note") or "").strip()
            linked_note = None
            if task_note_txt:
                nr = db.append_task_note(task_id, text=task_note_txt)
                if nr and nr.get("note"):
                    item = nr["task"]
                    linked_note = nr["note"]
                    badges.append(
                        {
                            "tool": name,
                            "domain": "note",
                            "item_id": linked_note["id"],
                            "label": f"Task Note: {linked_note['title']}",
                        }
                    )
            res_obj: dict[str, Any] = {"task": item}
            if linked_note:
                res_obj["note"] = linked_note
            return {
                "result": res_obj,
                "action_badge": main_badge,
                "action_badges": badges,
                "navigate_to": None,
            }

        if name == "manage_task_checklist":
            task_id = _coerce_int(args.get("task_id"))
            if task_id is None:
                return {"result": {"error": "task_id is required."}, "action_badge": None, "navigate_to": None}
            item = db.bulk_manage_task_items(
                task_id=task_id,
                add_items=_coerce_str_list(args.get("add_items")),
                check_items=_coerce_str_list(args.get("check_items")),
                uncheck_items=_coerce_str_list(args.get("uncheck_items")),
                remove_items=_coerce_str_list(args.get("remove_items")),
            )
            if not item:
                return {"result": {"error": f"Task #{task_id} not found."}, "action_badge": None, "navigate_to": None}
            main_badge = {
                "tool": name,
                "domain": "task",
                "item_id": item["id"],
                "label": (
                    f"Checklist: {item['title']} "
                    f"({item['checklist_done']}/{item['checklist_total']} · {item['status'].replace('_', ' ')})"
                ),
            }
            badges = [main_badge, *_parent_update_badges(item.get("parent_updates"))]
            return {
                "result": {"task": item},
                "action_badge": main_badge,
                "action_badges": badges,
                "navigate_to": None,
            }

        if name == "search_notes":
            items = db.list_notes(
                query=str(args.get("query") or ""),
                tag=str(args.get("tag") or ""),
                folder=str(args.get("folder") or ""),
                pinned_only=_coerce_bool(args.get("pinned_only"), False),
            )
            compact = [
                {
                    "id": n["id"],
                    "title": n["title"],
                    "folder": n["folder"],
                    "summary": n["summary"],
                    "tags": n["tags"],
                    "checklist": n["checklist"],
                    "snippet": n["content"][:280],
                    "updated_at": n["updated_at"],
                }
                for n in items[:20]
            ]
            return {"result": {"notes": compact}, "action_badge": None, "navigate_to": None}

        if name == "get_note":
            note_id = _coerce_int(args.get("note_id"))
            note = db.get_note(note_id) if note_id is not None else None
            if not note:
                return {"result": {"error": f"Note #{note_id} not found."}, "action_badge": None, "navigate_to": None}
            return {"result": {"note": note}, "action_badge": None, "navigate_to": None}

        if name == "create_note":
            title = str(args.get("title") or "").strip()
            content = str(args.get("content") or "").strip()
            folder = str(args.get("folder") or "").strip()
            summary = str(args.get("summary") or "").strip()
            tags = _coerce_str_list(args.get("tags"))
            checklist = _coerce_str_list(args.get("checklist_items"))
            if checklist:
                lines = "\n".join(f"- [ ] {c}" for c in checklist)
                content = f"{content}\n\n{lines}".strip() if content else lines
            if not title and not content:
                return {"result": {"error": "Provide title or content for note."}, "action_badge": None, "navigate_to": None}
            note = db.create_note(
                title=title,
                content=content,
                tags=tags,
                pinned=_coerce_bool(args.get("pinned"), False),
                folder=folder,
                summary=summary,
            )
            if _coerce_bool(args.get("save_to_folder"), False):
                folder_cfg = settings.get("notes_folder", "").strip()
                if folder_cfg:
                    try:
                        note = notes_sync.save_new_note_to_folder(note, folder_cfg)
                    except OSError:
                        pass
            folder_lbl = f" ({note['folder']})" if note.get("folder") else ""
            tag_label = f" [{', '.join('#' + t for t in note['tags'])}]" if note["tags"] else ""
            return {
                "result": {"note": note},
                "action_badge": {
                    "tool": name,
                    "domain": "note",
                    "item_id": note["id"],
                    "label": f"Note{folder_lbl}: {note['title']}{tag_label}",
                },
                "navigate_to": None,
            }

        if name == "update_note":
            note_id = _coerce_int(args.get("note_id"))
            if note_id is None:
                return {"result": {"error": "note_id is required."}, "action_badge": None, "navigate_to": None}
            patch: dict[str, Any] = {}
            for key in ("title", "content", "folder", "summary"):
                if key in args and args[key] is not None:
                    patch[key] = str(args[key])
            if "tags" in args and args["tags"] is not None:
                patch["tags"] = _coerce_str_list(args["tags"])
            if "pinned" in args and args["pinned"] is not None:
                patch["pinned"] = _coerce_bool(args["pinned"])
            note = db.update_note(note_id, patch)
            if not note:
                return {"result": {"error": f"Note #{note_id} not found."}, "action_badge": None, "navigate_to": None}
            note = _sync_note_if_needed(note)
            return {
                "result": {"note": note},
                "action_badge": {
                    "tool": name,
                    "domain": "note",
                    "item_id": note["id"],
                    "label": f"Organized Note: {note['title']}",
                },
                "navigate_to": None,
            }

        if name == "append_to_note":
            note_id = _coerce_int(args.get("note_id"))
            task_id = _coerce_int(args.get("task_id"))
            if note_id is None and task_id is not None:
                res = db.append_task_note(
                    task_id,
                    text=str(args.get("text") or ""),
                    heading=str(args.get("heading") or ""),
                )
                if not res:
                    return {"result": {"error": f"Task #{task_id} not found."}, "action_badge": None, "navigate_to": None}
                note = _sync_note_if_needed(res["note"])
                return {
                    "result": {"note": note, "task": res["task"]},
                    "action_badge": {
                        "tool": name,
                        "domain": "note",
                        "item_id": note["id"],
                        "label": f"Task Note: {note['title']}",
                    },
                    "navigate_to": None,
                }
            if note_id is None:
                return {"result": {"error": "note_id or task_id is required."}, "action_badge": None, "navigate_to": None}
            folder_arg = str(args.get("folder") or "").strip() or None
            note = db.append_to_note(
                note_id=note_id,
                text=str(args.get("text") or ""),
                checklist_items=_coerce_str_list(args.get("checklist_items")),
                add_tags=_coerce_str_list(args.get("add_tags")),
                heading=str(args.get("heading") or ""),
                folder=folder_arg,
            )
            if not note:
                return {"result": {"error": f"Note #{note_id} not found."}, "action_badge": None, "navigate_to": None}
            note = _sync_note_if_needed(note)
            return {
                "result": {"note": note},
                "action_badge": {
                    "tool": name,
                    "domain": "note",
                    "item_id": note["id"],
                    "label": f"Updated Note: {note['title']}",
                },
                "navigate_to": None,
            }

        if name == "update_note_checklist":
            note_id = _coerce_int(args.get("note_id"))
            if note_id is None:
                return {"result": {"error": "note_id is required."}, "action_badge": None, "navigate_to": None}
            note = db.get_note(note_id)
            if not note:
                return {"result": {"error": f"Note #{note_id} not found."}, "action_badge": None, "navigate_to": None}
            for needle in _coerce_str_list(args.get("check_items")):
                note = db.toggle_note_checklist_line(note_id, match_text=needle, done=True) or note
            for needle in _coerce_str_list(args.get("uncheck_items")):
                note = db.toggle_note_checklist_line(note_id, match_text=needle, done=False) or note
            note = _sync_note_if_needed(note)
            chk = note["checklist"]
            return {
                "result": {"note": note},
                "action_badge": {
                    "tool": name,
                    "domain": "note",
                    "item_id": note["id"],
                    "label": f"Note checklist: {note['title']} ({chk['done']}/{chk['total']})",
                },
                "navigate_to": None,
            }

        if name == "log_gym_day":
            day_date = args.get("date")
            if isinstance(day_date, str) and day_date.strip().lower() == "null":
                day_date = None
            summary = db.set_activity_day(
                "gym",
                day_date=day_date,
                note=str(args.get("note") or ""),
            )
            return {
                "result": {"gym_summary": summary},
                "action_badge": {
                    "tool": name,
                    "domain": "gym",
                    "item_id": None,
                    "label": f"Marked Gym Day ({day_date or db.today_str()})",
                },
                "navigate_to": None,
            }

        if name == "log_gym_exercise":
            exercise = str(args.get("exercise") or "").strip()
            if not exercise:
                return {"result": {"error": "exercise name is required."}, "action_badge": None, "navigate_to": None}
            day_date = args.get("date")
            if isinstance(day_date, str) and day_date.strip().lower() == "null":
                day_date = None
            unit = str(args.get("unit") or default_unit).lower()
            if unit not in ("kg", "lbs"):
                unit = default_unit
            item = db.create_gym_log(
                exercise=exercise,
                sets=_coerce_int(args.get("sets"), 3) or 3,
                reps=_coerce_int(args.get("reps"), 10) or 0,
                weight=_coerce_float(args.get("weight"), None),
                unit=unit,
                notes=str(args.get("notes") or ""),
                day_date=day_date,
            )
            w_txt = f" @ {item['weight']}{item['unit']}" if item["weight"] is not None else ""
            return {
                "result": {"gym_log": item},
                "action_badge": {
                    "tool": name,
                    "domain": "gym",
                    "item_id": item["id"],
                    "label": f"Gym: {item['exercise']} {item['sets']}x{item['reps']}{w_txt}",
                },
                "navigate_to": None,
            }

        if name == "get_gym_stats":
            ex = str(args.get("exercise") or "").strip() or None
            limit = _coerce_int(args.get("limit"), 15) or 15
            return {
                "result": {
                    "summary": db.get_activity_summary("gym"),
                    "personal_bests": db.get_gym_exercise_stats()[:15],
                    "recent_logs": db.list_gym_logs(exercise=ex, limit=limit),
                },
                "action_badge": None,
                "navigate_to": None,
            }

        if name == "upsert_art":
            title = str(args.get("title") or "").strip()
            status = str(args.get("status") or "wip").lower()
            notes = str(args.get("notes") or "").strip()
            mark_day = _coerce_bool(args.get("mark_art_day"), not bool(title))
            day_date = args.get("date")
            if isinstance(day_date, str) and day_date.strip().lower() == "null":
                day_date = None

            day_summary = None
            if mark_day:
                day_summary = db.set_activity_day("art", day_date=day_date, note=notes or title)

            art_item = None
            if title:
                existing = db.find_art_item_by_title(title)
                if existing:
                    patch: dict[str, Any] = {}
                    if status in db.ART_STATUSES:
                        patch["status"] = status
                    if notes and notes not in existing["notes"]:
                        patch["notes"] = f"{existing['notes']}\n{notes}".strip() if existing["notes"] else notes
                    art_item = db.update_art_item(existing["id"], patch) or existing
                    badge_label = f"Art ({art_item['status']}): {art_item['title']}"
                else:
                    art_item = db.create_art_item(
                        title=title,
                        status=status if status in db.ART_STATUSES else "wip",
                        notes=notes,
                    )
                    badge_label = f"Art ({art_item['status']}): {art_item['title']}"
            else:
                badge_label = f"Marked Art Day ({day_date or db.today_str()})"

            return {
                "result": {"art_item": art_item, "art_summary": day_summary},
                "action_badge": {
                    "tool": name,
                    "domain": "art",
                    "item_id": art_item["id"] if art_item else None,
                    "label": badge_label,
                },
                "navigate_to": None,
            }

        if name == "upsert_game":
            title = str(args.get("title") or "").strip()
            if not title:
                return {"result": {"error": "Game title is required."}, "action_badge": None, "navigate_to": None}
            status = str(args.get("status") or "playing").lower()
            platform = str(args.get("platform") or "").strip()
            rating = _coerce_int(args.get("rating"), None)
            notes = str(args.get("notes") or "").strip()

            existing = db.find_game_by_title(title)
            if existing:
                patch: dict[str, Any] = {}
                if status in db.GAME_STATUSES:
                    patch["status"] = status
                if platform:
                    patch["platform"] = platform
                if rating is not None:
                    patch["rating"] = rating
                if notes and notes not in existing["notes"]:
                    patch["notes"] = f"{existing['notes']}\n{notes}".strip() if existing["notes"] else notes
                item = db.update_game(existing["id"], patch) or existing
            else:
                item = db.create_game(
                    title=title,
                    status=status if status in db.GAME_STATUSES else "want_to_play",
                    platform=platform,
                    rating=rating,
                    notes=notes,
                )
            return {
                "result": {"game": item},
                "action_badge": {
                    "tool": name,
                    "domain": "game",
                    "item_id": item["id"],
                    "label": f"Game ({item['status'].replace('_', ' ')}): {item['title']}",
                },
                "navigate_to": None,
            }

        if name == "control_study_timer":
            action = str(args.get("action") or "").strip().lower()
            open_view = _coerce_bool(args.get("open_timer_view"), False)
            nav = "timer" if open_view else None
            logged_session = None

            if action == "start":
                mode = str(args.get("mode") or "countdown").strip().lower()
                dur_mins = _coerce_float(args.get("duration_minutes"), 25.0) or 25.0
                brk_mins = _coerce_float(args.get("break_minutes"), 5.0)
                if brk_mins is None:
                    brk_mins = 5.0
                subject = str(args.get("subject") or "Study").strip() or "Study"
                topic = str(args.get("topic") or "").strip()
                started = db.start_timer_and_autosave(
                    mode=mode,
                    duration_minutes=dur_mins,
                    break_minutes=brk_mins,
                    subject=subject,
                    topic=topic,
                    task_id=_coerce_int(args.get("task_id")),
                    note_id=_coerce_int(args.get("note_id")),
                    notes=str(args.get("notes") or ""),
                )
                timer_state = started["state"]
                logged_session = started["previous_logged_session"]
                if timer_state["mode"] == "stopwatch":
                    lbl = f"Started Stopwatch: {timer_state['subject']}"
                else:
                    mins_lbl = round(timer_state["target_seconds"] / 60, 1)
                    lbl = f"Started {mins_lbl:g}m Timer: {timer_state['subject']}"
                badge = {"tool": name, "domain": "timer", "item_id": None, "label": lbl}
            elif action == "pause":
                timer_state = db.pause_timer()
                badge = {
                    "tool": name,
                    "domain": "timer",
                    "item_id": None,
                    "label": f"Paused Timer ({timer_state['subject']})",
                }
            elif action == "resume":
                timer_state = db.resume_timer()
                badge = {
                    "tool": name,
                    "domain": "timer",
                    "item_id": None,
                    "label": f"Resumed Timer ({timer_state['subject']})",
                }
            elif action in ("stop", "reset"):
                save_sess = _coerce_bool(args.get("save_session"), action == "stop")
                res = db.stop_timer(
                    save_session=save_sess,
                    notes=str(args["notes"]) if args.get("notes") is not None else None,
                )
                timer_state = res["state"]
                logged_session = res["logged_session"]
                if logged_session:
                    lbl = f"Logged {logged_session['duration_minutes']:g}m Study: {logged_session['subject']}"
                    item_id = logged_session["id"]
                else:
                    lbl = "Reset Timer" if action == "reset" else "Stopped Timer"
                    item_id = None
                badge = {"tool": name, "domain": "timer", "item_id": item_id, "label": lbl}
            else:
                return {"result": {"error": f"Unsupported timer action: {action}"}, "action_badge": None, "navigate_to": None}

            badges_out = [badge]
            if action == "start" and logged_session:
                badges_out.append(
                    {
                        "tool": name,
                        "domain": "timer",
                        "item_id": logged_session["id"],
                        "label": f"Auto-Saved Prior Study: {logged_session['duration_minutes']:g}m ({logged_session['subject']})",
                    }
                )
            return {
                "result": {
                    "timer": timer_state,
                    "logged_session": logged_session,
                    "previous_logged_session": logged_session if action == "start" else None,
                    "stats": db.get_study_stats(),
                },
                "action_badge": badge,
                "action_badges": badges_out,
                "navigate_to": nav,
            }

        if name == "log_study_session":
            dur_mins = _coerce_float(args.get("duration_minutes"))
            if dur_mins is None or dur_mins <= 0:
                return {"result": {"error": "duration_minutes > 0 is required."}, "action_badge": None, "navigate_to": None}
            day_date = args.get("date")
            if isinstance(day_date, str) and day_date.strip().lower() == "null":
                day_date = None
            item = db.create_study_session(
                subject=str(args.get("subject") or "Study"),
                topic=str(args.get("topic") or ""),
                duration_minutes=dur_mins,
                mode="manual",
                day_date=day_date,
                task_id=_coerce_int(args.get("task_id")),
                note_id=_coerce_int(args.get("note_id")),
                notes=str(args.get("notes") or ""),
            )
            topic_sfx = f" — {item['topic']}" if item["topic"] else ""
            return {
                "result": {"session": item, "stats": db.get_study_stats()},
                "action_badge": {
                    "tool": name,
                    "domain": "timer",
                    "item_id": item["id"],
                    "label": f"Study {item['duration_minutes']:g}m: {item['subject']}{topic_sfx}",
                },
                "navigate_to": None,
            }

        if name == "get_study_stats":
            subj = str(args.get("subject") or "").strip() or None
            limit = _coerce_int(args.get("limit"), 15) or 15
            return {
                "result": {
                    "timer": db.get_timer_state(),
                    "stats": db.get_study_stats(),
                    "recent_sessions": db.list_study_sessions(subject=subj, limit=limit),
                },
                "action_badge": None,
                "navigate_to": None,
            }

        if name == "upsert_watchlist_item":
            title = str(args.get("title") or "").strip()
            url = str(args.get("url") or "").strip()
            if url and not watchlist.is_safe_http_url(url):
                return {"result": {"error": "Watchlist URL must be a valid public http(s) URL."}, "action_badge": None, "navigate_to": None}
            if not title and not url:
                return {"result": {"error": "Provide title or url for the watchlist item."}, "action_badge": None, "navigate_to": None}

            url_info = watchlist.detect_url_info(url) if url else {"source": "", "content_type": "playlist"}
            content_type = str(args.get("content_type") or url_info["content_type"] or "playlist").lower()
            source = url_info["source"]
            existing = db.find_watchlist_by_url_or_title(url=url, title=title)
            if existing:
                patch: dict[str, Any] = {}
                if title:
                    patch["title"] = title
                if url:
                    patch["url"] = url
                if args.get("content_type"):
                    patch["content_type"] = content_type
                if source and not existing["source"]:
                    patch["source"] = source
                for fkey in ("category", "status", "notes"):
                    if args.get(fkey) is not None and str(args.get(fkey)).strip():
                        patch[fkey] = str(args.get(fkey)).strip()
                if args.get("progress_current") is not None:
                    patch["progress_current"] = _coerce_int(args.get("progress_current"), existing["progress_current"])
                if args.get("progress_total") is not None:
                    patch["progress_total"] = _coerce_int(args.get("progress_total"), existing["progress_total"])
                if args.get("tags") is not None:
                    merged_tags = list(existing["tags"])
                    for t in _coerce_str_list(args.get("tags")):
                        if t.lower() not in {x.lower() for x in merged_tags}:
                            merged_tags.append(t)
                    patch["tags"] = merged_tags
                item = db.update_watchlist_item(existing["id"], patch) or existing
                badge_lbl = f"Updated Watchlist: {item['title']}"
            else:
                item = db.create_watchlist_item(
                    title=title,
                    url=url,
                    content_type=content_type,
                    source=source,
                    category=str(args.get("category") or ""),
                    status=str(args.get("status") or "to_watch").lower(),
                    progress_current=_coerce_int(args.get("progress_current"), 0) or 0,
                    progress_total=_coerce_int(args.get("progress_total"), 0) or 0,
                    tags=_coerce_str_list(args.get("tags")),
                    notes=str(args.get("notes") or ""),
                )
                badge_lbl = f"Watchlist ({item['content_type']}): {item['title']}"
            cloud_sync.maybe_auto_sync_cloud_folder()
            return {
                "result": {"watchlist_item": item},
                "action_badge": {
                    "tool": name,
                    "domain": "watchlist",
                    "item_id": item["id"],
                    "label": badge_lbl,
                },
                "navigate_to": None,
            }

        if name == "list_watchlist":
            items = db.list_watchlist(
                status=args.get("status"),
                content_type=args.get("content_type"),
                category=str(args.get("category") or ""),
                query=str(args.get("query") or ""),
            )
            return {"result": {"items": items[:25]}, "action_badge": None, "navigate_to": None}

        if name == "navigate_ui":
            view = str(args.get("view") or "hub").strip().lower()
            if view == "agent":
                view = "ask"
            if view not in ALLOWED_VIEWS:
                return {"result": {"error": f"Unknown view: {view}"}, "action_badge": None, "navigate_to": None}
            return {
                "result": {"navigated_to": view},
                "action_badge": {
                    "tool": name,
                    "domain": "ui",
                    "item_id": None,
                    "label": f"Opened {view.title()}",
                },
                "navigate_to": view,
            }

        if name == "delete_item":
            domain = str(args.get("domain") or "").strip().lower()
            item_id = _coerce_int(args.get("item_id"))
            confirmed = _coerce_bool(args.get("confirmed"), False)
            if item_id is None:
                return {"result": {"error": "item_id is required."}, "action_badge": None, "navigate_to": None}
            if not confirmed:
                return {
                    "result": {
                        "needs_confirmation": True,
                        "domain": domain,
                        "item_id": item_id,
                        "message": "Ask the user to confirm before deleting.",
                    },
                    "action_badge": None,
                    "navigate_to": None,
                }
            deleted = False
            if domain == "task":
                deleted = db.delete_task(item_id)
            elif domain == "note":
                deleted = db.delete_note(item_id)
            elif domain == "gym_log":
                deleted = db.delete_gym_log(item_id)
            elif domain == "art":
                deleted = db.delete_art_item(item_id)
            elif domain == "game":
                deleted = db.delete_game(item_id)
            elif domain == "study_session":
                deleted = db.delete_study_session(item_id)
            elif domain == "watchlist":
                deleted = db.delete_watchlist_item(item_id)
            else:
                return {"result": {"error": f"Unknown domain: {domain}"}, "action_badge": None, "navigate_to": None}
            if not deleted:
                return {"result": {"error": f"{domain} #{item_id} not found."}, "action_badge": None, "navigate_to": None}
            return {
                "result": {"deleted": True, "domain": domain, "item_id": item_id},
                "action_badge": {
                    "tool": name,
                    "domain": domain,
                    "item_id": item_id,
                    "label": f"Deleted {domain} #{item_id}",
                },
                "navigate_to": None,
            }

        return {"result": {"error": f"Unknown tool: {name}"}, "action_badge": None, "navigate_to": None}
    except Exception as exc:
        return {"result": {"error": str(exc)}, "action_badge": None, "navigate_to": None}
