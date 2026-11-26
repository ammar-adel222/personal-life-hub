"""FastAPI backend server for Personal Life Hub."""

from __future__ import annotations

import os
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db, dialogs
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
    due_date: str | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    list_type: str | None = None
    category: str | None = None
    priority: str | None = None
    due_date: str | None = None
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
) -> dict[str, Any]:
    return {"items": db.list_tasks(list_type=list_type, include_done=include_done)}


@app.post("/api/tasks", status_code=201)
def post_task(payload: TaskCreate) -> dict[str, Any]:
    try:
        item = db.create_task(
            title=payload.title,
            list_type=payload.list_type,
            category=payload.category,
            priority=payload.priority,
            due_date=payload.due_date,
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
    except ValueError as exc:
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
    return {"item": item}


@app.delete("/api/notes/{note_id}")
def remove_note(note_id: int) -> dict[str, Any]:
    if not db.delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found.")
    return {"deleted": True}


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
