"""Tests for Smart Capture (AI + offline fallback), Ask My Life, and Whisper voice."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app import ai, voice
from app.server import app


def test_smart_capture_offline_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        r_gym = client.post("/api/capture", json={"text": "Squat 100kg 4x6"})
        assert r_gym.status_code == 200
        data_gym = r_gym.json()
        assert data_gym["ai_used"] is False
        assert data_gym["results"][0]["type"] == "gym_set"
        assert data_gym["results"][0]["item"]["weight"] == 100.0

        r_game = client.post("/api/capture", json={"text": "want to play Celeste"})
        assert r_game.status_code == 200
        assert r_game.json()["results"][0]["type"] == "game"
        assert r_game.json()["results"][0]["item"]["title"] == "Celeste"

        r_task = client.post("/api/capture", json={"text": "Clean desk cables"})
        assert r_task.status_code == 200
        assert r_task.json()["results"][0]["type"] == "task"

        r_note = client.post("/api/capture", json={"text": "note: Color palette\nCobalt blue and warm ochre"})
        assert r_note.status_code == 200
        assert r_note.json()["results"][0]["type"] == "note"


def test_smart_capture_mocked_groq_and_deduplication(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    async def fake_chat(messages, api_key, model, json_mode=False, temperature=0.2):
        payload = {
            "summary": "Logged workout, daily task, and updated Hades notes",
            "actions": [
                {
                    "type": "gym_set",
                    "exercise": "Pull Ups",
                    "sets": 3,
                    "reps": 10,
                    "weight": None,
                    "unit": "kg",
                },
                {"type": "task", "title": "Buy drawing charcoal", "priority": "high"},
                {
                    "type": "game",
                    "title": "Hades",
                    "status": "playing",
                    "notes": "Cleared Elysium with Spear",
                },
            ],
        }
        return json.dumps(payload), model

    monkeypatch.setattr(ai, "_call_groq_chat", fake_chat)

    with TestClient(app) as client:
        client.patch("/api/settings", json={"groq_api_key": "gsk_testkey123456"})
        client.post("/api/games", json={"title": "Hades", "status": "want_to_play", "notes": "Try aspect of Achilles"})

        res = client.post(
            "/api/capture",
            json={"text": "Did 3x10 pull ups, need to buy drawing charcoal, cleared Elysium in Hades"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ai_used"] is True
        assert len(body["results"]) == 3

        # Verify Hades was updated in place (deduplicated) rather than duplicated
        games = client.get("/api/games").json()["items"]
        hades_entries = [g for g in games if g["title"].lower() == "hades"]
        assert len(hades_entries) == 1
        assert hades_entries[0]["status"] == "playing"
        assert "Achilles" in hades_entries[0]["notes"]
        assert "Elysium" in hades_entries[0]["notes"]


def test_ask_my_life_and_voice_stop_modes(monkeypatch, tmp_path):
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))

    async def fake_chat(messages, api_key, model, json_mode=False, temperature=0.2):
        if json_mode:
            return (
                json.dumps(
                    {
                        "summary": "Added task from voice",
                        "actions": [{"type": "task", "title": "Order new sketchbook"}],
                    }
                ),
                model,
            )
        return "Your last Bench Press was 85kg 3x8.", model

    async def fake_whisper(wav_bytes, api_key):
        assert wav_bytes == b"FAKE_WAV_BYTES"
        return "Order new sketchbook"

    monkeypatch.setattr(ai, "_call_groq_chat", fake_chat)
    monkeypatch.setattr(voice, "transcribe_wav_groq", fake_whisper)
    monkeypatch.setattr(voice.recorder, "stop", lambda: b"FAKE_WAV_BYTES")

    with TestClient(app) as client:
        client.patch("/api/settings", json={"groq_api_key": "gsk_testkey123456"})
        client.post("/api/gym/logs", json={"exercise": "Bench Press", "sets": 3, "reps": 8, "weight": 85})

        r_ask = client.post("/api/ai/ask", json={"question": "What was my last bench press?"})
        assert r_ask.status_code == 200
        assert r_ask.json()["ai_used"] is True
        assert "85kg" in r_ask.json()["answer"]
        assert any(s["type"] == "gym" for s in r_ask.json()["sources"])

        # Voice stop in auto_ai mode
        r_v1 = client.post("/api/voice/stop", json={"voice_mode": "auto_ai"})
        assert r_v1.status_code == 200
        assert r_v1.json()["transcript"] == "Order new sketchbook"
        assert r_v1.json()["capture"]["results"][0]["type"] == "task"

        # Voice stop in fill_input mode (transcribe only)
        r_v2 = client.post("/api/voice/stop", json={"voice_mode": "fill_input"})
        assert r_v2.status_code == 200
        assert r_v2.json()["transcript"] == "Order new sketchbook"
        assert r_v2.json()["capture"] is None
