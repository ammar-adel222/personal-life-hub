"""Tests for Global Hotkey & Tray settings, Watchlist & Playlists, Local Cloud Sync, and Direct Google Drive API."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from fastapi.testclient import TestClient

from app import agent, ai, cloud_sync, db, desktop_host, tools, watchlist
from app.config import get_gdrive_credentials_path, get_gdrive_token_path, load_settings
from app.server import app


def _isolate_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LIFEHUB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIFEHUB_DISABLE_AUTO_SYNC", "1")
    db._initialized_db_paths.clear()


class _FakeDriveFiles:
    """In-memory subset of Google Drive v3 `service.files()` for unit testing."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self._seq = 0
        self.created_calls: list[dict[str, Any]] = []
        self.updated_calls: list[dict[str, Any]] = []

    def list(self, q: str = "", spaces: str = "drive", fields: str = "", pageSize: int = 10):
        name_m = re.search(r"name\s*=\s*'((?:\\'|[^'])*)'", q)
        mime_m = re.search(r"mimeType\s*=\s*'([^']+)'", q)
        parent_m = re.search(r"'([^']+)'\s+in\s+parents", q)

        target_name = name_m.group(1).replace("\\'", "'").replace("\\\\", "\\") if name_m else None
        target_mime = mime_m.group(1) if mime_m else None
        target_parent = parent_m.group(1) if parent_m else None

        matched = []
        for fid, obj in self.items.items():
            if target_name is not None and obj["name"] != target_name:
                continue
            if target_mime is not None and obj.get("mimeType") != target_mime:
                continue
            if target_parent is not None and target_parent not in obj.get("parents", []):
                continue
            matched.append({"id": fid, "name": obj["name"]})

        class _Exec:
            def __init__(self, res: dict[str, Any]) -> None:
                self._res = res

            def execute(self) -> dict[str, Any]:
                return self._res

        return _Exec({"files": matched[:pageSize]})

    def create(self, body: dict[str, Any], media_body: Any = None, fields: str = "id"):
        self._seq += 1
        fid = f"drv_{self._seq}"
        content = ""
        if media_body is not None and hasattr(media_body, "_fd"):
            content = media_body._fd.getvalue().decode("utf-8", errors="ignore")
        elif media_body is not None and hasattr(media_body, "getbytes"):
            content = media_body.getbytes(0, media_body.size()).decode("utf-8", errors="ignore")
        rec = {
            "id": fid,
            "name": body.get("name", ""),
            "mimeType": body.get("mimeType", "application/octet-stream"),
            "parents": list(body.get("parents") or []),
            "content": content,
        }
        self.items[fid] = rec
        self.created_calls.append(rec)

        class _Exec:
            def execute(self_inner) -> dict[str, Any]:
                return {"id": fid}

        return _Exec()

    def update(self, fileId: str, media_body: Any = None, fields: str = "id"):
        content = ""
        if media_body is not None and hasattr(media_body, "getbytes"):
            content = media_body.getbytes(0, media_body.size()).decode("utf-8", errors="ignore")
        if fileId in self.items:
            self.items[fileId]["content"] = content
        self.updated_calls.append({"id": fileId, "content": content})

        class _Exec:
            def execute(self_inner) -> dict[str, Any]:
                return {"id": fileId}

        return _Exec()


class _FakeDriveService:
    def __init__(self) -> None:
        self._files = _FakeDriveFiles()

    def files(self) -> _FakeDriveFiles:
        return self._files


def test_hotkey_parsing_settings_and_global_state(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)

    mods, vk = desktop_host.parse_hotkey("ctrl+shift+space")
    assert mods == (desktop_host.MOD_CONTROL | desktop_host.MOD_SHIFT)
    assert vk == 0x20

    mods_fn, vk_fn = desktop_host.parse_hotkey("alt+f8")
    assert mods_fn == desktop_host.MOD_ALT
    assert vk_fn == 0x77

    assert desktop_host.format_hotkey_label("ctrl+shift+space") == "Ctrl+Shift+Space"
    assert desktop_host.format_hotkey_label("alt+f8") == "Alt+F8"

    for bad in ("", "space", "ctrl+unknownkey", "ctrl+a+b"):
        try:
            desktop_host.parse_hotkey(bad)
            raise AssertionError(f"Expected ValueError for {bad!r}")
        except ValueError:
            pass

    with TestClient(app) as client:
        st_resp = client.get("/api/voice/global-state")
        assert st_resp.status_code == 200
        gstate = st_resp.json()
        for key in ("hotkey", "hotkey_registered", "phase", "event_seq", "last_result"):
            assert key in gstate

        bad_patch = client.patch("/api/settings", json={"global_hotkey": "space"})
        assert bad_patch.status_code == 400

        ok_patch = client.patch(
            "/api/settings",
            json={
                "global_hotkey": "ctrl+alt+v",
                "minimize_to_tray": False,
                "floating_indicator": False,
                "sound_feedback": False,
                "gdrive_folder_name": "MyHub",
            },
        )
        assert ok_patch.status_code == 200
        pub = client.get("/api/settings").json()
        assert pub["global_hotkey"] == "ctrl+alt+v"
        assert pub["minimize_to_tray"] is False
        assert pub["floating_indicator"] is False
        assert pub["sound_feedback"] is False
        assert pub["gdrive_folder_name"] == "MyHub"


def test_watchlist_crud_rollup_metadata_ssrf_and_note_link(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)

    assert watchlist.is_safe_http_url("https://www.youtube.com/playlist?list=PL123") is True
    for unsafe in (
        "",
        "file:///C:/Windows/win.ini",
        "http://127.0.0.1:8000/secret",
        "http://localhost/admin",
        "http://192.168.1.10/router",
        "javascript:alert(1)",
    ):
        assert watchlist.is_safe_http_url(unsafe) is False

    yt_pl = watchlist.detect_url_info("https://www.youtube.com/playlist?list=PL_abc")
    assert yt_pl == {"source": "YouTube", "content_type": "playlist"}
    yt_vid = watchlist.detect_url_info("https://youtu.be/dQw4w9WgXcQ")
    assert yt_vid == {"source": "YouTube", "content_type": "video"}
    coursera = watchlist.detect_url_info("https://www.coursera.org/learn/machine-learning")
    assert coursera == {"source": "Coursera", "content_type": "course"}
    podcast = watchlist.detect_url_info("https://open.spotify.com/episode/12345")
    assert podcast == {"source": "Spotify", "content_type": "podcast"}

    async def fake_get(self, url: str, **kwargs):
        if "youtube.com/oembed" in url:
            return httpx.Response(200, json={"title": "CS229 Machine Learning Full Playlist"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><head><title>Fallback HTML Title</title></head></html>",
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    with TestClient(app) as client:
        # SSRF protection checks
        assert (
            client.post("/api/watchlist/fetch-meta", json={"url": "http://127.0.0.1:8080"}).status_code
            == 400
        )
        assert (
            client.post("/api/system/open-url", json={"url": "file:///etc/passwd"}).status_code == 400
        )
        assert (
            client.post("/api/system/open-url", json={"url": "http://localhost:1234"}).status_code
            == 400
        )

        # Fetch metadata cleanly via mocked HTTP
        meta_resp = client.post(
            "/api/watchlist/fetch-meta",
            json={"url": "https://www.youtube.com/playlist?list=PLcs229"},
        )
        assert meta_resp.status_code == 200
        meta = meta_resp.json()
        assert meta["title"] == "CS229 Machine Learning Full Playlist"
        assert meta["source"] == "YouTube"
        assert meta["content_type"] == "playlist"
        assert meta["fetched"] is True

        # Create without title -> auto-fills from URL metadata
        create_resp = client.post(
            "/api/watchlist",
            json={
                "url": "https://www.youtube.com/playlist?list=PLcs229",
                "category": "AI",
                "tags": ["ml", "stanford"],
                "progress_total": 10,
                "notes": "Watch twice a week",
            },
        )
        assert create_resp.status_code == 201
        item = create_resp.json()["item"]
        assert item["title"] == "CS229 Machine Learning Full Playlist"
        assert item["status"] == "to_watch"
        assert item["progress_current"] == 0
        assert item["progress_total"] == 10
        assert item["tags"] == ["ml", "stanford"]

        # Progress auto-rollup -> watching
        upd1 = client.patch(f"/api/watchlist/{item['id']}", json={"progress_current": 3})
        assert upd1.status_code == 200
        assert upd1.json()["item"]["status"] == "watching"
        assert upd1.json()["item"]["progress_current"] == 3

        # Progress auto-rollup -> watched (and caps overflow above progress_total)
        upd2 = client.patch(f"/api/watchlist/{item['id']}", json={"progress_current": 15})
        assert upd2.status_code == 200
        assert upd2.json()["item"]["status"] == "watched"
        assert upd2.json()["item"]["progress_current"] == 10

        # Open / Create linked note
        note_res1 = client.post(f"/api/watchlist/{item['id']}/open-note")
        assert note_res1.status_code == 200
        body1 = note_res1.json()
        assert body1["created"] is True
        assert body1["note"]["folder"] == "Lectures"
        assert "PLcs229" in body1["note"]["content"]
        assert "## To Research / Follow-Up" in body1["note"]["content"]

        note_res2 = client.post(f"/api/watchlist/{item['id']}/open-note")
        assert note_res2.status_code == 200
        body2 = note_res2.json()
        assert body2["created"] is False
        assert body2["note"]["id"] == body1["note"]["id"]

        # Dashboard, search, and export integration
        dash = db.get_dashboard()
        assert dash["watchlist_count"] == 1
        hits = db.search_everything("CS229")
        assert any(h["type"] == "watchlist" and "CS229" in h["title"] for h in hits)
        assert len(db.export_all_data()["watchlist"]) == 1

        # List & filter
        listed = client.get("/api/watchlist", params={"status": "watched", "q": "CS229"}).json()
        assert len(listed["items"]) == 1

        # Delete
        del_resp = client.delete(f"/api/watchlist/{item['id']}")
        assert del_resp.status_code == 200
        assert db.list_watchlist() == []


def test_watchlist_tools_offline_agent_and_capture(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    db.init_db()

    # Create via tool
    r1 = tools.execute_tool(
        "upsert_watchlist_item",
        {
            "title": "3Blue1Brown Neural Networks",
            "url": "https://www.youtube.com/playlist?list=PLZHQObOWTQDNU6R1_67000Dx_ZCJB-3pi",
            "category": "Math",
            "progress_current": 1,
            "progress_total": 4,
            "tags": ["math"],
        },
    )
    created = r1["result"]["watchlist_item"]
    assert created["status"] == "watching"
    assert created["source"] == "YouTube"
    assert created["content_type"] == "playlist"

    # Update via same URL/title -> merges tags and updates progress without duplicating
    r2 = tools.execute_tool(
        "upsert_watchlist_item",
        {
            "url": "https://www.youtube.com/playlist?list=PLZHQObOWTQDNU6R1_67000Dx_ZCJB-3pi",
            "progress_current": 2,
            "tags": ["neural-nets"],
        },
    )
    updated = r2["result"]["watchlist_item"]
    assert updated["id"] == created["id"]
    assert updated["progress_current"] == 2
    assert set(updated["tags"]) == {"math", "neural-nets"}

    # list_watchlist & navigate_ui & delete_item safety
    listed = tools.execute_tool("list_watchlist", {"status": "watching"})
    assert len(listed["result"]["items"]) == 1

    nav = tools.execute_tool("navigate_ui", {"view": "watchlist"})
    assert nav["navigate_to"] == "watchlist"

    deny = tools.execute_tool(
        "delete_item", {"domain": "watchlist", "item_id": created["id"], "confirmed": False}
    )
    assert deny["result"]["needs_confirmation"] is True
    assert db.get_watchlist_item(created["id"]) is not None

    allow = tools.execute_tool(
        "delete_item", {"domain": "watchlist", "item_id": created["id"], "confirmed": True}
    )
    assert allow["result"]["deleted"] is True
    assert db.get_watchlist_item(created["id"]) is None

    # Offline agent fallback handles watchlist URL / prefix
    reply, actions, _ = agent._offline_agent_fallback(
        "watchlist: https://www.youtube.com/playlist?list=PLxyz Rust Systems Course"
    )
    assert "Rust Systems Course" in reply
    assert any(a["domain"] == "watchlist" for a in actions)

    # Smart capture explicit watchlist mode
    import asyncio

    cap = asyncio.run(
        ai.smart_capture(
            "https://www.youtube.com/playlist?list=PLabc AI lectures",
            mode="watchlist",
        )
    )
    assert any(ex["type"] == "watchlist" for ex in cap["results"])
    assert len(db.list_watchlist()) >= 2


def test_local_cloud_folder_sync_and_idempotency(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    db.init_db()

    note = db.create_note(
        title="Transformers Self-Attention",
        content="## Overview\nScaled dot-product attention.\n- [ ] Read Vaswani paper",
        folder="Lectures",
        summary="Notes on attention scaling.",
        tags=["ai", "nlp"],
    )
    task = db.create_task(
        title="Finish CS229 Week 3",
        timeline="Next 10 days",
        frequency="Daily",
        due_date="2026-12-05",
    )
    db.add_task_item(task["id"], "Watch lecture 5")
    db.create_watchlist_item(
        title="CS229 Lecture 5",
        url="https://www.youtube.com/watch?v=test123",
        content_type="video",
        source="YouTube",
        category="AI",
        progress_current=1,
        progress_total=2,
        notes="Pause at minute 22",
    )

    mirror_dir = tmp_path / "CloudMirror"
    with TestClient(app) as client:
        r1 = client.post("/api/cloud/sync-folder", json={"folder": str(mirror_dir)})
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["files_written"] == 4
        assert d1["files_unchanged"] == 0
        assert d1["total_files"] == 4

        note_file = mirror_dir / "Notes" / "Lectures" / f"Transformers-Self-Attention-{note['id']}.md"
        assert note_file.exists()
        note_md = note_file.read_text(encoding="utf-8")
        assert "# Transformers Self-Attention" in note_md
        assert "Notebook: Lectures" in note_md
        assert "Scaled dot-product attention." in note_md

        wl_md = (mirror_dir / "Watchlist.md").read_text(encoding="utf-8")
        assert "## Currently Watching" in wl_md
        assert "[CS229 Lecture 5](https://www.youtube.com/watch?v=test123)" in wl_md

        tasks_md = (mirror_dir / "Tasks.md").read_text(encoding="utf-8")
        assert "Finish CS229 Week 3" in tasks_md
        assert "- [ ] Watch lecture 5" in tasks_md

        backup = json.loads((mirror_dir / "lifehub-backup.json").read_text(encoding="utf-8"))
        assert len(backup["notes"]) == 1
        assert len(backup["tasks"]) == 1
        assert len(backup["watchlist"]) == 1

        # Save folder in settings and verify idempotent second sync
        client.patch("/api/settings", json={"cloud_folder": str(mirror_dir)})
        r2 = client.post("/api/cloud/sync-folder", json={})
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["files_written"] == 0
        assert d2["files_unchanged"] == 4

        status = client.get("/api/cloud/status").json()
        assert status["last_sync"]
        assert "gdrive" in status


def test_gdrive_credentials_mocked_sync_and_disconnect(monkeypatch, tmp_path):
    _isolate_env(monkeypatch, tmp_path)
    db.init_db()

    db.create_note(
        title="Graphics Shader Math",
        content="Fresnel equations and PBR.",
        folder="Projects",
        tags=["shaders"],
    )
    db.create_watchlist_item(
        title="Blender Geometry Nodes",
        url="https://www.youtube.com/playlist?list=PLblender",
        content_type="playlist",
        source="YouTube",
    )

    with TestClient(app) as client:
        assert (
            client.post("/api/cloud/gdrive/credentials-raw", json={"credentials_json": "{bad"}).status_code
            == 400
        )
        assert (
            client.post("/api/cloud/gdrive/credentials-raw", json={"credentials_json": '{"foo": 1}'}).status_code
            == 400
        )

        valid_creds = json.dumps({"installed": {"client_id": "abc.apps.googleusercontent.com"}})
        ok = client.post("/api/cloud/gdrive/credentials-raw", json={"credentials_json": valid_creds})
        assert ok.status_code == 200
        assert ok.json()["has_credentials"] is True
        assert get_gdrive_credentials_path().exists()

        fake_drive = _FakeDriveService()
        first = cloud_sync.sync_to_gdrive(service=fake_drive)
        assert first["folder_name"] == "PersonalLifeHub"
        assert first["total_files"] == 4
        assert first["created"] == 4
        assert first["updated"] == 0

        # Confirm folder hierarchy created: PersonalLifeHub -> Notes -> Projects
        folder_names = [
            obj["name"]
            for obj in fake_drive._files.items.values()
            if obj["mimeType"] == cloud_sync.FOLDER_MIME
        ]
        assert set(folder_names) == {"PersonalLifeHub", "Notes", "Projects"}

        # Second sync reuses existing folders and updates files in place without duplicates
        second = cloud_sync.sync_to_gdrive(service=fake_drive)
        assert second["folder_id"] == first["folder_id"]
        assert second["created"] == 0
        assert second["updated"] == 4
        assert second["total_files"] == 4
        assert load_settings()["gdrive_last_sync"] == second["synced_at"]

        # Simulate connected token -> verify status & disconnect endpoint
        token_path = get_gdrive_token_path()
        token_path.write_text('{"token": "fake"}', encoding="utf-8")
        assert cloud_sync.get_gdrive_status()["connected"] is True

        disc = client.post("/api/cloud/gdrive/disconnect")
        assert disc.status_code == 200
        assert disc.json()["connected"] is False
        assert not token_path.exists()

