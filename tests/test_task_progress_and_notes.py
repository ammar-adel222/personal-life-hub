"""Tests for 2-tab Tasks, non-countable progress bars, countable day checkboxes, and connected task notes."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from app import db, tools
from app.server import app


def _iso_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIFEHUB_DISABLE_AUTO_SYNC", "1")
    db._initialized_db_paths.clear()


def test_non_countable_progress_bar_logs_and_rollup(monkeypatch, tmp_path):
    _iso_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        # Non-countable task -> defaults to progress bar (`effective_progress_mode == "percent"`)
        resp = client.post(
            "/api/tasks",
            json={
                "title": "Graduation Project Surveys",
                "list_type": "daily",
                "timeline": "Next 10 days",
                "progress_mode": "percent",
            },
        )
        assert resp.status_code == 201
        task = resp.json()["item"]
        assert task["effective_progress_mode"] == "percent"
        assert task["progress_percent"] == 0
        assert task["status"] == "not_started"
        assert task["is_today_task"] is False

        # Record 40% progress with improvement note
        p1 = client.post(
            f"/api/tasks/{task['id']}/progress",
            json={"progress_percent": 40, "note": "Drafted 15 survey questions"},
        )
        assert p1.status_code == 200
        t1 = p1.json()["item"]
        assert t1["progress_percent"] == 40
        assert t1["status"] == "partially_completed"
        assert t1["done"] is False
        assert t1["progress_note"] == "Drafted 15 survey questions"
        assert len(t1["progress_logs"]) == 1
        assert t1["progress_logs"][0]["progress_percent"] == 40

        # Increment via delta_percent (+60 -> 100%) -> auto-completes task
        p2 = client.post(
            f"/api/tasks/{task['id']}/progress",
            json={"delta_percent": 60, "note": "Collected and summarized all responses"},
        )
        assert p2.status_code == 200
        t2 = p2.json()["item"]
        assert t2["progress_percent"] == 100
        assert t2["status"] == "completed"
        assert t2["done"] is True
        assert len(t2["progress_logs"]) == 2
        assert t2["progress_logs"][0]["progress_percent"] == 100


def test_countable_day_checkbox_slots_and_mode_switching(monkeypatch, tmp_path):
    _iso_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        today = db.today_str()
        # 10-day recurring daily LeetCode habit -> generates 10 clickable day checkbox slots
        resp = client.post(
            "/api/tasks",
            json={
                "title": "Daily LeetCode practice for the next 10 days",
                "list_type": "daily",
                "start_date": "2026-11-27",
                "timeline": "Next 10 days",
                "frequency": "1 hour / day",
            },
        )
        assert resp.status_code == 201
        habit = resp.json()["item"]
        assert habit["effective_progress_mode"] == "days"
        assert len(habit["day_slots"]) == 10
        assert [s["day_num"] for s in habit["day_slots"]] == list(range(1, 11))
        assert all(s["done"] is False for s in habit["day_slots"])

        # Check Day 1 and Day 2 independently
        d1 = client.post(
            f"/api/tasks/{habit['id']}/done-today",
            json={"date": habit["day_slots"][0]["date"], "done": True},
        ).json()["item"]
        d2 = client.post(
            f"/api/tasks/{habit['id']}/done-today",
            json={"date": habit["day_slots"][1]["date"], "done": True},
        ).json()["item"]
        assert d2["days_completed"] == 2
        assert d2["day_slots"][0]["done"] is True
        assert d2["day_slots"][1]["done"] is True
        assert d2["day_slots"][2]["done"] is False
        assert d2["status"] == "partially_completed"

        # Open-ended daily habit without target_days -> 7-day rolling window ending today
        open_habit = client.post(
            "/api/tasks",
            json={"title": "Daily Vocabulary Review", "list_type": "daily", "frequency": "Daily"},
        ).json()["item"]
        assert open_habit["effective_progress_mode"] == "days"
        assert len(open_habit["day_slots"]) == 7
        assert open_habit["day_slots"][-1]["date"] == today
        assert open_habit["day_slots"][-1]["is_today"] is True

        # Switch tracking mode explicitly via PATCH /api/tasks/{id}
        switched = client.patch(
            f"/api/tasks/{open_habit['id']}", json={"progress_mode": "percent"}
        ).json()["item"]
        assert switched["progress_mode"] == "percent"
        assert switched["effective_progress_mode"] == "percent"
        assert switched["day_slots"] == []


def test_connected_task_notes_bidirectional_and_fk_set_null(monkeypatch, tmp_path):
    _iso_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        task = client.post(
            "/api/tasks",
            json={"title": "Autonomous Agents Architecture Survey", "list_type": "daily"},
        ).json()["item"]
        assert task["note_id"] is None

        # Append note directly -> auto-creates & connects a structured Markdown note
        ap = client.post(
            f"/api/tasks/{task['id']}/notes",
            json={"text": "Compare ReAct vs Plan-Execute latency tradeoffs."},
        )
        assert ap.status_code == 200
        ap_data = ap.json()
        assert ap_data["created"] is True
        note_id = ap_data["note"]["id"]
        assert ap_data["task"]["note_id"] == note_id
        assert "Compare ReAct vs Plan-Execute latency tradeoffs." in ap_data["note"]["content"]

        # Subsequent open-note reuses the existing note idempotently
        reopen = client.post(f"/api/tasks/{task['id']}/open-note").json()
        assert reopen["created"] is False
        assert reopen["note"]["id"] == note_id

        # Bidirectional lookup on Notes API exposes linked_tasks
        notes_list = client.get("/api/notes").json()["items"]
        linked = next(n for n in notes_list if n["id"] == note_id)
        assert len(linked["linked_tasks"]) == 1
        assert linked["linked_tasks"][0]["id"] == task["id"]
        assert linked["linked_tasks"][0]["title"] == "Autonomous Agents Architecture Survey"

        # Backup export includes task_progress_logs
        exp = client.get("/api/system/export").json()
        assert "task_progress_logs" in exp

        # Deleting the note leaves the task intact with note_id = None
        assert client.delete(f"/api/notes/{note_id}").status_code == 200
        refreshed = db.get_task(task["id"])
        assert refreshed is not None
        assert refreshed["note_id"] is None
        assert refreshed["note_title"] == ""


def test_agent_tools_for_progress_bar_and_connected_notes(monkeypatch, tmp_path):
    _iso_env(monkeypatch, tmp_path)
    assert len(tools.TOOL_SCHEMAS) == 24
    db.init_db()

    res_create = tools.execute_tool(
        "create_task",
        {
            "title": "Polish Publishing Workflow",
            "list_type": "daily",
            "timeline": "Next 10 days",
            "progress_mode": "percent",
        },
    )
    tid = res_create["result"]["task"]["id"]

    # update_task logs progress_percent + progress_update AND appends connected task_note
    res_upd = tools.execute_tool(
        "update_task",
        {
            "task_id": tid,
            "progress_percent": 55,
            "progress_update": "Added automated release checklist",
            "task_note": "Remember to verify webhook retries before launch.",
        },
    )
    upd_task = res_upd["result"]["task"]
    assert upd_task["progress_percent"] == 55
    assert upd_task["status"] == "partially_completed"
    assert len(upd_task["progress_logs"]) == 1
    assert upd_task["note_id"] is not None
    assert "action_badges" in res_upd and len(res_upd["action_badges"]) >= 2

    # append_to_note supports task_id directly when note_id is omitted
    res_app = tools.execute_tool(
        "append_to_note",
        {
            "task_id": tid,
            "heading": "Testing Ideas",
            "text": "Run staging smoke suite after webhook refactor.",
        },
    )
    assert res_app["result"]["note"]["id"] == upd_task["note_id"]
    assert "Testing Ideas" in res_app["result"]["note"]["content"]


def test_legacy_db_migration_adds_progress_and_note_columns(monkeypatch, tmp_path):
    _iso_env(monkeypatch, tmp_path)

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
        "INSERT INTO tasks (title, list_type, done, created_at) VALUES (?, 'daily', 0, '2026-11-20T10:00:00')",
        ("Legacy unmigrated task",),
    )
    conn.commit()
    conn.close()

    db.init_db()
    with db.get_conn() as c2:
        cols = {r["name"] for r in c2.execute("PRAGMA table_info(tasks)").fetchall()}
        assert {"progress_mode", "progress_percent", "note_id"}.issubset(cols)
        tbl = c2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='task_progress_logs'"
        ).fetchone()
        assert tbl is not None

    tasks = db.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Legacy unmigrated task"
    assert tasks[0]["progress_mode"] == "auto"
    assert tasks[0]["progress_percent"] == 0
    assert tasks[0]["note_id"] is None
