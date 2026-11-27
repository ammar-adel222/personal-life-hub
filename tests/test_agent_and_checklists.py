"""Tests for DB migration, 3-state checklists, note checklists, multi-turn agent loop, and TTS."""

from __future__ import annotations

import json
import sqlite3
from array import array

import httpx
from fastapi.testclient import TestClient

from app import agent, db, tools, voice
from app.server import app


def test_legacy_tasks_db_migration(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))
    db._initialized_db_paths.clear()

    # Create a legacy v0.1 tasks table without status or progress_note
    db_path = tmp_path / "lifehub.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            list_type TEXT NOT NULL DEFAULT 'daily',
            category TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT 'normal',
            due_date TEXT,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO tasks (title, list_type, done, created_at, completed_at) VALUES (?, 'daily', 1, '2026-11-20T10:00:00', '2026-11-20T11:00:00')",
        ("Legacy completed task",),
    )
    conn.execute(
        "INSERT INTO tasks (title, list_type, done, created_at) VALUES (?, 'daily', 0, '2026-11-20T10:05:00')",
        ("Legacy open task",),
    )
    conn.commit()
    conn.close()

    db.init_db()
    tasks = db.list_tasks()
    by_title = {t["title"]: t for t in tasks}
    assert by_title["Legacy completed task"]["status"] == "completed"
    assert by_title["Legacy completed task"]["done"] is True
    assert by_title["Legacy open task"]["status"] == "not_started"
    assert by_title["Legacy open task"]["done"] is False
    assert by_title["Legacy open task"]["timeline"] == ""
    assert by_title["Legacy open task"]["frequency"] == ""


def test_tasks_batch_timeline_and_frequency(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))
    assert len(tools.TOOL_SCHEMAS) == 19

    with TestClient(app) as client:
        res = tools.execute_tool(
            "create_tasks_batch",
            {
                "tasks": [
                    {
                        "title": "Stanford Agentic AI Lectures 1-5",
                        "list_type": "daily",
                        "start_date": "2026-11-27",
                        "due_date": "2026-12-07",
                        "timeline": "Next 10 days",
                        "frequency": "1 lecture every 2 days",
                        "checklist_items": ["Lecture 1", "Lecture 2", "Lecture 3", "Lecture 4", "Lecture 5"],
                    },
                    {
                        "title": "Draw 3 artworks",
                        "list_type": "daily",
                        "start_date": "2026-11-27",
                        "due_date": "2026-12-07",
                        "timeline": "Next 10 days",
                        "frequency": "3 artworks in 10 days",
                        "checklist_items": ["Artwork 1", "Artwork 2", "Artwork 3"],
                    },
                ]
            },
        )
        assert res["result"]["count"] == 2
        assert len(res["action_badges"]) == 2

        tasks = client.get("/api/tasks?list_type=daily").json()["items"]
        assert len(tasks) == 2
        by_title = {t["title"]: t for t in tasks}
        assert by_title["Stanford Agentic AI Lectures 1-5"]["timeline"] == "Next 10 days"
        assert by_title["Stanford Agentic AI Lectures 1-5"]["due_date"] == "2026-12-07"
        assert by_title["Draw 3 artworks"]["frequency"] == "3 artworks in 10 days"

        # Patch timeline/frequency via REST API
        tid = by_title["Draw 3 artworks"]["id"]
        upd = client.patch(
            f"/api/tasks/{tid}",
            json={"frequency": "3 artworks (1 every 3 days)", "timeline": "10-day sprint"},
        ).json()["item"]
        assert upd["frequency"] == "3 artworks (1 every 3 days)"
        assert upd["timeline"] == "10-day sprint"


def test_task_subchecklist_auto_rollup_and_partial_status(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        r = client.post(
            "/api/tasks",
            json={
                "title": "Clean studio",
                "list_type": "daily",
                "checklist_items": ["Clear desk", "Organize cables", "Sweep floor"],
            },
        )
        assert r.status_code == 201
        task = r.json()["item"]
        assert task["status"] == "not_started"
        assert task["checklist_total"] == 3
        assert task["checklist_done"] == 0
        items = task["checklist_items"]

        # Check 1 item -> parent automatically becomes partially_completed
        r1 = client.patch(
            f"/api/tasks/{task['id']}/items/{items[0]['id']}",
            json={"done": True},
        )
        assert r1.status_code == 200
        t1 = r1.json()["item"]
        assert t1["status"] == "partially_completed"
        assert t1["checklist_done"] == 1
        assert t1["done"] is False

        # Check remaining 2 items -> parent automatically becomes completed
        client.patch(f"/api/tasks/{task['id']}/items/{items[1]['id']}", json={"done": True})
        r3 = client.patch(f"/api/tasks/{task['id']}/items/{items[2]['id']}", json={"done": True})
        t3 = r3.json()["item"]
        assert t3["status"] == "completed"
        assert t3["checklist_done"] == 3
        assert t3["done"] is True

        # Uncheck 1 item -> rolls back to partially_completed
        r_un = client.patch(f"/api/tasks/{task['id']}/items/{items[1]['id']}", json={"done": False})
        t_un = r_un.json()["item"]
        assert t_un["status"] == "partially_completed"
        assert t_un["checklist_done"] == 2
        assert t_un["done"] is False

        # Standalone task manual partially_completed + progress_note
        rs = client.post("/api/tasks", json={"title": "Read Deep Learning book"}).json()["item"]
        rs_upd = client.patch(
            f"/api/tasks/{rs['id']}",
            json={"status": "partially_completed", "progress_note": "Finished chapter 3"},
        ).json()["item"]
        assert rs_upd["status"] == "partially_completed"
        assert rs_upd["progress_note"] == "Finished chapter 3"
        assert rs_upd["done"] is False


def test_note_interactive_checklists_and_lecture_append(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        n = client.post(
            "/api/notes",
            json={
                "title": "Deep Learning Lecture 4",
                "content": "Covered residual connections and batch norm.",
                "tags": ["AI", "lecture"],
            },
        ).json()["item"]
        assert n["checklist"]["total"] == 0

        # Mid-lecture append with checklist items and an extra tag
        appended = client.post(
            f"/api/notes/{n['id']}/append",
            json={
                "text": "Look into skip-connection gradient flow.",
                "checklist_items": ["Research ResNet", "Review backprop math"],
                "add_tags": ["research-later"],
            },
        ).json()["item"]
        assert appended["checklist"]["total"] == 2
        assert appended["checklist"]["status"] == "not_started"
        assert "research-later" in appended["tags"]

        # Toggle first checkbox -> note checklist becomes partially_completed (1/2)
        toggled = client.post(
            f"/api/notes/{n['id']}/checklist-toggle",
            json={"item_index": 0, "done": True},
        ).json()["item"]
        assert toggled["checklist"]["done"] == 1
        assert toggled["checklist"]["status"] == "partially_completed"
        assert "- [x] Research ResNet" in toggled["content"]


def test_multi_turn_agent_tool_loop_and_delete_safety(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    turn_calls: list[list[dict]] = []

    async def fake_agent_step(messages, api_key, model, use_tools=True):
        turn_calls.append(list(messages))
        has_tool_results = any(m.get("role") == "tool" for m in messages)
        last_user = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )

        # Turn 1: multi-domain voice dump (gym + art + lecture note with checklist)
        if "Transformers" in last_user and not has_tool_results:
            return (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tc_1",
                            "type": "function",
                            "function": {
                                "name": "log_gym_exercise",
                                "arguments": json.dumps(
                                    {"exercise": "Squats", "sets": 3, "reps": 5, "weight": 100, "unit": "kg"}
                                ),
                            },
                        },
                        {
                            "id": "tc_2",
                            "type": "function",
                            "function": {
                                "name": "upsert_art",
                                "arguments": json.dumps(
                                    {"title": "Portrait Study", "status": "wip", "mark_art_day": True}
                                ),
                            },
                        },
                        {
                            "id": "tc_3",
                            "type": "function",
                            "function": {
                                "name": "create_note",
                                "arguments": json.dumps(
                                    {
                                        "title": "Transformers Lecture",
                                        "content": "Attention is all you need.",
                                        "tags": ["NLP", "attention"],
                                        "checklist_items": ["read Vaswani paper", "code self-attention"],
                                    }
                                ),
                            },
                        },
                    ],
                },
                model,
            )

        if "Transformers" in last_user and has_tool_results:
            return (
                {
                    "role": "assistant",
                    "content": "Logged your squats, marked your portrait WIP, and saved your Transformers lecture note with 2 checklist steps.",
                },
                model,
            )

        # Turn 2: check off Vaswani paper on the lecture note created in Turn 1
        if "Vaswani" in last_user and not has_tool_results:
            notes = db.list_notes(query="Transformers")
            nid = notes[0]["id"]
            return (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "tc_4",
                            "type": "function",
                            "function": {
                                "name": "update_note_checklist",
                                "arguments": json.dumps(
                                    {"note_id": nid, "check_items": ["Vaswani"]}
                                ),
                            },
                        }
                    ],
                },
                model,
            )

        return (
            {
                "role": "assistant",
                "content": "Checked off reading the Vaswani paper — 1 of 2 items complete.",
            },
            model,
        )

    monkeypatch.setattr(agent, "_call_groq_agent_step", fake_agent_step)

    with TestClient(app) as client:
        client.patch("/api/settings", json={"groq_api_key": "gsk_testkey123456"})

        r1 = client.post(
            "/api/agent/chat",
            json={
                "text": "Went to gym and did squats 100kg 3x5, started a portrait WIP, and make a lecture note on Transformers tagged NLP, attention with checklist: read Vaswani paper, code self-attention",
                "source": "voice",
            },
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["ai_used"] is True
        assert len(d1["actions"]) == 3
        sid = d1["session_id"]

        assert len(client.get("/api/gym/logs").json()["items"]) == 1
        assert client.get("/api/art").json()["items"][0]["title"] == "Portrait Study"
        notes = client.get("/api/notes").json()["items"]
        assert len(notes) == 1
        assert notes[0]["checklist"]["total"] == 2
        assert notes[0]["checklist"]["done"] == 0

        # Turn 2 in same session
        r2 = client.post(
            "/api/agent/chat",
            json={
                "text": "I finished reading the Vaswani paper on that lecture note",
                "session_id": sid,
                "source": "voice",
            },
        )
        assert r2.status_code == 200
        updated_note = client.get("/api/notes").json()["items"][0]
        assert updated_note["checklist"]["done"] == 1
        assert updated_note["checklist"]["status"] == "partially_completed"

        # Verify session history persisted all 4 messages
        sess = client.get(f"/api/agent/sessions/{sid}").json()["session"]
        assert len(sess["messages"]) == 4

        # Verify delete_item refuses without confirmed=True
        safe_del = tools.execute_tool("delete_item", {"domain": "note", "item_id": updated_note["id"]})
        assert safe_del["result"]["needs_confirmation"] is True
        assert len(client.get("/api/notes").json()["items"]) == 1


def test_tts_endpoint_groq_and_local_fallback_and_rms(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    # Verify pure-Python PCM RMS calculation
    silent = array("h", [0] * 160).tobytes()
    loud = array("h", [2000] * 160).tobytes()
    assert voice._pcm_rms(silent) == 0.0
    assert voice._pcm_rms(loud) >= 1999.0

    with TestClient(app) as client:
        # No API key -> graceful local fallback JSON
        r_no_key = client.post("/api/voice/tts", json={"text": "Hello **there**!"})
        assert r_no_key.status_code == 200
        assert r_no_key.json()["fallback_local"] is True

        client.patch("/api/settings", json={"groq_api_key": "gsk_testkey123456"})

        class FakeResp:
            def __init__(self, status_code: int, content: bytes = b""):
                self.status_code = status_code
                self.content = content

        class FakeClient:
            def __init__(self, status_code: int, content: bytes = b""):
                self.status_code = status_code
                self.content = content

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                return FakeResp(self.status_code, self.content)

        # Groq TTS returns audio/wav
        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=20.0: FakeClient(200, b"RIFFDATA"))
        r_ok = client.post("/api/voice/tts", json={"text": "Your gym log is saved."})
        assert r_ok.status_code == 200
        assert r_ok.headers["content-type"].startswith("audio/wav")
        assert r_ok.content == b"RIFFDATA"

        # Groq TTS 429 rate-limit -> graceful local fallback
        monkeypatch.setattr(httpx, "AsyncClient", lambda timeout=20.0: FakeClient(429, b"rate limit"))
        r_429 = client.post("/api/voice/tts", json={"text": "Testing fallback."})
        assert r_429.status_code == 200
        assert r_429.json()["fallback_local"] is True
