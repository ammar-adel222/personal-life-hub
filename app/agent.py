"""Pure-Python multi-turn tool-calling agent for Personal Life Hub."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from app import ai, db, tools
from app.config import ALLOWED_GROQ_MODELS, load_settings

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
FALLBACK_CHAT_MODEL = "openai/gpt-oss-20b"
MAX_TOOL_ROUNDS = 12


def _build_calendar_context() -> str:
    now = datetime.now()
    rows = [f"Today: {now.strftime('%A, %Y-%m-%d')}"]
    upcoming = []
    for delta in range(1, 15):
        dt = now + timedelta(days=delta)
        upcoming.append(f"+{delta}d={dt.strftime('%a %Y-%m-%d')}")
    rows.append("Upcoming 14 days: " + ", ".join(upcoming))
    return "\n".join(rows)


async def _call_groq_agent_step(
    messages: list[dict[str, Any]],
    api_key: str,
    model: str,
    use_tools: bool = True,
) -> tuple[dict[str, Any], str]:
    """Execute one Groq chat-completions step with fallback and dynamic model discovery."""
    candidates: list[str] = []
    for m in (model, *ALLOWED_GROQ_MODELS, FALLBACK_CHAT_MODEL):
        if m and m not in candidates:
            candidates.append(m)

    tried: set[str] = set()
    last_error = "Unknown Groq error"
    async with httpx.AsyncClient(timeout=35.0) as client:
        idx = 0
        discovered = False
        while idx < len(candidates):
            candidate = candidates[idx]
            idx += 1
            if candidate in tried:
                continue
            tried.add(candidate)

            body: dict[str, Any] = {
                "model": candidate,
                "messages": messages,
                "temperature": 0.15,
            }
            if use_tools:
                body["tools"] = tools.TOOL_SCHEMAS
                body["tool_choice"] = "auto"

            resp = await client.post(
                f"{GROQ_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            if resp.status_code == 200:
                data = resp.json()
                msg = data["choices"][0]["message"]
                return msg, candidate

            last_error = f"HTTP {resp.status_code}: {resp.text[:260]}"
            if resp.status_code == 404 and not discovered:
                discovered = True
                for live_model in await ai.discover_live_chat_models(client, api_key):
                    if live_model not in tried and live_model not in candidates:
                        candidates.append(live_model)
            if resp.status_code in (400, 404, 429, 500, 502, 503) and idx < len(candidates):
                continue
            raise ai.AIError(last_error)

    raise ai.AIError(last_error)


def _offline_agent_fallback(
    user_text: str, default_unit: str = "kg"
) -> tuple[str, list[dict[str, Any]]]:
    """Deterministic fallback when no Groq API key is configured or network is down."""
    text = user_text.strip()
    lower = text.lower()
    actions_taken: list[dict[str, Any]] = []

    steps_match = re.search(
        r"(?:with\s+)?(?:steps|checklist)\s*:\s*(.+)$", text, flags=re.IGNORECASE
    )
    if steps_match:
        raw_steps = [
            s.strip(" .")
            for s in re.split(r"[,;]+", steps_match.group(1))
            if s.strip(" .")
        ]
        prefix_title = text[: steps_match.start()].strip(" :-")
        prefix_title = re.sub(
            r"^(?:create|make|add)\s+(?:a\s+)?(?:task|checklist)\s*(?:to|for)?\s*",
            "",
            prefix_title,
            flags=re.IGNORECASE,
        ).strip()
        if raw_steps and prefix_title:
            res = tools.execute_tool(
                "create_task",
                {"title": prefix_title, "checklist_items": raw_steps},
            )
            if res["action_badge"]:
                actions_taken.append(res["action_badge"])
            return (
                f"Created task '{prefix_title}' with {len(raw_steps)} checklist items (offline mode).",
                actions_taken,
            )

    # Only save as a lecture note if the user isn't explicitly asking for a task list / timeline
    is_task_plan = any(
        kw in lower
        for kw in ("task list", "make a task", "create a task", "next 10 days", "for the next")
    )
    if not is_task_plan and (
        "note:" in lower or "tags:" in lower or ("lecture" in lower and "watch" not in lower)
    ):
        tags_match = re.search(
            r"tags?\s*[:\-]?\s*([a-zA-Z0-9,\s_-]+)$", text, flags=re.IGNORECASE
        )
        tags = (
            [t.strip() for t in re.split(r"[\s,]+", tags_match.group(1)) if t.strip()]
            if tags_match
            else ["lecture"]
        )
        res = tools.execute_tool(
            "create_note",
            {"title": text[:65], "content": text, "tags": tags},
        )
        if res["action_badge"]:
            actions_taken.append(res["action_badge"])
        return (
            f"Saved note '{res['result']['note']['title']}' (offline mode).",
            actions_taken,
        )

    parsed = ai._fallback_parse(text, db.today_str(), default_unit)
    executed = ai.execute_actions(parsed.get("actions", []), default_unit=default_unit)
    for ex in executed:
        domain = (
            "gym"
            if ex["type"].startswith("gym")
            else ("art" if ex["type"].startswith("art") else ex["type"])
        )
        item = ex.get("item") if isinstance(ex.get("item"), dict) else {}
        actions_taken.append(
            {
                "tool": "offline_fallback",
                "domain": domain,
                "item_id": item.get("id"),
                "label": ex["label"],
            }
        )
    reply = " · ".join(a["label"] for a in actions_taken) if actions_taken else "Saved note."
    return (
        f"{reply} (offline mode — add your Groq API key in Settings for full agentic AI).",
        actions_taken,
    )


def _parse_tool_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def run_agent_turn(
    user_text: str,
    session_id: int | None = None,
    source: str = "text",
) -> dict[str, Any]:
    """Run a multi-turn pure-Python tool-calling agent step and persist memory."""
    clean = (user_text or "").strip()
    if not clean:
        raise ValueError("Message cannot be empty.")

    session = db.get_or_create_active_session(session_id)
    sid = int(session["id"])
    detail = db.get_agent_session_detail(sid, message_limit=14)
    prior_messages = detail["messages"] if detail else []

    db.add_agent_message(sid, "user", clean, source=source)

    if session["title"] in ("Voice Session", "New Session") and not prior_messages:
        short_title = clean[:52] + ("…" if len(clean) > 52 else "")
        db.update_agent_session_title(sid, short_title)
        session["title"] = short_title

    settings = load_settings()
    api_key = settings.get("groq_api_key", "").strip()
    model = settings.get("groq_model", "openai/gpt-oss-120b")
    default_unit = settings.get("weight_unit", "kg")
    calendar_ctx = _build_calendar_context()

    system_prompt = (
        "You are the voice-first personal AI agent inside Personal Life Hub.\n"
        f"{calendar_ctx}\n"
        f"Default gym weight unit: {default_unit}.\n\n"
        "CRITICAL RULES:\n"
        "1. **Clean up voice/Whisper typos**: Spoken transcripts may contain phonetic mistakes "
        "(e.g. 'Stanford Asian Tech AI' or 'agent a key I' -> 'Stanford Agentic AI Course', "
        "'publishing hardness' -> 'Publishing Harness', 'EREXO' -> 'Ericsson'). Always use clean, accurate titles.\n"
        "2. **Decompose multi-item plans into SEPARATE Active Tasks**: When the user gives a task list or multi-part plan, "
        "use `create_tasks_batch` with `list_type='daily'` (Active Tasks) to create a separate, well-named task for EACH goal/deliverable. "
        "NEVER merge unrelated goals into one task, and NEVER dump a spoken task list as a raw note. "
        "Watching/studying lectures as part of a to-do plan is a TASK (e.g. title 'Stanford Agentic AI Lectures 1-5' with checklist_items "
        "['Lecture 1', 'Lecture 2', 'Lecture 3', 'Lecture 4', 'Lecture 5']), NOT a note. "
        "Only use `create_note` or `append_to_note` when recording actual lecture content, ideas, or research notes.\n"
        "3. **Always populate timelines, dates & frequencies**:\n"
        "   - Compute exact `start_date` and `due_date` (`YYYY-MM-DD`) using the 14-day calendar above (e.g. 'next 10 days' -> start_date=today, due_date=+10d, timeline='Next 10 days').\n"
        "   - Always fill `frequency` when a cadence or target amount is mentioned (e.g. '4x / week', '3 artworks in 10 days', '10-20 hours total').\n"
        "   - Add concrete `checklist_items` when an item is countable (e.g. Lectures 1..5, Artwork 1..3, or subtasks).\n"
        "   - Reserve `list_type='want_to_do'` strictly for unscheduled someday ideas with no timeline.\n"
        "4. **Auto-structure notes**: Whenever saving or updating a note (`create_note`, `append_to_note`, `update_note`), "
        "NEVER dump raw unformatted voice transcripts. Format `content` in clean Markdown (`##` headings, concise bullet points, "
        "**bold** key terms, `> [!NOTE]` callout for key takeaways, and `- [ ]` research/action items), write a concise 1-2 sentence `summary`, "
        "pick an appropriate `folder` (`Lectures`, `Projects`, `Study`, `Personal`, `Ideas`), and include relevant `tags`. "
        "When the user asks to organize or clean up an existing note, fetch it with `get_note` or `search_notes` and update it via `update_note`.\n"
        "5. **Update hobby trackers too**: If the user mentions games (e.g. Elden Ring) or art projects, ALSO call `upsert_game` / `upsert_art` alongside creating any sprint tasks.\n"
        "6. **Check existing records first**: When updating/checking off an existing task or lecture note without its ID in recent history, call `list_tasks` or `search_notes` first.\n"
        "7. **Delete safety**: Never delete without explicit confirmation (`confirmed=false` first).\n"
        "8. **Concise replies**: Confirm clearly what was created/updated in 1-3 short, friendly sentences. Ask a brief clarifying question only if genuinely ambiguous."
    )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for msg in prior_messages[-12:]:
        content = msg["content"]
        if msg["role"] == "assistant" and msg.get("actions"):
            act_notes = "; ".join(
                f"{a.get('domain', '')} #{a.get('item_id')}: {a.get('label', '')}"
                if a.get("item_id") is not None
                else str(a.get("label", ""))
                for a in msg["actions"]
            )
            content = f"{content}\n[Executed actions: {act_notes}]"
        messages.append({"role": msg["role"], "content": content})
    messages.append({"role": "user", "content": clean})

    actions_taken: list[dict[str, Any]] = []
    navigate_target: str | None = None
    ai_used = False
    model_used: str | None = None
    warning: str | None = None
    assistant_reply = ""

    if api_key:
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                msg_dict, model_used = await _call_groq_agent_step(
                    messages=messages,
                    api_key=api_key,
                    model=model,
                    use_tools=True,
                )
                ai_used = True
                tool_calls = msg_dict.get("tool_calls") or []
                if tool_calls:
                    messages.append(
                        {
                            "role": "assistant",
                            "content": msg_dict.get("content") or "",
                            "tool_calls": tool_calls,
                        }
                    )
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        fn_name = str(fn.get("name") or "")
                        fn_args = _parse_tool_args(fn.get("arguments"))
                        out = tools.execute_tool(fn_name, fn_args)
                        if out.get("action_badges"):
                            actions_taken.extend(out["action_badges"])
                        elif out.get("action_badge"):
                            actions_taken.append(out["action_badge"])
                        if out.get("navigate_to"):
                            navigate_target = out["navigate_to"]
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": str(tc.get("id") or fn_name),
                                "content": json.dumps(out["result"], ensure_ascii=False),
                            }
                        )
                    continue

                assistant_reply = str(msg_dict.get("content") or "").strip()
                break

            if not assistant_reply:
                if actions_taken:
                    assistant_reply = "Done: " + " · ".join(a["label"] for a in actions_taken) + "."
                else:
                    assistant_reply = "Got it."
        except Exception as exc:
            warning = f"Groq agent unavailable ({exc}); used offline fallback."
            assistant_reply, actions_taken = _offline_agent_fallback(clean, default_unit)
            ai_used = False
            model_used = None
    else:
        assistant_reply, actions_taken = _offline_agent_fallback(clean, default_unit)

    db.add_agent_message(
        sid,
        "assistant",
        assistant_reply,
        actions=actions_taken,
        source=source,
    )
    if actions_taken:
        db.record_capture(
            raw_text=clean,
            source=source,
            summary=assistant_reply[:240],
            actions=actions_taken,
        )

    return {
        "session_id": sid,
        "session_title": session["title"],
        "reply": assistant_reply,
        "is_question": "?" in assistant_reply,
        "actions": actions_taken,
        "navigate_to": navigate_target,
        "ai_used": ai_used,
        "model": model_used,
        "warning": warning,
    }
