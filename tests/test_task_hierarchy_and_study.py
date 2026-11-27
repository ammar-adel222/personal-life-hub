"""Tests for hierarchical task/habit auto-linking, ambiguous clarification, and live daily study tracking."""

from __future__ import annotations

import json
import sqlite3
import time

import httpx
from fastapi.testclient import TestClient

from app import agent, db, tools
from app.server import app


def _iso_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIFEHUB_DISABLE_AUTO_SYNC", "1")
    db._initialized_db_paths.clear()


def test_recurring_goal_inference_and_high_confidence_auto_linking(monkeypatch, tmp_path):
    _iso_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        # 1. Create a 10-day recurring daily goal
        g_resp = client.post(
            "/api/tasks",
            json={
                "title": "LeetCode daily LeetCode for 1 hour a day for the next 10 days",
                "list_type": "daily",
                "timeline": "Next 10 days",
                "frequency": "1 hour / day",
            },
        )
        assert g_resp.status_code == 201
        goal = g_resp.json()["item"]
        assert goal["is_recurring"] is True
        assert goal["target_days"] == 10
        assert goal["daily_target_minutes"] == 60
        assert goal["is_today_task"] is False
        assert goal["done_today"] is False

        # 2. Create today's child task -> auto-links to LeetCode recurring goal
        today = db.today_str()
        c_resp = client.post(
            "/api/tasks",
            json={
                "title": "LeetCode today",
                "list_type": "daily",
                "start_date": today,
                "due_date": today,
                "timeline": "Today",
            },
        )
        assert c_resp.status_code == 201
        child = c_resp.json()["item"]
        assert child["parent_task_id"] == goal["id"]
        assert child["parent_task_title"] == goal["title"]
        assert child["is_today_task"] is True
        assert child["possible_parents"] == []

        # 3. Completing today's child propagates to recurring parent (Done Today + 1/10 days, keeps parent active)
        done_resp = client.patch(f"/api/tasks/{child['id']}", json={"status": "completed"})
        assert done_resp.status_code == 200
        updated_child = done_resp.json()["item"]
        assert updated_child["done"] is True
        assert len(updated_child["parent_updates"]) == 1
        pu = updated_child["parent_updates"][0]
        assert pu["parent_task_id"] == goal["id"]
        assert pu["done_today"] is True
        assert pu["days_completed"] == 1
        assert pu["target_days"] == 10

        parent_after = db.get_task(goal["id"])
        assert parent_after["done_today"] is True
        assert parent_after["days_completed"] == 1
        assert parent_after["status"] == "partially_completed"
        assert parent_after["done"] is False

        # 4. Multi-step parent matching (Lecture 1 under Lectures 1-5)
        lec_goal = client.post(
            "/api/tasks",
            json={
                "title": "Stanford Agentic AI Lectures 1-5",
                "list_type": "daily",
                "timeline": "Next 10 days",
                "checklist_items": ["Lecture 1", "Lecture 2", "Lecture 3"],
            },
        ).json()["item"]
        lec_today = client.post(
            "/api/tasks",
            json={
                "title": "Watch Stanford Agentic AI Lecture 1",
                "list_type": "daily",
                "due_date": today,
                "timeline": "Today",
            },
        ).json()["item"]
        assert lec_today["parent_task_id"] == lec_goal["id"]
        assert lec_today["parent_item_text"] == "Lecture 1"

        client.patch(f"/api/tasks/{lec_today['id']}", json={"status": "completed"})
        lec_parent_after = db.get_task(lec_goal["id"])
        assert lec_parent_after["status"] == "partially_completed"
        assert lec_parent_after["checklist_items"][0]["done"] is True
        assert lec_parent_after["checklist_items"][1]["done"] is False


def test_ambiguous_parent_returns_possible_parents_and_agent_asks(monkeypatch, tmp_path):
    _iso_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        # Generic-topic parent -> "Read autonomous agents survey" should ask instead of blindly linking
        proj = client.post(
            "/api/tasks",
            json={
                "title": "Graduation Project Surveys",
                "list_type": "daily",
                "timeline": "Next 10 days",
                "checklist_items": ["Design surveys", "Collect responses"],
            },
        ).json()["item"]

        today = db.today_str()
        survey_today = client.post(
            "/api/tasks",
            json={
                "title": "Read autonomous agents architecture survey",
                "list_type": "daily",
                "due_date": today,
                "timeline": "Today",
            },
        ).json()["item"]
        assert survey_today["parent_task_id"] is None
        assert any(cand["id"] == proj["id"] for cand in survey_today["possible_parents"])

        # Tool surface exposes clarification candidates cleanly
        tool_res = tools.execute_tool(
            "create_task",
            {
                "title": "Summarize another survey",
                "list_type": "daily",
                "due_date": today,
                "timeline": "Today",
            },
        )
        assert tool_res["result"].get("needs_parent_clarification") is True
        assert len(tool_res["result"].get("possible_parents", [])) >= 1

        # Competing two similar parents -> tie triggers ambiguous candidates rather than guessing
        g1 = client.post(
            "/api/tasks",
            json={"title": "LeetCode Algorithms Daily", "list_type": "daily", "timeline": "Next 10 days"},
        ).json()["item"]
        g2 = client.post(
            "/api/tasks",
            json={"title": "LeetCode SQL Daily", "list_type": "daily", "timeline": "Next 10 days"},
        ).json()["item"]
        lc = client.post(
            "/api/tasks",
            json={"title": "LeetCode today", "list_type": "daily", "due_date": today, "timeline": "Today"},
        ).json()["item"]
        assert lc["parent_task_id"] is None
        cand_ids = {c["id"] for c in lc["possible_parents"]}
        assert g1["id"] in cand_ids and g2["id"] in cand_ids


def test_done_today_endpoint_target_days_completion_and_auto_link(monkeypatch, tmp_path):
    _iso_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        # 2-day recurring goal completes when 2 distinct dates are logged via done-today
        goal = client.post(
            "/api/tasks",
            json={
                "title": "Daily Rust reading for 2 days",
                "list_type": "daily",
                "is_recurring": True,
                "target_days": 2,
                "timeline": "Next 2 days",
            },
        ).json()["item"]
        assert goal["target_days"] == 2

        d1 = client.post(f"/api/tasks/{goal['id']}/done-today", json={"date": "2026-11-26"}).json()["item"]
        assert d1["days_completed"] == 1
        assert d1["status"] == "partially_completed"
        assert d1["done"] is False

        d2 = client.post(f"/api/tasks/{goal['id']}/done-today", json={}).json()["item"]
        assert d2["done_today"] is True
        assert d2["days_completed"] == 2
        assert d2["status"] == "completed"
        assert d2["done"] is True

        # Toggle off today rolls back to partially_completed
        d2_off = client.post(f"/api/tasks/{goal['id']}/done-today", json={"done": False}).json()["item"]
        assert d2_off["done_today"] is False
        assert d2_off["days_completed"] == 1
        assert d2_off["status"] == "partially_completed"

        # Auto-link existing unlinked today tasks
        unlinked_parent = client.post(
            "/api/tasks",
            json={"title": "Pytorch Model Optimization Next 7 Days", "list_type": "daily", "timeline": "Next 7 days"},
        ).json()["item"]
        today = db.today_str()
        orphan_child = client.post(
            "/api/tasks",
            json={
                "title": "Pytorch pruning check",
                "list_type": "daily",
                "due_date": today,
                "timeline": "Today",
                "auto_link_parent": False,
                "status": "completed",
            },
        ).json()["item"]
        assert orphan_child["parent_task_id"] is None

        link_res = client.post("/api/tasks/auto-link").json()
        assert link_res["count"] >= 1
        assert any(
            x["task_id"] == orphan_child["id"] and x["parent_task_id"] == unlinked_parent["id"]
            for x in link_res["linked"]
        )
        assert db.get_task(orphan_child["id"])["parent_task_id"] == unlinked_parent["id"]


def test_live_timer_counts_toward_today_autosaves_and_completes_daily_target(monkeypatch, tmp_path):
    _iso_env(monkeypatch, tmp_path)

    with TestClient(app) as client:
        # Recurring LeetCode habit expecting 60m/day
        goal = client.post(
            "/api/tasks",
            json={
                "title": "LeetCode 1 hour daily for next 10 days",
                "list_type": "daily",
                "is_recurring": True,
                "target_days": 10,
                "daily_target_minutes": 60,
            },
        ).json()["item"]

        # 1. Start a live focus timer with topic matching LeetCode (auto-links task_id)
        start1 = client.post(
            "/api/timer/control",
            json={
                "action": "start",
                "mode": "countdown",
                "duration_minutes": 60,
                "subject": "Coding",
                "topic": "LeetCode daily problems",
            },
        ).json()
        assert start1["state"]["task_id"] == goal["id"]

        # Simulate 40 minutes already running live (no pause/stop yet)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE active_timer SET started_at_epoch = ? WHERE id = 1",
                (time.time() - 2400.0,),
            )

        # Stats & task immediately include the live 40 minutes in today's totals!
        live_stats = client.get("/api/timer/state").json()["stats"]
        assert live_stats["today_logged_seconds"] == 0
        assert live_stats["live_focus_seconds"] >= 2400
        assert live_stats["today_seconds"] >= 2400
        assert db.get_task(goal["id"])["study_seconds_today"] >= 2400

        # 2. Starting a second timer auto-saves the running 40m focus timer instead of dropping it
        start2 = client.post(
            "/api/timer/control",
            json={
                "action": "start",
                "mode": "countdown",
                "duration_minutes": 25,
                "subject": "Coding",
                "topic": "LeetCode extra 25m",
            },
        ).json()
        assert start2["logged_session"] is not None
        assert start2["logged_session"]["duration_seconds"] >= 2400
        assert start2["logged_session"]["task_id"] == goal["id"]

        # 3. Logging remaining 20+ minutes hits 60m daily_target_minutes -> auto-marks recurring goal Done Today!
        client.post(
            "/api/study/sessions",
            json={"subject": "Coding", "topic": "LeetCode finish", "duration_minutes": 25, "task_id": goal["id"]},
        )
        refreshed_goal = db.get_task(goal["id"])
        assert refreshed_goal["done_today"] is True
        assert refreshed_goal["days_completed"] == 1
        assert refreshed_goal["status"] == "partially_completed"
        assert refreshed_goal["study_seconds_today"] >= 3900


def test_legacy_tasks_migration_adds_hierarchy_columns(monkeypatch, tmp_path):
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
        ("Old open task",),
    )
    conn.commit()
    conn.close()

    db.init_db()
    with db.get_conn() as c2:
        cols = {r["name"] for r in c2.execute("PRAGMA table_info(tasks)").fetchall()}
        assert {"parent_task_id", "parent_item_id", "is_recurring", "target_days", "daily_target_minutes"}.issubset(cols)
        tbl = c2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='task_daily_logs'"
        ).fetchone()
        assert tbl is not None
    tasks = db.list_tasks()
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Old open task"
    assert tasks[0]["parent_task_id"] is None
    assert tasks[0]["done_today"] is False
