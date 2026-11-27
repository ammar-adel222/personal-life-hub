"""Tests for Focus/Study Timer state machine, study sessions, stats, agent tools, and smart capture."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app import db, tools
from app.server import app


def test_live_timer_countdown_stopwatch_pause_resume_and_break(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        task = client.post("/api/tasks", json={"title": "Watch Lecture 4"}).json()["item"]
        note = client.post(
            "/api/notes",
            json={"title": "ResNets Lecture", "folder": "Lectures", "content": "## Notes"},
        ).json()["item"]

        # 1. Start countdown linked to task + note
        r_start = client.post(
            "/api/timer/control",
            json={
                "action": "start",
                "mode": "countdown",
                "duration_minutes": 25,
                "break_minutes": 5,
                "subject": "Lectures",
                "topic": "ResNets",
                "task_id": task["id"],
                "note_id": note["id"],
            },
        )
        assert r_start.status_code == 200
        st = r_start.json()["state"]
        assert st["active"] is True
        assert st["paused"] is False
        assert st["mode"] == "countdown"
        assert st["phase"] == "focus"
        assert st["target_seconds"] == 1500
        assert st["break_seconds"] == 300
        assert st["task_title"] == "Watch Lecture 4"
        assert st["note_title"] == "ResNets Lecture"

        # 2. Update meta without resetting elapsed clock
        r_meta = client.post(
            "/api/timer/control",
            json={
                "action": "update_meta",
                "subject": "Lectures",
                "topic": "ResNets & Skip Connections",
                "notes": "Key gradient flow insight",
            },
        )
        assert r_meta.status_code == 200
        assert r_meta.json()["state"]["topic"] == "ResNets & Skip Connections"
        assert r_meta.json()["state"]["active"] is True

        # 3. Pause & Resume
        r_pause = client.post("/api/timer/control", json={"action": "pause"})
        assert r_pause.json()["state"]["paused"] is True
        r_res = client.post("/api/timer/control", json={"action": "resume"})
        assert r_res.json()["state"]["paused"] is False

        # 4. Complete countdown phase -> logs full planned 1500s session & starts 300s break
        r_comp = client.post(
            "/api/timer/control",
            json={"action": "complete", "start_break": True},
        )
        assert r_comp.status_code == 200
        comp = r_comp.json()
        assert comp["logged_session"] is not None
        assert comp["logged_session"]["duration_seconds"] == 1500
        assert comp["logged_session"]["task_id"] == task["id"]
        assert comp["logged_session"]["note_id"] == note["id"]
        assert comp["state"]["active"] is True
        assert comp["state"]["phase"] == "break"
        assert comp["state"]["target_seconds"] == 300

        # 5. Completing break returns to idle WITHOUT adding a study session
        r_brk_end = client.post("/api/timer/control", json={"action": "complete"})
        assert r_brk_end.status_code == 200
        assert r_brk_end.json()["logged_session"] is None
        assert r_brk_end.json()["state"]["active"] is False
        assert r_brk_end.json()["stats"]["total_sessions"] == 1

        # 6. Start stopwatch, advance elapsed slightly, and stop/save
        r_sw = client.post(
            "/api/timer/control",
            json={
                "action": "start",
                "mode": "stopwatch",
                "subject": "Coding",
                "topic": "Timer API",
            },
        )
        assert r_sw.json()["state"]["mode"] == "stopwatch"
        # Nudge started_at_epoch back 65 seconds directly so stop logs 65s cleanly without sleeping
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE active_timer SET started_at_epoch = ? WHERE id = 1",
                (time.time() - 65.0,),
            )
        r_stop = client.post(
            "/api/timer/control",
            json={"action": "stop", "save_session": True, "notes": "Finished endpoints"},
        )
        assert r_stop.status_code == 200
        sw_logged = r_stop.json()["logged_session"]
        assert sw_logged is not None
        assert sw_logged["mode"] == "stopwatch"
        assert sw_logged["duration_seconds"] >= 64
        assert sw_logged["subject"] == "Coding"
        assert sw_logged["notes"] == "Finished endpoints"
        assert r_stop.json()["state"]["active"] is False

        # 7. Reset discards without saving
        client.post(
            "/api/timer/control",
            json={"action": "start", "mode": "countdown", "duration_minutes": 10},
        )
        r_reset = client.post("/api/timer/control", json={"action": "reset"})
        assert r_reset.json()["state"]["active"] is False
        assert r_reset.json()["logged_session"] is None
        assert r_reset.json()["stats"]["total_sessions"] == 2


def test_study_sessions_crud_fk_safety_stats_and_integrations(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        task = client.post("/api/tasks", json={"title": "Build ML Pipeline"}).json()["item"]
        note = client.post(
            "/api/notes", json={"title": "ML Architecture Note", "content": "## Layers"}
        ).json()["item"]

        # Create manual sessions across two subjects
        s1 = client.post(
            "/api/study/sessions",
            json={
                "subject": "Coding",
                "topic": "PyTorch DataLoader",
                "duration_minutes": 60,
                "task_id": task["id"],
                "note_id": note["id"],
                "notes": "Implemented multi-worker prefetch",
            },
        )
        assert s1.status_code == 201
        sess1 = s1.json()["item"]
        assert sess1["duration_seconds"] == 3600
        assert sess1["task_title"] == "Build ML Pipeline"

        s2 = client.post(
            "/api/study/sessions",
            json={"subject": "Art", "topic": "Anatomy gestures", "duration_minutes": 30},
        )
        assert s2.status_code == 201

        # Update session
        upd = client.patch(
            f"/api/study/sessions/{sess1['id']}",
            json={"topic": "PyTorch DataLoader v2", "duration_minutes": 90},
        )
        assert upd.status_code == 200
        assert upd.json()["item"]["duration_seconds"] == 5400

        # Deleting linked task preserves study session with task_id=None (ON DELETE SET NULL)
        assert client.delete(f"/api/tasks/{task['id']}").status_code == 200
        listed = client.get("/api/study/sessions").json()["items"]
        kept = next(x for x in listed if x["id"] == sess1["id"])
        assert kept["task_id"] is None
        assert kept["note_id"] == note["id"]

        # Stats, dashboard, search, export
        overview = client.get("/api/timer").json()
        assert overview["stats"]["today_seconds"] == 5400 + 1800
        assert overview["stats"]["today_sessions_count"] == 2
        assert overview["stats"]["streak_days"] == 1
        assert len(overview["stats"]["by_subject"]) == 2

        dash = client.get("/api/dashboard").json()
        assert dash["study"]["today_seconds"] == 7200
        assert "timer" in dash

        search = client.get("/api/search?q=DataLoader").json()["results"]
        assert any(r["type"] == "study" and r["id"] == sess1["id"] for r in search)

        export = client.get("/api/system/export").json()
        assert len(export["study_sessions"]) == 2
        assert export["study_stats"]["total_sessions"] == 2

        # Delete session
        assert client.delete(f"/api/study/sessions/{sess1['id']}").status_code == 200
        assert len(client.get("/api/study/sessions").json()["items"]) == 1


def test_agent_study_tools_offline_fallback_and_smart_capture(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        # 1. Agent tools: control_study_timer start/pause/resume/stop + log_study_session + get_study_stats
        res_start = tools.execute_tool(
            "control_study_timer",
            {
                "action": "start",
                "mode": "countdown",
                "duration_minutes": 45,
                "subject": "Lectures",
                "topic": "Agentic AI",
                "open_timer_view": True,
            },
        )
        assert res_start["navigate_to"] == "timer"
        assert res_start["action_badge"]["domain"] == "timer"
        assert res_start["result"]["timer"]["target_seconds"] == 2700

        res_log = tools.execute_tool(
            "log_study_session",
            {"subject": "Coding", "topic": "FastAPI routes", "duration_minutes": 40},
        )
        assert res_log["action_badge"]["domain"] == "timer"
        sid = res_log["result"]["session"]["id"]

        stats = tools.execute_tool("get_study_stats", {})
        assert stats["result"]["stats"]["total_sessions"] == 1
        assert stats["result"]["timer"]["active"] is True

        # navigate_ui supports timer & delete_item supports study_session
        assert tools.execute_tool("navigate_ui", {"view": "timer"})["navigate_to"] == "timer"
        assert (
            tools.execute_tool(
                "delete_item",
                {"domain": "study_session", "item_id": sid, "confirmed": True},
            )["result"]["deleted"]
            is True
        )

        # Reset live timer via tool
        tools.execute_tool("control_study_timer", {"action": "reset"})

        # 2. Smart capture explicit study mode
        cap = client.post(
            "/api/capture",
            json={"text": "Transformers paper reading 50m", "mode": "study"},
        )
        assert cap.status_code == 200
        assert any(r["type"] == "study_session" for r in cap.json()["results"])

        # 3. Offline voice agent timer start & studied logging (without Groq key)
        ch1 = client.post(
            "/api/agent/chat",
            json={"text": "Start a 25 minute Pomodoro timer for studying"},
        )
        assert ch1.status_code == 200
        assert ch1.json()["navigate_to"] == "timer"
        assert client.get("/api/timer/state").json()["state"]["active"] is True

        ch2 = client.post(
            "/api/agent/chat",
            json={"text": "I studied neural networks for 1.5 hours"},
        )
        assert ch2.status_code == 200
        assert client.get("/api/timer").json()["stats"]["total_sessions"] == 2


def test_running_timer_elapsed_survives_resync_and_client_formula(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))
    db._initialized_db_paths.clear()

    with TestClient(app) as client:
        r_start = client.post(
            "/api/timer/control",
            json={
                "action": "start",
                "mode": "countdown",
                "duration_minutes": 30,
                "subject": "Study",
            },
        )
        assert r_start.status_code == 200

        # Simulate 10 minutes elapsing while window is minimized (no pause events yet, so accumulated_seconds=0)
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE active_timer SET started_at_epoch = ? WHERE id = 1",
                (time.time() - 600.0,),
            )

        resynced = client.get("/api/timer/state").json()["state"]
        assert resynced["accumulated_seconds"] == 0
        assert resynced["elapsed_seconds"] >= 600
        assert resynced["remaining_seconds"] <= 1200

        meta_upd = client.post(
            "/api/timer/control",
            json={"action": "update_meta", "subject": "Study", "topic": "Minimized session"},
        ).json()["state"]
        assert meta_upd["elapsed_seconds"] >= 600
        assert meta_upd["remaining_seconds"] <= 1200

    # Verify client JS computes elapsed time from server timestamps/Date.now() and re-syncs on restore
    from pathlib import Path

    app_js = (Path(__file__).resolve().parent.parent / "app" / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "server_epoch" in app_js
    assert "elapsed_seconds" in app_js
    assert "Date.now()" in app_js
    assert "visibilitychange" in app_js
    assert "Date.now() - state.timerClientSyncedAt" in app_js
    assert "let elapsed = Number(t.accumulated_seconds || 0);" not in app_js
    assert "timerClientSyncedAt = performance.now()" not in app_js
