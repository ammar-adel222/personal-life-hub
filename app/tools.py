"""Pure-Python tool registry and execution handlers for the Personal Life Hub agent."""

from __future__ import annotations

import json
from typing import Any

from app import db, notes_sync
from app.config import load_settings

ALLOWED_VIEWS = ("hub", "tasks", "notes", "gym", "art", "gaming", "ask", "agent", "settings")

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
            "description": "List tasks and their nested checklist items. Use before updating/checking off an existing task when you do not yet know its task_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_type": {"type": "string", "enum": ["daily", "want_to_do"]},
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
            "description": "Create a new Daily Task or Want-to-Do item, optionally with checklist sub-items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "list_type": {"type": "string", "enum": ["daily", "want_to_do"]},
                    "checklist_items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of checkable steps inside the task.",
                    },
                    "priority": {"type": "string", "enum": ["normal", "high"]},
                    "category": {"type": "string"},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD or null"},
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
            "name": "update_task",
            "description": "Update an existing task's status (not_started, partially_completed, completed), title, list_type, priority, due_date, or progress_note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["not_started", "partially_completed", "completed"],
                    },
                    "list_type": {"type": "string", "enum": ["daily", "want_to_do"]},
                    "priority": {"type": "string", "enum": ["normal", "high"]},
                    "category": {"type": "string"},
                    "due_date": {"type": "string"},
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
            "description": "Search notes by keyword query and/or tag. Use before appending to or checking off a lecture/research note if note_id is not yet known.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "tag": {"type": "string"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_note",
            "description": "Fetch full note content, tags, and interactive checklist stats by note_id.",
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
            "description": "Create a new note (e.g. lecture note, idea, research note) with optional tags and Markdown checklist items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "content": {"type": "string"},
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
            "name": "append_to_note",
            "description": "Append additional text, checklist items to research later, or tags to an existing note (great for mid-lecture voice additions).",
            "parameters": {
                "type": "object",
                "properties": {
                    "note_id": {"type": "integer"},
                    "text": {"type": "string"},
                    "checklist_items": {"type": "array", "items": {"type": "string"}},
                    "add_tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["note_id"],
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
            "name": "navigate_ui",
            "description": "Switch the desktop app screen to a specific view when the user asks to open or show a section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {
                        "type": "string",
                        "enum": ["hub", "tasks", "notes", "gym", "art", "gaming", "agent", "settings"],
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
            "description": "Delete a task, note, gym log, art item, or game. Only set confirmed=true after the user has explicitly confirmed deletion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "enum": ["task", "note", "gym_log", "art", "game"]},
                    "item_id": {"type": "integer"},
                    "confirmed": {"type": "boolean"},
                },
                "required": ["domain", "item_id"],
            },
        },
    },
]


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
                    or q in t["progress_note"].lower()
                    or any(q in sub["text"].lower() for sub in t["checklist_items"])
                ]
            return {"result": {"tasks": items[:30]}, "action_badge": None, "navigate_to": None}

        if name == "create_task":
            title = str(args.get("title") or "").strip()
            checklist = _coerce_str_list(args.get("checklist_items"))
            due = args.get("due_date")
            if isinstance(due, str) and due.strip().lower() == "null":
                due = None
            item = db.create_task(
                title=title,
                list_type=str(args.get("list_type") or "daily"),
                category=str(args.get("category") or ""),
                priority=str(args.get("priority") or "normal"),
                due_date=due,
                status=str(args.get("status") or "not_started"),
                progress_note=str(args.get("progress_note") or ""),
                checklist_items=checklist,
            )
            sub_info = f" ({item['checklist_total']} steps)" if item["checklist_total"] else ""
            prefix = "Task" if item["list_type"] == "daily" else "Want to do"
            return {
                "result": {"task": item},
                "action_badge": {
                    "tool": name,
                    "domain": "task",
                    "item_id": item["id"],
                    "label": f"{prefix}: {item['title']}{sub_info}",
                },
                "navigate_to": None,
            }

        if name == "update_task":
            task_id = _coerce_int(args.get("task_id"))
            if task_id is None:
                return {"result": {"error": "task_id is required."}, "action_badge": None, "navigate_to": None}
            patch: dict[str, Any] = {}
            for key in ("title", "status", "list_type", "priority", "category", "due_date", "progress_note"):
                if key in args and args[key] is not None:
                    val = args[key]
                    if key == "due_date" and str(val).strip().lower() == "null":
                        val = None
                    patch[key] = val
            item = db.update_task(task_id, patch)
            if not item:
                return {"result": {"error": f"Task #{task_id} not found."}, "action_badge": None, "navigate_to": None}
            return {
                "result": {"task": item},
                "action_badge": {
                    "tool": name,
                    "domain": "task",
                    "item_id": item["id"],
                    "label": f"Task ({item['status'].replace('_', ' ')}): {item['title']}",
                },
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
            return {
                "result": {"task": item},
                "action_badge": {
                    "tool": name,
                    "domain": "task",
                    "item_id": item["id"],
                    "label": (
                        f"Checklist: {item['title']} "
                        f"({item['checklist_done']}/{item['checklist_total']} · {item['status'].replace('_', ' ')})"
                    ),
                },
                "navigate_to": None,
            }

        if name == "search_notes":
            items = db.list_notes(
                query=str(args.get("query") or ""),
                tag=str(args.get("tag") or ""),
            )
            compact = [
                {
                    "id": n["id"],
                    "title": n["title"],
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
            )
            if _coerce_bool(args.get("save_to_folder"), False):
                folder = settings.get("notes_folder", "").strip()
                if folder:
                    try:
                        note = notes_sync.save_new_note_to_folder(note, folder)
                    except OSError:
                        pass
            tag_label = f" [{', '.join('#' + t for t in note['tags'])}]" if note["tags"] else ""
            return {
                "result": {"note": note},
                "action_badge": {
                    "tool": name,
                    "domain": "note",
                    "item_id": note["id"],
                    "label": f"Note: {note['title']}{tag_label}",
                },
                "navigate_to": None,
            }

        if name == "append_to_note":
            note_id = _coerce_int(args.get("note_id"))
            if note_id is None:
                return {"result": {"error": "note_id is required."}, "action_badge": None, "navigate_to": None}
            note = db.append_to_note(
                note_id=note_id,
                text=str(args.get("text") or ""),
                checklist_items=_coerce_str_list(args.get("checklist_items")),
                add_tags=_coerce_str_list(args.get("add_tags")),
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
