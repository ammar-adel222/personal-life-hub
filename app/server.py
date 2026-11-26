"""FastAPI backend server for Personal Life Hub."""

from __future__ import annotations

import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import agent, ai, db, dialogs, notes_sync, tts, voice
from app.config import (
    get_data_dir,
    get_media_dir,
    get_public_settings,
    load_settings,
    update_settings,
)

ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_data_dir()
    db.init_db()
    notes_folder = load_settings().get("notes_folder", "").strip()
    if notes_folder:
        try:
            notes_sync.sync_notes_folder(notes_folder)
        except Exception:
            pass
    yield


app = FastAPI(title="Personal Life Hub", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------

class TaskCreate(BaseModel):
    title: str
    list_type: str = "daily"
    category: str = ""
    priority: str = "normal"
    start_date: str | None = None
    due_date: str | None = None
    timeline: str = ""
    frequency: str = ""
    status: str = "not_started"
    progress_note: str = ""
    checklist_items: list[str] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    title: str | None = None
    list_type: str | None = None
    category: str | None = None
    priority: str | None = None
    start_date: str | None = None
    due_date: str | None = None
    timeline: str | None = None
    frequency: str | None = None
    done: bool | None = None
    status: str | None = None
    progress_note: str | None = None


class TaskItemCreate(BaseModel):
    text: str


class TaskItemUpdate(BaseModel):
    text: str | None = None
    done: bool | None = None


class NoteAppendPayload(BaseModel):
    text: str = ""
    checklist_items: list[str] = Field(default_factory=list)
    add_tags: list[str] | str = Field(default_factory=list)


class NoteChecklistTogglePayload(BaseModel):
    item_index: int | None = None
    match_text: str | None = None
    done: bool | None = None


class NoteCreate(BaseModel):
    title: str = ""
    content: str = ""
    tags: list[str] | str = Field(default_factory=list)
    pinned: bool = False
    save_to_folder: bool = False


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | str | None = None
    pinned: bool | None = None


class ActivityDayPayload(BaseModel):
    domain: str
    date: str | None = None
    note: str = ""
    mode: str = "toggle"  # "toggle" or "set"


class GymLogCreate(BaseModel):
    exercise: str
    sets: int = 1
    reps: int = 0
    weight: float | None = None
    unit: str = "kg"
    notes: str = ""
    date: str | None = None


class GymLogUpdate(BaseModel):
    exercise: str | None = None
    sets: int | None = None
    reps: int | None = None
    weight: float | None = None
    unit: str | None = None
    notes: str | None = None
    date: str | None = None


class ArtItemCreate(BaseModel):
    title: str
    status: str = "idea"
    notes: str = ""


class ArtItemUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    notes: str | None = None


class LocalImagePayload(BaseModel):
    path: str


class GameCreate(BaseModel):
    title: str
    status: str = "want_to_play"
    platform: str = ""
    rating: int | None = None
    notes: str = ""


class GameUpdate(BaseModel):
    title: str | None = None
    status: str | None = None
    platform: str | None = None
    rating: int | None = None
    notes: str | None = None


class SettingsUpdate(BaseModel):
    groq_api_key: str | None = None
    groq_model: str | None = None
    voice_mode: str | None = None
    mic_device: int | str | None = None
    notes_folder: str | None = None
    weight_unit: str | None = None
    tts_mode: str | None = None
    tts_provider: str | None = None
    tts_voice: str | None = None
    auto_silence: bool | None = None
    silence_seconds: float | None = None


class CapturePayload(BaseModel):
    text: str
    mode: str = "auto"
    source: str = "text"


class AskPayload(BaseModel):
    question: str


class AgentSessionCreate(BaseModel):
    title: str = "Voice Session"


class AgentChatPayload(BaseModel):
    text: str
    session_id: int | None = None
    source: str = "text"


class TestGroqPayload(BaseModel):
    api_key: str | None = None


class NotesSyncPayload(BaseModel):
    folder: str | None = None


class VoiceStartPayload(BaseModel):
    auto_silence: bool | None = None
    silence_seconds: float | None = None


class VoiceStopPayload(BaseModel):
    voice_mode: str | None = None
    session_id: int | None = None
    use_agent: bool = False


class TTSPayload(BaseModel):
    text: str
    provider: str | None = None
    voice: str | None = None


# ---------------------------------------------------------------------------
# Health & Dashboard
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict[str, Any]:
    db.init_db()
    return {"status": "ok", "today": db.today_str()}


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    db.init_db()
    return db.get_dashboard()


@app.get("/api/search")
def search(q: str = Query("", max_length=300), limit: int = 25) -> dict[str, Any]:
    return {"query": q, "results": db.search_everything(q, limit=limit)}


# ---------------------------------------------------------------------------
# Tasks & Want to Do
# ---------------------------------------------------------------------------

@app.get("/api/tasks")
def get_tasks(
    list_type: str | None = None,
    include_done: bool = True,
    status: str | None = None,
) -> dict[str, Any]:
    return {"items": db.list_tasks(list_type=list_type, include_done=include_done, status=status)}


@app.post("/api/tasks", status_code=201)
def post_task(payload: TaskCreate) -> dict[str, Any]:
    try:
        item = db.create_task(
            title=payload.title,
            list_type=payload.list_type,
            category=payload.category,
            priority=payload.priority,
            due_date=payload.due_date,
            status=payload.status,
            progress_note=payload.progress_note,
            checklist_items=payload.checklist_items,
            start_date=payload.start_date,
            timeline=payload.timeline,
            frequency=payload.frequency,
        )
        return {"item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/tasks/{task_id}")
def patch_task(task_id: int, payload: TaskUpdate) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)
    try:
        item = db.update_task(task_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"item": item}


@app.delete("/api/tasks/{task_id}")
def remove_task(task_id: int) -> dict[str, Any]:
    if not db.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"deleted": True}


@app.post("/api/tasks/{task_id}/items", status_code=201)
def post_task_item(task_id: int, payload: TaskItemCreate) -> dict[str, Any]:
    try:
        item = db.add_task_item(task_id, payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"item": item}


@app.patch("/api/tasks/{task_id}/items/{item_id}")
def patch_task_item(task_id: int, item_id: int, payload: TaskItemUpdate) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)
    try:
        item = db.update_task_item(task_id, item_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Task or checklist item not found.")
    return {"item": item}


@app.delete("/api/tasks/{task_id}/items/{item_id}")
def remove_task_item(task_id: int, item_id: int) -> dict[str, Any]:
    item = db.delete_task_item(task_id, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Task or checklist item not found.")
    return {"item": item}


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

@app.get("/api/notes")
def get_notes(q: str = "", tag: str = "") -> dict[str, Any]:
    return {"items": db.list_notes(query=q, tag=tag)}


@app.post("/api/notes", status_code=201)
def post_note(payload: NoteCreate) -> dict[str, Any]:
    try:
        item = db.create_note(
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            pinned=payload.pinned,
        )
        if payload.save_to_folder:
            folder = load_settings().get("notes_folder", "").strip()
            if not folder:
                raise ValueError("Configure a local notes folder in Settings first.")
            item = notes_sync.save_new_note_to_folder(item, folder)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"item": item}


@app.patch("/api/notes/{note_id}")
def patch_note(note_id: int, payload: NoteUpdate) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)
    try:
        item = db.update_note(note_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Note not found.")
    if item.get("source_path") and any(k in patch for k in ("title", "content")):
        try:
            item = notes_sync.write_note_to_source(item)
        except OSError:
            pass
    return {"item": item}


@app.delete("/api/notes/{note_id}")
def remove_note(note_id: int) -> dict[str, Any]:
    if not db.delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found.")
    return {"deleted": True}


@app.post("/api/notes/{note_id}/append")
def post_note_append(note_id: int, payload: NoteAppendPayload) -> dict[str, Any]:
    item = db.append_to_note(
        note_id=note_id,
        text=payload.text,
        checklist_items=payload.checklist_items,
        add_tags=payload.add_tags,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Note not found.")
    if item.get("source_path"):
        try:
            item = notes_sync.write_note_to_source(item)
        except OSError:
            pass
    return {"item": item}


@app.post("/api/notes/{note_id}/checklist-toggle")
def post_note_checklist_toggle(
    note_id: int, payload: NoteChecklistTogglePayload
) -> dict[str, Any]:
    item = db.toggle_note_checklist_line(
        note_id=note_id,
        item_index=payload.item_index,
        match_text=payload.match_text,
        done=payload.done,
    )
    if not item:
        raise HTTPException(status_code=404, detail="Note not found.")
    if item.get("source_path"):
        try:
            item = notes_sync.write_note_to_source(item)
        except OSError:
            pass
    return {"item": item}


@app.post("/api/notes/sync")
def sync_notes(payload: NotesSyncPayload | None = None) -> dict[str, Any]:
    folder = (payload.folder if payload and payload.folder else "") or load_settings().get("notes_folder", "")
    try:
        return notes_sync.sync_notes_folder(folder)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Activity Days (Gym & Art consistency calendars)
# ---------------------------------------------------------------------------

@app.get("/api/activity-days")
def get_activity_days(
    domain: str = Query(..., pattern="^(gym|art)$"),
    month: str | None = None,
) -> dict[str, Any]:
    try:
        return db.get_activity_summary(domain=domain, month=month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/activity-days")
def post_activity_day(payload: ActivityDayPayload) -> dict[str, Any]:
    try:
        if payload.mode == "set":
            summary = db.set_activity_day(
                domain=payload.domain, day_date=payload.date, note=payload.note
            )
            summary["active"] = True
            return summary
        return db.toggle_activity_day(
            domain=payload.domain, day_date=payload.date, note=payload.note
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/activity-days")
def remove_activity_day(
    domain: str = Query(..., pattern="^(gym|art)$"),
    date: str | None = None,
) -> dict[str, Any]:
    try:
        summary = db.delete_activity_day(domain=domain, day_date=date)
        summary["active"] = False
        return summary
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Gym Logs & Personal Bests
# ---------------------------------------------------------------------------

@app.get("/api/gym/logs")
def get_gym_logs(
    date: str | None = None,
    exercise: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    return {"items": db.list_gym_logs(day_date=date, exercise=exercise, limit=limit)}


@app.post("/api/gym/logs", status_code=201)
def post_gym_log(payload: GymLogCreate) -> dict[str, Any]:
    try:
        item = db.create_gym_log(
            exercise=payload.exercise,
            sets=payload.sets,
            reps=payload.reps,
            weight=payload.weight,
            unit=payload.unit,
            notes=payload.notes,
            day_date=payload.date,
        )
        return {"item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/gym/logs/{log_id}")
def patch_gym_log(log_id: int, payload: GymLogUpdate) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)
    try:
        item = db.update_gym_log(log_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Gym log not found.")
    return {"item": item}


@app.delete("/api/gym/logs/{log_id}")
def remove_gym_log(log_id: int) -> dict[str, Any]:
    if not db.delete_gym_log(log_id):
        raise HTTPException(status_code=404, detail="Gym log not found.")
    return {"deleted": True}


@app.get("/api/gym/exercises")
def get_gym_exercises() -> dict[str, Any]:
    return {"items": db.get_gym_exercise_stats()}


# ---------------------------------------------------------------------------
# Art Items & Reference Media
# ---------------------------------------------------------------------------

@app.get("/api/art")
def get_art(status: str | None = None) -> dict[str, Any]:
    return {"items": db.list_art_items(status=status)}


@app.post("/api/art", status_code=201)
def post_art(payload: ArtItemCreate) -> dict[str, Any]:
    try:
        item = db.create_art_item(
            title=payload.title, status=payload.status, notes=payload.notes
        )
        return {"item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/art/{item_id}")
def patch_art(item_id: int, payload: ArtItemUpdate) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)
    try:
        item = db.update_art_item(item_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Art item not found.")
    return {"item": item}


@app.delete("/api/art/{item_id}")
def remove_art(item_id: int) -> dict[str, Any]:
    if not db.delete_art_item(item_id):
        raise HTTPException(status_code=404, detail="Art item not found.")
    return {"deleted": True}


@app.post("/api/art/{item_id}/image")
async def upload_art_image(item_id: int, file: UploadFile = File(...)) -> dict[str, Any]:
    if not db.get_art_item(item_id):
        raise HTTPException(status_code=404, detail="Art item not found.")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format. Use PNG, JPG, WEBP, or GIF.",
        )
    dest_name = f"{uuid.uuid4().hex[:12]}{ext}"
    dest_path = get_media_dir() / dest_name
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded image file is empty.")
    dest_path.write_bytes(content)
    updated = db.add_art_image(item_id, dest_name)
    return {"item": updated, "filename": dest_name}


@app.post("/api/art/{item_id}/image/local")
def attach_local_art_image(item_id: int, payload: LocalImagePayload) -> dict[str, Any]:
    if not db.get_art_item(item_id):
        raise HTTPException(status_code=404, detail="Art item not found.")
    src = Path(payload.path.strip())
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=404, detail="Selected image file was not found.")
    ext = src.suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image format. Use PNG, JPG, WEBP, or GIF.",
        )
    dest_name = f"{uuid.uuid4().hex[:12]}{ext}"
    dest_path = get_media_dir() / dest_name
    shutil.copy2(src, dest_path)
    updated = db.add_art_image(item_id, dest_name)
    return {"item": updated, "filename": dest_name}


@app.delete("/api/art/{item_id}/image/{filename}")
def delete_art_image(item_id: int, filename: str) -> dict[str, Any]:
    updated = db.remove_art_image(item_id, filename)
    if not updated:
        raise HTTPException(status_code=404, detail="Art item not found.")
    return {"item": updated}


@app.get("/api/media/{filename}")
def get_media_file(filename: str) -> FileResponse:
    media_dir = get_media_dir().resolve()
    safe_name = Path(filename).name
    target = (media_dir / safe_name).resolve()
    if target.parent != media_dir or not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Media file not found.")
    return FileResponse(target)


# ---------------------------------------------------------------------------
# Games Tracker
# ---------------------------------------------------------------------------

@app.get("/api/games")
def get_games(status: str | None = None) -> dict[str, Any]:
    return {"items": db.list_games(status=status)}


@app.post("/api/games", status_code=201)
def post_game(payload: GameCreate) -> dict[str, Any]:
    try:
        item = db.create_game(
            title=payload.title,
            status=payload.status,
            platform=payload.platform,
            rating=payload.rating,
            notes=payload.notes,
        )
        return {"item": item}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/games/{game_id}")
def patch_game(game_id: int, payload: GameUpdate) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)
    try:
        item = db.update_game(game_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="Game not found.")
    return {"item": item}


@app.delete("/api/games/{game_id}")
def remove_game(game_id: int) -> dict[str, Any]:
    if not db.delete_game(game_id):
        raise HTTPException(status_code=404, detail="Game not found.")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Smart Capture, AI Search & Voice Input
# ---------------------------------------------------------------------------

@app.post("/api/capture")
async def post_capture(payload: CapturePayload) -> dict[str, Any]:
    try:
        return await ai.smart_capture(
            raw_text=payload.text,
            source=payload.source,
            mode=payload.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/captures")
def get_captures(limit: int = 20) -> dict[str, Any]:
    return {"items": db.list_captures(limit=limit)}


@app.post("/api/ai/ask")
async def post_ai_ask(payload: AskPayload) -> dict[str, Any]:
    try:
        return await ai.ask_my_life(payload.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/agent/sessions")
def get_agent_sessions(limit: int = 20) -> dict[str, Any]:
    return {"sessions": db.list_agent_sessions(limit=limit)}


@app.post("/api/agent/sessions", status_code=201)
def post_agent_session(payload: AgentSessionCreate | None = None) -> dict[str, Any]:
    title = payload.title if payload else "Voice Session"
    session = db.create_agent_session(title=title)
    session["messages"] = []
    return {"session": session}


@app.get("/api/agent/sessions/{session_id}")
def get_agent_session(session_id: int) -> dict[str, Any]:
    detail = db.get_agent_session_detail(session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Agent session not found.")
    return {"session": detail}


@app.delete("/api/agent/sessions/{session_id}")
def remove_agent_session(session_id: int) -> dict[str, Any]:
    if not db.delete_agent_session(session_id):
        raise HTTPException(status_code=404, detail="Agent session not found.")
    return {"deleted": True}


@app.post("/api/agent/chat")
async def post_agent_chat(payload: AgentChatPayload) -> dict[str, Any]:
    try:
        return await agent.run_agent_turn(
            user_text=payload.text,
            session_id=payload.session_id,
            source=payload.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/settings/test-groq")
async def test_groq(payload: TestGroqPayload | None = None) -> dict[str, Any]:
    key = (payload.api_key if payload and payload.api_key else "") or load_settings().get("groq_api_key", "")
    try:
        return await ai.verify_groq_key(key)
    except ai.AIError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Network error contacting Groq: {exc}") from exc


@app.get("/api/voice/devices")
def get_voice_devices() -> dict[str, Any]:
    return {"devices": voice.list_microphones()}


@app.get("/api/voice/status")
def get_voice_status() -> dict[str, Any]:
    return voice.recorder.status()


@app.post("/api/voice/start")
def post_voice_start(payload: VoiceStartPayload | None = None) -> dict[str, Any]:
    settings = load_settings()
    auto_silence = (
        payload.auto_silence
        if payload and payload.auto_silence is not None
        else settings.get("auto_silence", True)
    )
    silence_seconds = (
        payload.silence_seconds
        if payload and payload.silence_seconds is not None
        else settings.get("silence_seconds", 1.8)
    )
    try:
        return voice.recorder.start(
            device=settings.get("mic_device"),
            auto_silence=bool(auto_silence),
            silence_seconds=float(silence_seconds),
        )
    except voice.VoiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/voice/stop")
async def post_voice_stop(payload: VoiceStopPayload | None = None) -> dict[str, Any]:
    settings = load_settings()
    api_key = settings.get("groq_api_key", "").strip()
    mode = (payload.voice_mode if payload and payload.voice_mode else "") or settings.get("voice_mode", "auto_ai")
    use_agent = bool(payload.use_agent) if payload else False
    session_id = payload.session_id if payload else None
    try:
        wav_bytes = voice.recorder.stop()
        transcript = await voice.transcribe_wav_groq(wav_bytes, api_key)
    except voice.VoiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Whisper request failed: {exc}") from exc

    agent_result = None
    capture_result = None
    if mode == "auto_ai":
        if use_agent:
            agent_result = await agent.run_agent_turn(
                transcript, session_id=session_id, source="voice"
            )
        else:
            capture_result = await ai.smart_capture(transcript, source="voice", mode="auto")
    return {
        "transcript": transcript,
        "voice_mode": mode,
        "capture": capture_result,
        "agent": agent_result,
    }


@app.post("/api/voice/cancel")
def post_voice_cancel() -> dict[str, Any]:
    return voice.recorder.cancel()


@app.post("/api/voice/tts")
async def post_voice_tts(payload: TTSPayload) -> Response:
    res = await tts.synthesize_speech(
        text=payload.text,
        provider=payload.provider,
        voice=payload.voice,
    )
    if res.get("audio_bytes"):
        return Response(content=res["audio_bytes"], media_type="audio/wav")
    return JSONResponse(
        content={
            "fallback_local": True,
            "reason": res.get("reason", "Using built-in speech."),
        }
    )


# ---------------------------------------------------------------------------
# Settings & System Utilities
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return get_public_settings()


@app.patch("/api/settings")
def patch_settings(payload: SettingsUpdate) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)
    # Only overwrite API key if a non-empty value is provided, or if explicitly cleared via ""
    if "groq_api_key" in patch and patch["groq_api_key"] is None:
        patch.pop("groq_api_key")
    update_settings(patch)
    return get_public_settings()


@app.post("/api/system/pick-folder")
def system_pick_folder() -> dict[str, Any]:
    selected = dialogs.pick_folder()
    return {"path": selected}


@app.post("/api/system/pick-image")
def system_pick_image() -> dict[str, Any]:
    selected = dialogs.pick_image_file()
    return {"path": selected}


@app.post("/api/system/open-data-dir")
def system_open_data_dir() -> dict[str, Any]:
    data_dir = get_data_dir()
    if hasattr(os, "startfile"):
        os.startfile(str(data_dir))
    return {"opened": True, "path": str(data_dir)}


@app.get("/api/system/export")
def system_export() -> JSONResponse:
    db.init_db()
    data = db.export_all_data()
    return JSONResponse(
        content=data,
        headers={
            "Content-Disposition": 'attachment; filename="personal-life-hub-backup.json"'
        },
    )


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
