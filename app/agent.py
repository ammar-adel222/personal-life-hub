"""Pure-Python multi-turn tool-calling agent for Personal Life Hub."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app import ai, db, tools
from app.config import load_settings

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
FALLBACK_CHAT_MODEL = "llama-3.1-8b-instant"
MAX_TOOL_ROUNDS = 6


async def _call_groq_agent_step(
    messages: list[dict[str, Any]],
    api_key: str,
    model: str,
    use_tools: bool = True,
) -> tuple[dict[str, Any], str]:
    """Execute one Groq chat-completions step with optional tool schemas."""
    candidates = [model]
    if model != FALLBACK_CHAT_MODEL:
        candidates.append(FALLBACK_CHAT_MODEL)

    last_error = "Unknown Groq error"
    async with httpx.AsyncClient(timeout=30.0) as client:
        for idx, candidate in enumerate(candidates):
            body: dict[str, Any] = {
                "model": candidate,
                "messages": messages,
                "temperature": 0.2,
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
            if resp.status_code in (400, 404, 429, 500, 502, 503) and idx + 1 < len(candidates):
                continue
            raise ai.AIError(last_error)

    raise ai.AIError(last_error)


def _offline_agent_fallback(user_text: str, default_unit: str = "kg") -> tuple[str, list[dict[str, Any]]]:
    """Deterministic fallback when no Groq API key is configured or network is down."""
    text = user_text.strip()
    lower = text.lower()
    actions_taken: list[dict[str, Any]] = []

    # Check for explicit checklist creation pattern: "task X with steps: a, b, c"
    steps_match = re.search(r"(?:with\s+)?(?:steps|checklist)\s*:\s*(.+)$", text, flags=re.IGNORECASE)
    if steps_match:
        raw_steps = [s.strip(" .") for s in re.split(r"[,;]+", steps_match.group(1)) if s.strip(" .")]
        prefix_title = text[: steps_match.start()].strip(" :-")
        prefix_title = re.sub(r"^(?:create|make|add)\s+(?:a\s+)?(?:task|checklist)\s*(?:to|for)?\s*", "", prefix_title, flags=re.IGNORECASE).strip()
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

    # Lecture note pattern with tags
    if "lecture" in lower or "tags" in lower or "tag " in lower:
        tags_match = re.search(r"tags?\s*[:\-]?\s*([a-zA-Z0-9,\s_-]+)$", text, flags=re.IGNORECASE)
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
        return (f"Saved note '{res['result']['note']['title']}' (offline mode).", actions_taken)

    # Fallback to smart_capture heuristics
    parsed = ai._fallback_parse(text, db.today_str(), default_unit)
    executed = ai.execute_actions(parsed.get("actions", []), default_unit=default_unit)
    for ex in executed:
        domain = "gym" if ex["type"].startswith("gym") else ("art" if ex["type"].startswith("art") else ex["type"])
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
    return f"{reply} (offline mode — add your Groq API key in Settings for full agentic AI).", actions_taken


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

    # Save user turn immediately
    db.add_agent_message(sid, "user", clean, source=source)

    # Auto-name new sessions after first user turn
    if session["title"] in ("Voice Session", "New Session") and not prior_messages:
        short_title = clean[:52] + ("…" if len(clean) > 52 else "")
        db.update_agent_session_title(sid, short_title)
        session["title"] = short_title

    settings = load_settings()
    api_key = settings.get("groq_api_key", "").strip()
    model = settings.get("groq_model", "llama-3.3-70b-versatile")
    default_unit = settings.get("weight_unit", "kg")
    today = db.today_str()

    system_prompt = (
        f"You are the voice-first personal AI agent inside Personal Life Hub. Today's date is {today}.\n"
        f"Default gym weight unit: {default_unit}.\n\n"
        "Capabilities & Rules:\n"
        "1. You control the entire app via tools: 3-state checklist tasks (not_started, partially_completed, completed), "
        "nested task checklist sub-items, interactive Markdown checklist notes & tags, gym days/exercises/stats, art days/WIPs/ideas, gaming tracker, and UI navigation.\n"
        "2. When the user dumps multiple events at once (e.g. went to gym, drew art, watched a lecture with tags, need to research X later), "
        "call ALL needed tools so everything lands in the right place.\n"
        "3. When referring to an existing task or lecture note (e.g. 'check off the first step', 'add to that lecture note', 'mark it partially done') "
        "and you do not already see its ID in recent history, call list_tasks or search_notes first.\n"
        "4. Use checklist_items on create_task/create_note whenever the user lists steps, sub-tasks, or things to research/check later.\n"
        "5. Never delete anything without explicit confirmation: first call delete_item with confirmed=false (or ask), and only pass confirmed=true after the user confirms.\n"
        "6. Keep your final reply concise, friendly, and voice-ready (1 to 3 short sentences unless asked for details). "
        "If anything is genuinely ambiguous between two items, ask a brief clarifying question."
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
                        if out.get("action_badge"):
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
