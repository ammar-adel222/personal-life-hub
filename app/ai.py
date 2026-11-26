"""Groq AI smart capture, action execution, local fallback, and Ask My Life Q&A."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app import db
from app.config import load_settings

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
FALLBACK_CHAT_MODEL = "llama-3.1-8b-instant"


class AIError(Exception):
    """Raised when an explicit AI verification or request fails."""


def _extract_json_object(text: str) -> dict[str, Any]:
    clean = (text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    try:
        parsed = json.loads(clean)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("AI response did not contain a valid JSON object.")


async def verify_groq_key(api_key: str) -> dict[str, Any]:
    """Verify a Groq API key by querying the models endpoint."""
    key = (api_key or "").strip()
    if not key:
        raise AIError("No Groq API key provided.")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{GROQ_BASE_URL}/models",
            headers={"Authorization": f"Bearer {key}"},
        )
    if resp.status_code == 401:
        raise AIError("Invalid Groq API key (401 Unauthorized).")
    if resp.status_code >= 400:
        raise AIError(f"Groq API error ({resp.status_code}): {resp.text[:200]}")
    payload = resp.json()
    models = [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)]
    return {"valid": True, "models_count": len(models), "models": models[:20]}


async def _call_groq_chat(
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
    json_mode: bool = False,
    temperature: float = 0.2,
) -> tuple[str, str]:
    """Call Groq chat completions with automatic fallback to 8B instant on rate/model issues."""
    candidates = [model]
    if model != FALLBACK_CHAT_MODEL:
        candidates.append(FALLBACK_CHAT_MODEL)

    last_error = "Unknown Groq error"
    async with httpx.AsyncClient(timeout=25.0) as client:
        for idx, candidate in enumerate(candidates):
            body: dict[str, Any] = {
                "model": candidate,
                "messages": messages,
                "temperature": temperature,
            }
            if json_mode:
                body["response_format"] = {"type": "json_object"}
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
                content = data["choices"][0]["message"]["content"]
                return content, candidate
            last_error = f"HTTP {resp.status_code}: {resp.text[:240]}"
            if resp.status_code in (429, 500, 502, 503, 404) and idx + 1 < len(candidates):
                continue
            raise AIError(last_error)
    raise AIError(last_error)


def _fallback_parse(raw_text: str, today: str, default_unit: str = "kg") -> dict[str, Any]:
    """Lightweight offline/no-key parser so quick capture always works immediately."""
    text = raw_text.strip()
    lower = text.lower()
    actions: list[dict[str, Any]] = []

    if re.search(r"\b(went to (?:the )?gym|did gym|gym day|worked out)\b", lower):
        actions.append({"type": "gym_day", "date": today, "note": text})

    gym_match = re.match(
        r"^(?P<ex>[a-zA-Z][a-zA-Z0-9\s\-]{1,40}?)\s+"
        r"(?:(?P<w1>\d+(?:\.\d+)?)\s*(?P<u1>kg|lbs|lb)?\s+)?"
        r"(?P<sets>\d+)\s*[xX×]\s*(?P<reps>\d+)"
        r"(?:\s*(?:@|at)?\s*(?P<w2>\d+(?:\.\d+)?)\s*(?P<u2>kg|lbs|lb)?)?$",
        text,
    )
    if gym_match:
        ex = gym_match.group("ex").strip()
        sets = int(gym_match.group("sets"))
        reps = int(gym_match.group("reps"))
        w_str = gym_match.group("w2") or gym_match.group("w1")
        u_str = gym_match.group("u2") or gym_match.group("u1") or default_unit
        unit = "lbs" if u_str in ("lbs", "lb") else "kg"
        actions.append(
            {
                "type": "gym_set",
                "date": today,
                "exercise": ex,
                "sets": sets,
                "reps": reps,
                "weight": float(w_str) if w_str else None,
                "unit": unit,
                "notes": "",
            }
        )

    if not actions and re.search(r"\b(drew today|did art|sketched today|painted today)\b", lower):
        actions.append({"type": "art_day", "date": today, "note": text})

    if not actions:
        if lower.startswith("want to play "):
            actions.append(
                {
                    "type": "game",
                    "title": text[13:].strip(),
                    "status": "want_to_play",
                    "notes": "",
                }
            )
        elif lower.startswith("playing "):
            actions.append(
                {
                    "type": "game",
                    "title": text[8:].strip(),
                    "status": "playing",
                    "notes": "",
                }
            )
        elif lower.startswith("want to ") or lower.startswith("someday "):
            actions.append({"type": "want_to_do", "title": text, "category": ""})
        elif lower.startswith("note:") or "\n" in text or len(text) > 120:
            clean_note = text[5:].strip() if lower.startswith("note:") else text
            actions.append({"type": "note", "title": "", "content": clean_note, "tags": []})
        else:
            actions.append({"type": "task", "title": text, "priority": "normal", "category": ""})

    return {
        "summary": f"Saved via local quick-parse ({actions[0]['type'].replace('_', ' ')})",
        "actions": actions,
    }


def _explicit_mode_actions(raw_text: str, mode: str, today: str, unit: str) -> dict[str, Any]:
    text = raw_text.strip()
    if mode == "task":
        return {"summary": "Added daily task", "actions": [{"type": "task", "title": text}]}
    if mode == "want_to_do":
        return {"summary": "Added to Want to Do", "actions": [{"type": "want_to_do", "title": text}]}
    if mode == "note":
        return {"summary": "Saved note", "actions": [{"type": "note", "title": "", "content": text}]}
    if mode == "gym":
        parsed = _fallback_parse(text, today, unit)
        gym_actions = [a for a in parsed["actions"] if a["type"] in ("gym_day", "gym_set")]
        if not gym_actions:
            gym_actions = [{"type": "gym_day", "date": today, "note": text}]
        return {"summary": "Logged gym entry", "actions": gym_actions}
    if mode == "art":
        return {"summary": "Saved art idea", "actions": [{"type": "art_item", "title": text, "status": "idea"}]}
    if mode == "game":
        return {"summary": "Saved game", "actions": [{"type": "game", "title": text, "status": "want_to_play"}]}
    return _fallback_parse(text, today, unit)


def execute_actions(actions: list[dict[str, Any]], default_unit: str = "kg") -> list[dict[str, Any]]:
    """Persist structured smart-capture actions into SQLite with smart deduplication."""
    today = db.today_str()
    executed: list[dict[str, Any]] = []

    for act in actions:
        if not isinstance(act, dict):
            continue
        atype = str(act.get("type", "")).strip().lower()

        if atype == "task":
            title = str(act.get("title") or "").strip()
            if title:
                item = db.create_task(
                    title=title,
                    list_type="daily",
                    category=str(act.get("category") or ""),
                    priority=str(act.get("priority") or "normal"),
                    due_date=act.get("due_date"),
                )
                executed.append({"type": "task", "label": f"Daily task: {item['title']}", "item": item})

        elif atype == "want_to_do":
            title = str(act.get("title") or "").strip()
            if title:
                item = db.create_task(
                    title=title,
                    list_type="want_to_do",
                    category=str(act.get("category") or ""),
                    priority="normal",
                )
                executed.append({"type": "want_to_do", "label": f"Want to do: {item['title']}", "item": item})

        elif atype == "note":
            title = str(act.get("title") or "").strip()
            content = str(act.get("content") or title).strip()
            if title or content:
                item = db.create_note(
                    title=title,
                    content=content,
                    tags=act.get("tags") or [],
                )
                executed.append({"type": "note", "label": f"Note: {item['title']}", "item": item})

        elif atype == "gym_day":
            day_date = str(act.get("date") or today).strip()
            note = str(act.get("note") or "").strip()
            summary = db.set_activity_day("gym", day_date=day_date, note=note)
            executed.append({"type": "gym_day", "label": f"Marked Gym Day ({day_date})", "summary": summary})

        elif atype == "gym_set":
            exercise = str(act.get("exercise") or "").strip()
            if exercise:
                day_date = str(act.get("date") or today).strip()
                unit = str(act.get("unit") or default_unit).lower()
                if unit not in ("kg", "lbs"):
                    unit = default_unit
                weight = act.get("weight")
                try:
                    weight_val = float(weight) if weight is not None and str(weight) != "" else None
                except (TypeError, ValueError):
                    weight_val = None
                item = db.create_gym_log(
                    exercise=exercise,
                    sets=int(act.get("sets") or 1),
                    reps=int(act.get("reps") or 0),
                    weight=weight_val,
                    unit=unit,
                    notes=str(act.get("notes") or ""),
                    day_date=day_date,
                )
                w_label = f" @ {item['weight']}{item['unit']}" if item["weight"] is not None else ""
                executed.append(
                    {
                        "type": "gym_set",
                        "label": f"Gym: {item['exercise']} {item['sets']}x{item['reps']}{w_label}",
                        "item": item,
                    }
                )

        elif atype == "art_day":
            day_date = str(act.get("date") or today).strip()
            note = str(act.get("note") or "").strip()
            summary = db.set_activity_day("art", day_date=day_date, note=note)
            executed.append({"type": "art_day", "label": f"Marked Art Day ({day_date})", "summary": summary})

        elif atype == "art_item":
            title = str(act.get("title") or "").strip()
            if title:
                status = str(act.get("status") or "idea").lower()
                new_notes = str(act.get("notes") or "").strip()
                existing = db.find_art_item_by_title(title)
                if existing:
                    patch: dict[str, Any] = {}
                    if status in db.ART_STATUSES:
                        patch["status"] = status
                    if new_notes and new_notes not in existing["notes"]:
                        patch["notes"] = (
                            f"{existing['notes']}\n{new_notes}".strip()
                            if existing["notes"]
                            else new_notes
                        )
                    item = db.update_art_item(existing["id"], patch) or existing
                    executed.append({"type": "art_item", "label": f"Updated Art: {item['title']}", "item": item})
                else:
                    item = db.create_art_item(title=title, status=status, notes=new_notes)
                    executed.append({"type": "art_item", "label": f"Art ({item['status']}): {item['title']}", "item": item})

        elif atype == "game":
            title = str(act.get("title") or "").strip()
            if title:
                status = str(act.get("status") or "").lower()
                platform = str(act.get("platform") or "").strip()
                rating = act.get("rating")
                new_notes = str(act.get("notes") or "").strip()
                existing = db.find_game_by_title(title)
                if existing:
                    patch = {}
                    if status in db.GAME_STATUSES:
                        patch["status"] = status
                    if platform:
                        patch["platform"] = platform
                    if rating is not None and str(rating) != "":
                        patch["rating"] = rating
                    if new_notes and new_notes not in existing["notes"]:
                        patch["notes"] = (
                            f"{existing['notes']}\n{new_notes}".strip()
                            if existing["notes"]
                            else new_notes
                        )
                    item = db.update_game(existing["id"], patch) or existing
                    executed.append({"type": "game", "label": f"Updated Game: {item['title']}", "item": item})
                else:
                    item = db.create_game(
                        title=title,
                        status=status if status in db.GAME_STATUSES else "want_to_play",
                        platform=platform,
                        rating=rating,
                        notes=new_notes,
                    )
                    executed.append({"type": "game", "label": f"Game ({item['status']}): {item['title']}", "item": item})

    return executed


async def smart_capture(
    raw_text: str,
    source: str = "text",
    mode: str = "auto",
) -> dict[str, Any]:
    """Parse and file freeform text or voice transcript into the appropriate trackers."""
    clean = (raw_text or "").strip()
    if not clean:
        raise ValueError("Capture text cannot be empty.")

    settings = load_settings()
    today = db.today_str()
    default_unit = settings.get("weight_unit", "kg")
    api_key = settings.get("groq_api_key", "").strip()
    model = settings.get("groq_model", "llama-3.3-70b-versatile")

    ai_used = False
    model_used: str | None = None
    warning: str | None = None

    if mode != "auto":
        parsed = _explicit_mode_actions(clean, mode, today, default_unit)
    elif api_key:
        system_prompt = (
            f"You are the fast personal assistant inside Personal Life Hub. Today's date is {today}.\n"
            "Classify the user's quick note/voice dump into one or more structured JSON actions.\n"
            "Return ONLY a JSON object: {\"summary\": \"short friendly confirmation\", \"actions\": [...]}.\n"
            "Allowed action schemas:\n"
            "1. {\"type\": \"task\", \"title\": \"...\", \"priority\": \"normal|high\", \"due_date\": \"YYYY-MM-DD or null\", \"category\": \"\"}\n"
            "2. {\"type\": \"want_to_do\", \"title\": \"...\", \"category\": \"\"} (bucket list, someday goals, things to try)\n"
            "3. {\"type\": \"note\", \"title\": \"...\", \"content\": \"...\", \"tags\": [\"...\"]}\n"
            "4. {\"type\": \"gym_day\", \"date\": \"YYYY-MM-DD\", \"note\": \"\"} (when user mentions going to gym / working out)\n"
            "5. {\"type\": \"gym_set\", \"date\": \"YYYY-MM-DD\", \"exercise\": \"...\", \"sets\": 3, \"reps\": 10, \"weight\": 80, \"unit\": \"kg\", \"notes\": \"\"}\n"
            "6. {\"type\": \"art_day\", \"date\": \"YYYY-MM-DD\", \"note\": \"\"} (when user says they drew/painted/did art)\n"
            "7. {\"type\": \"art_item\", \"title\": \"...\", \"status\": \"idea|wip|finished\", \"notes\": \"\"}\n"
            "8. {\"type\": \"game\", \"title\": \"...\", \"status\": \"playing|want_to_play|finished|on_hold\", \"platform\": \"\", \"rating\": null, \"notes\": \"\"}\n"
            f"Default weight unit if unspecified: {default_unit}. "
            "If the user mentions multiple things in one sentence, output multiple actions."
        )
        try:
            raw_reply, model_used = await _call_groq_chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": clean},
                ],
                api_key=api_key,
                model=model,
                json_mode=True,
                temperature=0.1,
            )
            parsed = _extract_json_object(raw_reply)
            if not isinstance(parsed.get("actions"), list) or not parsed["actions"]:
                raise ValueError("No actions returned")
            ai_used = True
        except Exception as exc:
            warning = f"Groq unavailable ({exc}); used local quick-parse."
            parsed = _fallback_parse(clean, today, default_unit)
    else:
        parsed = _fallback_parse(clean, today, default_unit)

    results = execute_actions(parsed.get("actions", []), default_unit=default_unit)
    if not results:
        results = execute_actions([{"type": "note", "title": "", "content": clean}], default_unit=default_unit)

    summary = str(parsed.get("summary") or "").strip()
    if not summary:
        summary = " · ".join(r["label"] for r in results)

    saved = db.record_capture(
        raw_text=clean,
        source=source,
        summary=summary,
        actions=[{"type": r["type"], "label": r["label"]} for r in results],
    )
    return {
        "capture": saved,
        "summary": summary,
        "results": results,
        "ai_used": ai_used,
        "model": model_used,
        "warning": warning,
    }


def _build_life_context(question: str) -> tuple[str, list[dict[str, Any]]]:
    hits = db.search_everything(question, limit=18)
    recent_notes = db.list_notes()[:6]
    open_tasks = db.list_tasks(list_type="daily", include_done=False)[:10]
    want_tasks = db.list_tasks(list_type="want_to_do", include_done=False)[:8]
    recent_gym = db.list_gym_logs(limit=10)
    gym_stats = db.get_gym_exercise_stats()[:10]
    art_items = db.list_art_items()[:8]
    games = db.list_games()[:10]

    lines: list[str] = [f"Today: {db.today_str()}"]
    if hits:
        lines.append("\n[Top Direct Search Matches]")
        for h in hits[:12]:
            lines.append(f"- ({h['type']} #{h['id']}) {h['title']} | {h['meta']}: {h['snippet']}")

    if recent_notes:
        lines.append("\n[Recent Notes]")
        for n in recent_notes:
            tags = " ".join(f"#{t}" for t in n["tags"])
            lines.append(f"- Note #{n['id']} '{n['title']}' [{tags}]: {n['content'][:320]}")

    if open_tasks or want_tasks:
        lines.append("\n[Open Tasks & Want-to-Do]")
        for t in open_tasks:
            lines.append(f"- Daily Task: {t['title']}" + (f" (due {t['due_date']})" if t["due_date"] else ""))
        for w in want_tasks:
            lines.append(f"- Want to Do: {w['title']}")

    if recent_gym or gym_stats:
        lines.append("\n[Gym Logs & Personal Bests]")
        for g in recent_gym:
            w = f"{g['weight']}{g['unit']}" if g["weight"] is not None else "bodyweight"
            lines.append(f"- {g['date']}: {g['exercise']} {g['sets']}x{g['reps']} @ {w} {g['notes']}")
        for s in gym_stats:
            pb = f"{s['max_weight']}{s['max_weight_unit']}" if s["max_weight"] is not None else f"{s['max_reps']} reps"
            lines.append(f"- PB {s['exercise']}: max {pb}, last on {s['last_date']}")

    if art_items:
        lines.append("\n[Art Tracker]")
        for a in art_items:
            lines.append(f"- [{a['status']}] {a['title']}: {a['notes'][:140]}")

    if games:
        lines.append("\n[Gaming Tracker]")
        for g in games:
            r = f" ({g['rating']}/10)" if g["rating"] else ""
            lines.append(f"- [{g['status']}] {g['title']}{r}: {g['notes'][:140]}")

    return "\n".join(lines), hits


async def ask_my_life(question: str) -> dict[str, Any]:
    """Answer natural-language questions over notes, tasks, gym, art, and gaming logs."""
    clean = (question or "").strip()
    if not clean:
        raise ValueError("Question cannot be empty.")

    context_text, hits = _build_life_context(clean)
    settings = load_settings()
    api_key = settings.get("groq_api_key", "").strip()
    model = settings.get("groq_model", "llama-3.3-70b-versatile")

    if not api_key:
        if hits:
            preview = "\n".join(f"• [{h['type'].upper()}] {h['title']} — {h['snippet'][:110]}" for h in hits[:6])
            answer = (
                "Groq API key is not configured yet, so here are your closest local matches:\n\n"
                + preview
            )
        else:
            answer = "No matching records found locally. Add your Groq API key in Settings for full AI Q&A."
        return {"answer": answer, "sources": hits, "ai_used": False, "model": None}

    system_prompt = (
        "You are Personal Life Hub's helpful assistant. Answer the user's question concisely and accurately "
        "using ONLY the personal records provided in the context below. Cite relevant note titles or dates when helpful. "
        "If the context does not contain the answer, say so clearly without inventing facts."
    )
    try:
        answer, model_used = await _call_groq_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {clean}"},
            ],
            api_key=api_key,
            model=model,
            json_mode=False,
            temperature=0.2,
        )
        return {"answer": answer.strip(), "sources": hits, "ai_used": True, "model": model_used}
    except Exception as exc:
        fallback_msg = (
            f"Groq request failed ({exc}). Showing local matches instead."
            if hits
            else f"Groq request failed ({exc}) and no local matches were found."
        )
        return {"answer": fallback_msg, "sources": hits, "ai_used": False, "model": None}
