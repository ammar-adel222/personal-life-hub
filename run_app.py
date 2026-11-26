"""Desktop entry point for Personal Life Hub (FastAPI + Edge WebView2)."""

from __future__ import annotations

import os
import shutil
import socket
import sys
import tempfile
import threading
import time

import httpx
import uvicorn


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(base_url: str, timeout: float = 12.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/api/health", timeout=1.0)
            if resp.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.08)
    raise RuntimeError("Personal Life Hub backend did not become ready in time.")


def main() -> int:
    smoke_mode = "--smoke-test" in sys.argv
    temp_smoke_dir: str | None = None
    if smoke_mode and not os.environ.get("LIFEHUB_DATA_DIR"):
        temp_smoke_dir = tempfile.mkdtemp(prefix="lifehub_smoke_")
        os.environ["LIFEHUB_DATA_DIR"] = temp_smoke_dir

    try:
        from app import dialogs
        from app.server import app

        port = _find_free_port()
        base_url = f"http://127.0.0.1:{port}"
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        _wait_until_ready(base_url)

        import webview  # Verify pywebview & WebView2 bindings load cleanly

        if smoke_mode:
            for route in ("/api/health", "/", "/styles.css", "/app.js"):
                resp = httpx.get(f"{base_url}{route}", timeout=3.0)
                if resp.status_code != 200:
                    raise RuntimeError(f"Smoke test route {route} returned HTTP {resp.status_code}")
            server.should_exit = True
            thread.join(timeout=3.0)
            print("SMOKE_TEST_OK")
            return 0

        window = webview.create_window(
            "Personal Life Hub",
            base_url,
            width=1280,
            height=840,
            min_size=(940, 620),
            background_color="#0d1017",
        )
        dialogs.set_active_window(window)
        try:
            webview.start()
        finally:
            server.should_exit = True
            thread.join(timeout=3.0)
        return 0
    finally:
        if temp_smoke_dir:
            shutil.rmtree(temp_smoke_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
