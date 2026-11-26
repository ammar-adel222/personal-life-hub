"""Tests for local .md/.txt folder sync and cross-app search."""

from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from app.server import app


def test_notes_folder_sync_roundtrip(monkeypatch, tmp_path):
    data_dir = tmp_path / "app_data"
    notes_dir = tmp_path / "my_notes"
    notes_dir.mkdir(parents=True)
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(data_dir))

    md_file = notes_dir / "workout_ideas.md"
    txt_file = notes_dir / "quick.txt"
    md_file.write_text("# Leg Day Reminders\nDo calf raises slowly.", encoding="utf-8")
    txt_file.write_text("Buy acrylic pens", encoding="utf-8")

    with TestClient(app) as client:
        client.patch("/api/settings", json={"notes_folder": str(notes_dir)})
        r_sync = client.post("/api/notes/sync")
        assert r_sync.status_code == 200
        stats = r_sync.json()
        assert stats["imported"] == 2
        assert stats["updated"] == 0

        notes = client.get("/api/notes").json()["items"]
        assert len(notes) == 2
        md_note = next(n for n in notes if n["title"] == "Leg Day Reminders")

        # Update disk file with newer mtime and resync
        new_mtime = time.time() + 5
        md_file.write_text("# Leg Day Reminders\nAdd Romanian deadlifts.", encoding="utf-8")
        os.utime(md_file, (new_mtime, new_mtime))

        r_sync2 = client.post("/api/notes/sync")
        assert r_sync2.status_code == 200
        assert r_sync2.json()["updated"] == 1

        refreshed = next(
            n for n in client.get("/api/notes").json()["items"] if n["id"] == md_note["id"]
        )
        assert "Romanian deadlifts" in refreshed["content"]

        # Edit synced note in app -> writes back to disk file
        r_patch = client.patch(
            f"/api/notes/{md_note['id']}",
            json={"content": "# Leg Day Reminders\nAdd Romanian deadlifts and squats."},
        )
        assert r_patch.status_code == 200
        assert "squats" in md_file.read_text(encoding="utf-8")

        # Create a new note in app with save_to_folder=True
        r_new = client.post(
            "/api/notes",
            json={"title": "Game Lore", "content": "Elden Ring boss notes", "save_to_folder": True},
        )
        assert r_new.status_code == 201
        created = r_new.json()["item"]
        assert created["source_path"] is not None
        assert (notes_dir / "Game-Lore.md").exists()

        # Cross-app search finds synced notes
        search_res = client.get("/api/search?q=Romanian").json()["results"]
        assert any(hit["type"] == "note" and hit["id"] == md_note["id"] for hit in search_res)
