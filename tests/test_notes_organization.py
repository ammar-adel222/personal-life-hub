"""Tests for structured Markdown notes, folders/tags filtering, subfolder sync, and AI note organization."""

from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from app import ai, db, tools
from app.server import app


def test_legacy_notes_db_migration(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))
    db._initialized_db_paths.clear()

    db_path = tmp_path / "lifehub.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            pinned INTEGER NOT NULL DEFAULT 0,
            source_path TEXT UNIQUE,
            file_mtime REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO notes (title, content, tags, pinned, created_at, updated_at)
        VALUES (?, ?, ?, 1, '2026-11-20T10:00:00', '2026-11-20T10:00:00')
        """,
        ("Legacy Voice Dump", "Went to gym\nBench press 80kg 3x8", '["gym"]'),
    )
    conn.commit()
    conn.close()

    db.init_db()
    notes = db.list_notes()
    assert len(notes) == 1
    assert notes[0]["title"] == "Legacy Voice Dump"
    assert notes[0]["folder"] == ""
    assert notes[0]["summary"] == ""
    assert notes[0]["pinned"] is True
    assert notes[0]["word_count"] == 7


def test_notes_folders_tags_pin_and_sort(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        n1 = client.post(
            "/api/notes",
            json={
                "title": "Zebra Transformers",
                "folder": "Lectures",
                "summary": "Self-attention mechanisms and KV caching.",
                "content": "## Attention\n- Scaled dot product",
                "tags": ["ai", "lecture"],
                "pinned": True,
            },
        ).json()["item"]
        n2 = client.post(
            "/api/notes",
            json={
                "title": "Alpha Hub App",
                "folder": "Projects",
                "summary": "FastAPI desktop roadmap.",
                "content": "## Roadmap\n- Add Markdown support",
                "tags": ["python", "ai"],
                "pinned": False,
            },
        ).json()["item"]
        n3 = client.post(
            "/api/notes",
            json={
                "title": "Unfiled Scrap",
                "folder": "",
                "content": "Quick reminder",
                "tags": ["misc"],
            },
        ).json()["item"]
        n4 = client.post(
            "/api/notes",
            json={
                "title": "Paper Review",
                "folder": "Research",
                "content": "Notes on tool-calling agents",
                "tags": ["research"],
            },
        ).json()["item"]

        # Unfiltered response includes folder & tag facets
        r_all = client.get("/api/notes").json()
        assert len(r_all["items"]) == 4
        assert r_all["folders"]["total"] == 4
        assert r_all["folders"]["pinned"] == 1
        assert r_all["folders"]["unfiled"] == 1
        folder_map = {f["name"]: f["count"] for f in r_all["folders"]["folders"]}
        assert folder_map["Lectures"] == 1
        assert folder_map["Projects"] == 1
        assert folder_map["Research"] == 1
        assert folder_map["Ideas"] == 0

        # Folder filtering
        r_lec = client.get("/api/notes?folder=Lectures").json()["items"]
        assert [n["id"] for n in r_lec] == [n1["id"]]

        r_unf = client.get("/api/notes?folder=__unfiled__").json()["items"]
        assert [n["id"] for n in r_unf] == [n3["id"]]

        # Pinned filter
        r_pin = client.get("/api/notes?pinned_only=true").json()["items"]
        assert [n["id"] for n in r_pin] == [n1["id"]]

        # Tag filter + summary query
        r_ai = client.get("/api/notes?tag=ai&q=roadmap").json()["items"]
        assert [n["id"] for n in r_ai] == [n2["id"]]

        # Title sort (pinned note stays on top, remaining ordered alphabetically)
        r_sorted = client.get("/api/notes?sort=title").json()["items"]
        assert [n["id"] for n in r_sorted] == [n1["id"], n2["id"], n4["id"], n3["id"]]


def test_subfolder_sync_and_save(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    notes_dir = tmp_path / "notes"
    (notes_dir / "Lectures").mkdir(parents=True)
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(data_dir))

    (notes_dir / "Lectures" / "agents.md").write_text(
        "# Agentic Systems\n- Tool loops\n- Memory", encoding="utf-8"
    )
    (notes_dir / "scratch.txt").write_text("Buy pens", encoding="utf-8")

    with TestClient(app) as client:
        client.patch("/api/settings", json={"notes_folder": str(notes_dir)})
        sync_res = client.post("/api/notes/sync").json()
        assert sync_res["imported"] == 2

        by_title = {n["title"]: n for n in client.get("/api/notes").json()["items"]}
        assert by_title["Agentic Systems"]["folder"] == "Lectures"
        assert by_title["scratch"]["folder"] == ""

        # Creating a note with folder + save_to_folder writes into folder subfolder
        created = client.post(
            "/api/notes",
            json={
                "title": "App Architecture",
                "folder": "Projects",
                "content": "## Stack\n- FastAPI",
                "save_to_folder": True,
            },
        ).json()["item"]
        assert created["folder"] == "Projects"
        assert (notes_dir / "Projects" / "App-Architecture.md").exists()


def test_ai_organize_endpoint_and_offline_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        # 1. Offline/no-key organizer preview & note organize
        raw = client.post(
            "/api/notes",
            json={
                "title": "Gym Session",
                "content": "Went to gym chest and back. Upper chest 8 reps 40kg.\n- [ ] Check protein intake",
            },
        ).json()["item"]

        off_res = client.post(
            f"/api/notes/{raw['id']}/organize",
            json={"style": "structured", "apply": True},
        )
        assert off_res.status_code == 200
        off_body = off_res.json()
        assert off_body["ai_used"] is False
        assert "## Key Notes" in off_body["item"]["content"]
        assert "## Action & Research Items" in off_body["item"]["content"]
        assert "- [ ] Check protein intake" in off_body["item"]["content"]
        assert off_body["item"]["checklist"]["total"] == 1

        # 2. Online Groq organizer (mocked _call_groq_chat)
        client.patch("/api/settings", json={"groq_api_key": "gsk_test_organize_1234"})

        async def fake_groq(messages, api_key, model, json_mode=False, temperature=0.2):
            return (
                json.dumps(
                    {
                        "title": "Upper Body Push/Pull Log",
                        "folder": "Personal",
                        "summary": "Chest and back hypertrophy workout featuring 8 reps at 40kg on upper chest.",
                        "tags": ["gym", "workout"],
                        "content": (
                            "## Workout Breakdown\n"
                            "- **Upper Chest**: 8 reps @ 40kg\n"
                            "- **Back**: Completed session\n\n"
                            "> [!NOTE]\n"
                            "> Strong upper-chest volume — aim for 42.5kg next session.\n\n"
                            "## Action & Research Items\n"
                            "- [ ] Check protein intake"
                        ),
                    }
                ),
                "openai/gpt-oss-120b",
            )

        monkeypatch.setattr(ai, "_call_groq_chat", fake_groq)

        ai_res = client.post(
            f"/api/notes/{raw['id']}/organize",
            json={"style": "structured", "apply": True},
        )
        assert ai_res.status_code == 200
        ai_body = ai_res.json()
        assert ai_body["ai_used"] is True
        assert ai_body["item"]["title"] == "Upper Body Push/Pull Log"
        assert ai_body["item"]["folder"] == "Personal"
        assert "40kg" in ai_body["item"]["summary"]
        assert "## Workout Breakdown" in ai_body["item"]["content"]
        assert ai_body["item"]["checklist"]["total"] == 1

        # Organize preview for New Note composer (does not persist in DB)
        prev = client.post(
            "/api/notes/organize-preview",
            json={"content": "Raw scratch lecture note", "style": "lecture"},
        )
        assert prev.status_code == 200
        assert prev.json()["ai_used"] is True
        assert len(client.get("/api/notes").json()["items"]) == 1


def test_agent_note_tools_create_append_update(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app):
        res_create = tools.execute_tool(
            "create_note",
            {
                "title": "Distributed Systems Lecture 2",
                "folder": "Lectures",
                "summary": "Consensus with Raft leader election.",
                "content": "## Core Concepts\n- Leader election\n- Log replication",
                "tags": ["distributed", "lecture"],
                "pinned": True,
            },
        )
        note_id = res_create["result"]["note"]["id"]
        assert res_create["result"]["note"]["folder"] == "Lectures"
        assert res_create["result"]["note"]["pinned"] is True

        # Append structured section with heading
        res_app = tools.execute_tool(
            "append_to_note",
            {
                "note_id": note_id,
                "heading": "Mid-Lecture Follow-up",
                "text": "Compare Raft term numbers vs Paxos ballots.",
                "checklist_items": ["Read Ongaro paper Section 5.2"],
            },
        )
        assert "## Mid-Lecture Follow-up" in res_app["result"]["note"]["content"]
        assert res_app["result"]["note"]["checklist"]["total"] == 1

        # Search by folder & pinned
        res_search = tools.execute_tool(
            "search_notes",
            {"folder": "Lectures", "pinned_only": True},
        )
        assert len(res_search["result"]["notes"]) == 1
        assert res_search["result"]["notes"][0]["id"] == note_id

        # Update note cleanly via update_note tool
        res_upd = tools.execute_tool(
            "update_note",
            {
                "note_id": note_id,
                "folder": "Study",
                "summary": "Deep dive on Raft safety and leader election.",
                "tags": ["raft", "consensus"],
            },
        )
        assert res_upd["result"]["note"]["folder"] == "Study"
        assert "consensus" in res_upd["result"]["note"]["tags"]
        assert "Organized Note" in res_upd["action_badge"]["label"]
