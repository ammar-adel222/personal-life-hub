"""Groq AI smart capture, action execution, local fallback, and Ask My Life Q&A."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app import db, watchlist
from app.config import ALLOWED_GROQ_MODELS, load_settings

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
FALLBACK_CHAT_MODEL = "openai/gpt-oss-20b"
_NON_CHAT_SUBSTRINGS = ("whisper", "orpheus", "guard", "safeguard", "tts", "audio")


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


def _candidate_chat_models(preferred: str) -> list[str]:
    ordered: list[str] = []
    for m in (preferred, *ALLOWED_GROQ_MODELS, FALLBACK_CHAT_MODEL):
        if m and m not in ordered:
            ordered.append(m)
    return ordered


async def discover_live_chat_models(client: httpx.AsyncClient, api_key: str) -> list[str]:
    """Query Groq /models to dynamically discover available chat-capable models."""
    try:
        resp = await client.get(
            f"{GROQ_BASE_URL}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        if resp.status_code != 200:
            return []
        models = []
        for entry in resp.json().get("data", []):
            mid = str(entry.get("id") or "").strip()
            if not mid:
                continue
            low = mid.lower()
            if any(bad in low for bad in _NON_CHAT_SUBSTRINGS):
                continue
            models.append(mid)
        return models
    except Exception:
        return []


async def _call_groq_chat(
    messages: list[dict[str, str]],
    api_key: str,
    model: str,
    json_mode: bool = False,
    temperature: float = 0.2,
) -> tuple[str, str]:
    """Call Groq chat completions with model fallback and dynamic /models discovery."""
    candidates = _candidate_chat_models(model)
    tried: set[str] = set()
    last_error = "Unknown Groq error"

    async with httpx.AsyncClient(timeout=25.0) as client:
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
            if resp.status_code == 404 and not discovered:
                discovered = True
                for live_model in await discover_live_chat_models(client, api_key):
                    if live_model not in tried and live_model not in candidates:
                        candidates.append(live_model)
            if resp.status_code in (429, 500, 502, 503, 404) and idx < len(candidates):
                continue
            if idx < len(candidates):
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
        elif lower.startswith("watchlist:") or lower.startswith("playlist:") or "youtube.com/playlist" in lower or "youtu.be/" in lower:
            url_m = re.search(r"https?://[^\s]+", text)
            u = url_m.group(0).rstrip(".,)") if url_m else ""
            t_clean = re.sub(r"^(?:watchlist|playlist)\s*:\s*", "", text, flags=re.IGNORECASE)
            if u:
                t_clean = t_clean.replace(u, "").strip(" -—:")
            actions.append({"type": "watchlist", "title": t_clean or u, "url": u, "content_type": "playlist"})
        elif not lower.startswith("note:") and re.search(
            r"\b(?:today'?s?\s+tasks?|for\s+today|task\s+list|make\s+(?:a|the)\s+tasks?|create\s+(?:a|the)\s+tasks?|add\s+(?:a\s+|the\s+)?tasks?|for\s+the\s+next\s+\d*\s*(?:days?|weeks?))\b",
            lower,
        ):
            actions.append({"type": "task", "title": text[:120], "priority": "normal", "category": ""})
        elif lower.startswith("note:") or "\n" in text or len(text) > 120:
            clean_note = text[5:].strip() if lower.startswith("note:") else text
            actions.append({"type": "note", "title": "", "content": clean_note, "tags": []})
        else:
            actions.append({"type": "task", "title": text, "priority": "normal", "category": ""})

    return {
        "summary": f"Saved via local quick-parse ({actions[0]['type'].replace('_', ' ')})",
        "actions": actions,
    }


def _infer_note_folder(text: str) -> str:
    low = (text or "").lower()
    if any(k in low for k in ("lecture", "course", "professor", "class ", "semester", "stanford", "MIT")):
        return "Lectures"
    if any(k in low for k in ("project", "repo", "deploy", "backend", "frontend", "api", "roadmap", "milestone")):
        return "Projects"
    if any(k in low for k in ("study", "research", "paper", "chapter", "book", "exam", "concept", "algorithm")):
        return "Study"
    if any(k in low for k in ("idea", "brainstorm", "Concept", "sketch")):
        return "Ideas"
    return "Personal"


def _offline_organize_note(
    raw_content: str,
    current_title: str = "",
    current_folder: str = "",
    current_tags: list[str] | None = None,
    style: str = "structured",
) -> dict[str, Any]:
    """Deterministic local fallback that cleans prose into Markdown sections and checklists."""
    text = (raw_content or "").strip()
    title = (current_title or "").strip()
    if not text and not title:
        raise ValueError("Note content cannot be empty.")

    lines = text.splitlines() if text else [title]
    has_headings = any(ln.lstrip().startswith("#") for ln in lines)
    checklist_lines: list[str] = []
    body_blocks: list[str] = []
    in_code = False

    if not has_headings:
        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith("```"):
                in_code = not in_code
                body_blocks.append(ln)
                continue
            if in_code:
                body_blocks.append(ln)
                continue
            if re.match(r"^[-*]\s*\[[ xX]\]\s+", stripped):
                checklist_lines.append(stripped)
            elif stripped:
                body_blocks.append(stripped)

        formatted_points: list[str] = []
        in_code = False
        for blk in body_blocks:
            if blk.strip().startswith("```"):
                in_code = not in_code
                formatted_points.append(blk)
                continue
            if in_code:
                formatted_points.append(blk)
                continue
            if re.match(r"^([-*]|\d+\.|>|\|)\s", blk):
                formatted_points.append(blk)
            elif len(blk) > 140 and ". " in blk:
                for sent in re.split(r"(?<=[.!?])\s+", blk):
                    s = sent.strip()
                    if s:
                        formatted_points.append(f"- {s}")
            else:
                formatted_points.append(f"- {blk}")

        heading_label = {
            "lecture": "## Key Concepts",
            "concise": "## Key Takeaways",
        }.get(style, "## Key Notes")
        parts: list[str] = []
        if formatted_points:
            parts.append(heading_label + "\n\n" + "\n".join(formatted_points))
        if checklist_lines:
            parts.append("## Action & Research Items\n\n" + "\n".join(checklist_lines))
        organized_content = "\n\n".join(parts).strip() or text
    else:
        organized_content = text

    plain_preview = re.sub(r"[#>*`\[\]_~]+", " ", text or title)
    plain_preview = re.sub(r"\s+", " ", plain_preview).strip()
    first_sent = re.split(r"(?<=[.!?])\s+", plain_preview)[0] if plain_preview else ""
    summary = first_sent[:160].strip()

    if not title or title.lower().startswith("untitled"):
        words = [w for w in re.split(r"\s+", first_sent) if w]
        title = " ".join(words[:7]).rstrip(".,;:")[:75] or "Structured Note"

    folder = (current_folder or "").strip() or _infer_note_folder(f"{title} {text}")
    tags = db._clean_tags(current_tags)
    if not tags and folder:
        tags = [folder.lower()]

    return {
        "title": title,
        "folder": folder,
        "summary": summary,
        "tags": tags,
        "content": organized_content,
    }


async def organize_note_text(
    raw_content: str,
    current_title: str = "",
    current_folder: str = "",
    current_tags: list[str] | str | None = None,
    style: str = "structured",
) -> dict[str, Any]:
    """Turn messy notes or voice transcripts into clean structured Markdown with summary & tags."""
    clean_content = (raw_content or "").strip()
    clean_title = (current_title or "").strip()
    if not clean_content and not clean_title:
        raise ValueError("Note content cannot be empty.")

    norm_style = style if style in ("structured", "lecture", "concise") else "structured"
    base_tags = db._clean_tags(current_tags)
    settings = load_settings()
    api_key = settings.get("groq_api_key", "").strip()
    model = settings.get("groq_model", "openai/gpt-oss-120b")

    if not api_key:
        organized = _offline_organize_note(
            clean_content,
            current_title=clean_title,
            current_folder=current_folder,
            current_tags=base_tags,
            style=norm_style,
        )
        return {**organized, "ai_used": False, "model": None, "warning": None}

    style_guidance = {
        "structured": (
            "Format with clear `##` topical headings, clean bullet points, **bold** key terms, "
            "a `> [!NOTE]` or `> [!IMPORTANT]` callout for the main takeaway if helpful, "
            "Markdown tables for comparisons/numbers if applicable, and `- [ ]` items under "
            "`## Action & Research Items` for follow-ups."
        ),
        "lecture": (
            "Structure as a clean study/lecture note using sections like `## Overview`, "
            "`## Core Concepts`, `> [!IMPORTANT]` Key Takeaways callout, and "
            "`## To Research / Review` (`- [ ]` checkboxes) for open questions."
        ),
        "concise": (
            "Distill into a tight executive summary: one `> [!NOTE]` TL;DR callout, compact `##` "
            "bullet lists, and `- [ ]` action items if any exist."
        ),
    }[norm_style]

    system_prompt = (
        "You are an expert personal knowledge editor inside Personal Life Hub.\n"
        "Transform the user's raw note or spoken transcript into a crisp, well-organized Markdown note.\n"
        "Return ONLY valid JSON: "
        '{"title": "...", "folder": "...", "summary": "1-2 sentence TL;DR", "tags": ["..."], "content": "..."}.\n'
        "Rules:\n"
        "1. Fix obvious speech-to-text / Whisper phonetic typos (e.g. 'to the DEM' -> 'to the gym' if workout context) "
        "WITHOUT inventing facts. Preserve all specific numbers, weights, sets/reps, dates, links, and code blocks.\n"
        "2. Preserve existing Markdown checkboxes `- [x]` and `- [ ]` accurately (do not uncheck done items).\n"
        f"3. {style_guidance}\n"
        "4. Do NOT start `content` with `# {title}` since `title` is stored separately.\n"
        f"5. Choose an appropriate `folder` from {list(db.DEFAULT_NOTE_FOLDERS)} (or keep the existing folder if clearly right).\n"
        "6. Provide 2-5 concise lowercase `tags` without `#`."
    )
    user_payload = (
        f"Current title: {clean_title or '(auto-generate a clean title)'}\n"
        f"Current folder: {(current_folder or '').strip() or '(auto-pick)'}\n"
        f"Current tags: {', '.join(base_tags) if base_tags else '(none)'}\n\n"
        f"Note text:\n{clean_content or clean_title}"
    )

    try:
        raw_reply, model_used = await _call_groq_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            api_key=api_key,
            model=model,
            json_mode=True,
            temperature=0.15,
        )
        parsed = _extract_json_object(raw_reply)
        out_content = str(parsed.get("content") or "").strip()
        if not out_content:
            raise ValueError("AI returned empty note content")
        out_title = str(parsed.get("title") or clean_title).strip() or "Structured Note"
        # Strip duplicate leading H1 if model repeated the title
        first_line = out_content.splitlines()[0].strip() if out_content.splitlines() else ""
        if first_line.startswith("# ") and first_line[2:].strip().lower() == out_title.lower():
            out_content = "\n".join(out_content.splitlines()[1:]).lstrip("\r\n")
        out_folder = str(parsed.get("folder") or current_folder).strip() or _infer_note_folder(
            f"{out_title} {out_content}"
        )
        out_summary = str(parsed.get("summary") or "").strip()
        out_tags = db._clean_tags(parsed.get("tags") or base_tags)
        return {
            "title": out_title[:120],
            "folder": out_folder[:60],
            "summary": out_summary[:320],
            "tags": out_tags,
            "content": out_content,
            "ai_used": True,
            "model": model_used,
            "warning": None,
        }
    except Exception as exc:
        organized = _offline_organize_note(
            clean_content,
            current_title=clean_title,
            current_folder=current_folder,
            current_tags=base_tags,
            style=norm_style,
        )
        return {
            **organized,
            "ai_used": False,
            "model": None,
            "warning": f"Groq unavailable ({exc}); used local organizer.",
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
    if mode == "study":
        dur_match = re.search(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m)\b", text, flags=re.IGNORECASE)
        mins = 30.0
        topic_text = text
        if dur_match:
            val = float(dur_match.group(1))
            mins = val * 60.0 if dur_match.group(2).lower().startswith("h") else val
            topic_text = (text[: dur_match.start()] + text[dur_match.end() :]).strip(" -—:,") or text
        return {
            "summary": f"Logged {mins:g}m study session",
            "actions": [
                {
                    "type": "study_session",
                    "subject": "Study",
                    "topic": topic_text[:120],
                    "duration_minutes": mins,
                    "date": today,
                }
            ],
        }
    if mode == "watchlist":
        url_m = re.search(r"https?://[^\s]+", text)
        u = url_m.group(0).rstrip(".,)") if url_m else ""
        t_clean = text.replace(u, "").strip(" -—:") if u else text
        return {
            "summary": f"Saved to Watchlist: {t_clean or u}",
            "actions": [{"type": "watchlist", "title": t_clean or u, "url": u, "content_type": "playlist"}],
        }
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
                    folder=str(act.get("folder") or ""),
                    summary=str(act.get("summary") or ""),
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

        elif atype == "study_session":
            dur_mins = act.get("duration_minutes")
            dur_secs = act.get("duration_seconds")
            try:
                mins_val = float(dur_mins) if dur_mins is not None and str(dur_mins) != "" else None
            except (TypeError, ValueError):
                mins_val = None
            try:
                secs_val = int(dur_secs) if dur_secs is not None and str(dur_secs) != "" else None
            except (TypeError, ValueError):
                secs_val = None
            if (secs_val and secs_val > 0) or (mins_val and mins_val > 0):
                item = db.create_study_session(
                    subject=str(act.get("subject") or "Study"),
                    topic=str(act.get("topic") or ""),
                    duration_seconds=secs_val,
                    duration_minutes=mins_val,
                    mode="manual",
                    day_date=str(act.get("date") or today).strip(),
                    notes=str(act.get("notes") or ""),
                )
                executed.append(
                    {
                        "type": "study_session",
                        "label": f"Study {item['duration_minutes']:g}m: {item['subject']}",
                        "item": item,
                    }
                )

        elif atype == "watchlist":
            w_url = str(act.get("url") or "").strip()
            w_title = str(act.get("title") or "").strip()
            if w_url and not watchlist.is_safe_http_url(w_url):
                continue
            if w_title or w_url:
                info = watchlist.detect_url_info(w_url) if w_url else {"source": "", "content_type": "playlist"}
                item = db.create_watchlist_item(
                    title=w_title,
                    url=w_url,
                    content_type=str(act.get("content_type") or info["content_type"] or "playlist"),
                    source=info["source"],
                    category=str(act.get("category") or ""),
                    tags=act.get("tags") or [],
                    notes=str(act.get("notes") or ""),
                )
                executed.append(
                    {
                        "type": "watchlist",
                        "label": f"Watchlist: {item['title']}",
                        "item": item,
                    }
                )

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
    model = settings.get("groq_model", "openai/gpt-oss-120b")

    ai_used = False
    model_used: str | None = None
    warning: str | None = None

    if mode == "note" and api_key and len(clean) > 40:
        org = await organize_note_text(clean, style="structured")
        parsed = {
            "summary": f"Saved organized note: {org['title']}",
            "actions": [
                {
                    "type": "note",
                    "title": org["title"],
                    "folder": org["folder"],
                    "summary": org["summary"],
                    "tags": org["tags"],
                    "content": org["content"],
                }
            ],
        }
        ai_used = bool(org["ai_used"])
        model_used = org["model"]
        warning = org["warning"]
    elif mode != "auto":
        parsed = _explicit_mode_actions(clean, mode, today, default_unit)
    elif api_key:
        system_prompt = (
            f"You are the fast personal assistant inside Personal Life Hub. Today's date is {today}.\n"
            "Classify the user's quick note/voice dump into one or more structured JSON actions.\n"
            "Return ONLY a JSON object: {\"summary\": \"short friendly confirmation\", \"actions\": [...]}.\n"
            "Allowed action schemas:\n"
            "1. {\"type\": \"task\", \"title\": \"...\", \"priority\": \"normal|high\", \"due_date\": \"YYYY-MM-DD or null\", \"category\": \"\"}\n"
            "2. {\"type\": \"want_to_do\", \"title\": \"...\", \"category\": \"\"} (bucket list, someday goals, things to try)\n"
            "3. {\"type\": \"note\", \"title\": \"...\", \"folder\": \"Lectures|Projects|Study|Personal|Ideas\", \"summary\": \"1-sentence TL;DR\", \"content\": \"clean Markdown with ## headings, bullets & - [ ] follow-ups\", \"tags\": [\"...\"]}\n"
            "4. {\"type\": \"gym_day\", \"date\": \"YYYY-MM-DD\", \"note\": \"\"} (when user mentions going to gym / working out)\n"
            "5. {\"type\": \"gym_set\", \"date\": \"YYYY-MM-DD\", \"exercise\": \"...\", \"sets\": 3, \"reps\": 10, \"weight\": 80, \"unit\": \"kg\", \"notes\": \"\"}\n"
            "6. {\"type\": \"art_day\", \"date\": \"YYYY-MM-DD\", \"note\": \"\"} (when user says they drew/painted/did art)\n"
            "7. {\"type\": \"art_item\", \"title\": \"...\", \"status\": \"idea|wip|finished\", \"notes\": \"\"}\n"
            "8. {\"type\": \"game\", \"title\": \"...\", \"status\": \"playing|want_to_play|finished|on_hold\", \"platform\": \"\", \"rating\": null, \"notes\": \"\"}\n"
            "9. {\"type\": \"study_session\", \"subject\": \"Study|Lectures|Coding|Reading|Research|Art\", \"topic\": \"...\", \"duration_minutes\": 45, \"date\": \"YYYY-MM-DD\", \"notes\": \"\"}\n"
            "10. {\"type\": \"watchlist\", \"title\": \"...\", \"url\": \"https://...\", \"content_type\": \"playlist|video|course|podcast|article\", \"category\": \"\", \"tags\": [\"...\"], \"notes\": \"\"} "
            "(ONLY emit watchlist when the user explicitly asks to add/save to their playlist or watchlist or pastes a playlist/video URL to bookmark — NEVER for statements like 'I will watch X' or 'watch 5 lectures', use 'task' instead)\n"
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

    raw_actions = parsed.get("actions", [])
    if mode == "auto" and not watchlist.has_explicit_watchlist_intent(clean):
        sanitized_actions: list[dict[str, Any]] = []
        for act in raw_actions:
            if isinstance(act, dict) and str(act.get("type") or "").strip().lower() == "watchlist":
                sanitized_actions.append(
                    {
                        "type": "task",
                        "title": str(act.get("title") or clean[:120]).strip(),
                        "priority": "normal",
                        "category": str(act.get("category") or ""),
                    }
                )
            else:
                sanitized_actions.append(act)
        raw_actions = sanitized_actions

    results = execute_actions(raw_actions, default_unit=default_unit)
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
            folder_lbl = f" ({n['folder']})" if n.get("folder") else ""
            body_preview = n.get("summary") or n["content"][:320]
            lines.append(f"- Note #{n['id']} '{n['title']}'{folder_lbl} [{tags}]: {body_preview}")

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

    study_stats = db.get_study_stats()
    recent_study = db.list_study_sessions(limit=8)
    if study_stats["total_sessions"] or recent_study:
        lines.append(
            f"\n[Study & Focus Stats]\n"
            f"- Today: {study_stats['today_minutes']}m ({study_stats['today_sessions_count']} sessions), "
            f"7-day total: {study_stats['week_hours']}h, Streak: {study_stats['streak_days']}d"
        )
        for rs in recent_study:
            lines.append(
                f"- {rs['date']}: {rs['subject']} ({rs['duration_minutes']}m) {rs['topic']} {rs['notes']}"
            )

    watchlist_items = db.list_watchlist()[:8]
    if watchlist_items:
        lines.append("\n[Watchlist & Playlists]")
        for w in watchlist_items:
            prog = f" ({w['progress_current']}/{w['progress_total']})" if w["progress_total"] > 0 else ""
            lines.append(f"- [{w['status']}] {w['title']}{prog}: {w['url']} {w['notes'][:100]}")

    return "\n".join(lines), hits


async def ask_my_life(question: str) -> dict[str, Any]:
    """Answer natural-language questions over notes, tasks, gym, art, and gaming logs."""
    clean = (question or "").strip()
    if not clean:
        raise ValueError("Question cannot be empty.")

    context_text, hits = _build_life_context(clean)
    settings = load_settings()
    api_key = settings.get("groq_api_key", "").strip()
    model = settings.get("groq_model", "openai/gpt-oss-120b")

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
