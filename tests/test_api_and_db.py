"""End-to-end API and SQLite tests for tasks, gym, art, gaming, dashboard, and settings."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app import db
from app.config import get_media_dir
from app.server import app


def test_tasks_and_want_to_do_promotion(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        r1 = client.post(
            "/api/tasks",
            json={"title": "Buy sketchbook", "list_type": "daily", "priority": "high"},
        )
        assert r1.status_code == 201
        daily = r1.json()["item"]
        assert daily["list_type"] == "daily"
        assert daily["done"] is False

        r2 = client.post(
            "/api/tasks",
            json={"title": "Learn blender sculpting", "list_type": "want_to_do", "category": "art"},
        )
        assert r2.status_code == 201
        want = r2.json()["item"]
        assert want["list_type"] == "want_to_do"

        # Promote Want-to-Do to Daily Tasks
        r_prom = client.patch(f"/api/tasks/{want['id']}", json={"list_type": "daily"})
        assert r_prom.status_code == 200
        assert r_prom.json()["item"]["list_type"] == "daily"

        # Complete daily task
        r_done = client.patch(f"/api/tasks/{daily['id']}", json={"done": True})
        assert r_done.status_code == 200
        assert r_done.json()["item"]["done"] is True
        assert r_done.json()["item"]["completed_at"] is not None

        dash = client.get("/api/dashboard").json()
        assert dash["daily_done_today_count"] == 1
        assert dash["daily_open_count"] == 1


def test_gym_days_auto_marking_and_personal_bests(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))
    today = db.today_str()

    with TestClient(app) as client:
        # Logging an exercise should automatically mark that date as a Gym Day
        r1 = client.post(
            "/api/gym/logs",
            json={"exercise": "Bench Press", "sets": 3, "reps": 8, "weight": 80, "unit": "kg", "date": today},
        )
        assert r1.status_code == 201

        r2 = client.post(
            "/api/gym/logs",
            json={"exercise": "Bench Press", "sets": 3, "reps": 5, "weight": 92.5, "unit": "kg", "date": "2026-11-20"},
        )
        assert r2.status_code == 201

        cal = client.get(f"/api/activity-days?domain=gym&month={today[:7]}").json()
        assert cal["today_active"] is True
        assert today in cal["all_dates"]

        # Toggle off and back on
        t_off = client.post("/api/activity-days", json={"domain": "gym", "date": today, "mode": "toggle"}).json()
        assert t_off["active"] is False
        t_on = client.post("/api/activity-days", json={"domain": "gym", "date": today, "mode": "toggle"}).json()
        assert t_on["active"] is True

        stats = client.get("/api/gym/exercises").json()["items"]
        assert len(stats) == 1
        assert stats[0]["exercise"] == "Bench Press"
        assert stats[0]["max_weight"] == 92.5
        assert stats[0]["max_reps"] == 8
        assert stats[0]["entries"] == 2


def test_art_lifecycle_and_reference_image_cleanup(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        r_day = client.post("/api/activity-days", json={"domain": "art"})
        assert r_day.status_code == 200
        assert r_day.json()["today_active"] is True

        r_art = client.post(
            "/api/art",
            json={"title": "Cyberpunk Alley", "status": "idea", "notes": "Neon puddle reflections"},
        )
        assert r_art.status_code == 201
        art_id = r_art.json()["item"]["id"]

        # Promote idea -> wip
        r_wip = client.patch(f"/api/art/{art_id}", json={"status": "wip"})
        assert r_wip.status_code == 200
        assert r_wip.json()["item"]["status"] == "wip"

        # Upload reference image
        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        r_up = client.post(
            f"/api/art/{art_id}/image",
            files={"file": ("ref.png", fake_png, "image/png")},
        )
        assert r_up.status_code == 200
        fname = r_up.json()["filename"]
        assert (get_media_dir() / fname).exists()

        # Serve image back cleanly
        r_media = client.get(f"/api/media/{fname}")
        assert r_media.status_code == 200
        assert r_media.content == fake_png

        # Delete image and verify media cleanup
        r_del_img = client.delete(f"/api/art/{art_id}/image/{fname}")
        assert r_del_img.status_code == 200
        assert not (get_media_dir() / fname).exists()


def test_gaming_crud_and_settings_key_masking(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        rg = client.post(
            "/api/games",
            json={
                "title": "Hollow Knight",
                "status": "playing",
                "platform": "PC",
                "rating": 9,
                "notes": "City of Tears next",
            },
        )
        assert rg.status_code == 201
        gid = rg.json()["item"]["id"]

        ru = client.patch(f"/api/games/{gid}", json={"status": "finished", "rating": 10})
        assert ru.status_code == 200
        assert ru.json()["item"]["status"] == "finished"
        assert ru.json()["item"]["rating"] == 10

        # Save API key and verify masking + preservation on unrelated PATCH
        rs1 = client.patch(
            "/api/settings",
            json={"groq_api_key": "gsk_supersecret1234567890abcd", "weight_unit": "lbs"},
        ).json()
        assert rs1["has_api_key"] is True
        assert rs1["groq_api_key_masked"] == "gsk_...abcd"
        assert "supersecret" not in str(rs1)

        rs2 = client.patch("/api/settings", json={"weight_unit": "kg"}).json()
        assert rs2["has_api_key"] is True
        assert rs2["groq_api_key_masked"] == "gsk_...abcd"
        assert rs2["weight_unit"] == "kg"

        # Explicit clear works
        rs3 = client.patch("/api/settings", json={"groq_api_key": ""}).json()
        assert rs3["has_api_key"] is False
        assert rs3["groq_api_key_masked"] == ""
