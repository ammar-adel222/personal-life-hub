"""Desktop entry point for Personal Life Hub (FastAPI + Edge WebView2)."""

from __future__ import annotations

import ctypes
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import traceback
from typing import IO, Any

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


def _ensure_std_streams() -> tuple[IO[str] | None, Any, Any]:
    """Ensure sys.stdout and sys.stderr are valid streams when launched via pythonw.exe."""
    orig_out = sys.stdout
    orig_err = sys.stderr
    if orig_out is not None and orig_err is not None:
        return None, orig_out, orig_err

    from app.config import get_data_dir

    handle: IO[str] | None = None
    try:
        log_path = get_data_dir() / "app.log"
        handle = open(log_path, "a", encoding="utf-8", buffering=1)
    except Exception:
        handle = open(os.devnull, "a", encoding="utf-8")

    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle
    return handle, orig_out, orig_err


def _show_error_popup(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            message,
            "Personal Life Hub — Startup Error",
            0x10,
        )
    except Exception:
        pass


def main() -> int:
    smoke_mode = "--smoke-test" in sys.argv
    temp_smoke_dir: str | None = None
    if smoke_mode and not os.environ.get("LIFEHUB_DATA_DIR"):
        temp_smoke_dir = tempfile.mkdtemp(prefix="lifehub_smoke_")
        os.environ["LIFEHUB_DATA_DIR"] = temp_smoke_dir

    log_handle, orig_out, orig_err = _ensure_std_streams()
    try:
        from app import desktop_host, dialogs
        from app.config import load_settings
        from app.server import app

        port = _find_free_port()
        base_url = f"http://127.0.0.1:{port}"
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_config=None,
            access_log=False,
        )
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

        exiting = [False]

        def _request_exit() -> None:
            exiting[0] = True
            try:
                window.destroy()
            except Exception:
                pass

        def _on_closing() -> bool | None:
            if not exiting[0] and load_settings().get("minimize_to_tray", True):
                threading.Timer(0.05, window.hide).start()
                return False
            return True

        window.events.closing += _on_closing
        desktop_host.host.start(window=window, on_quit=_request_exit)
        try:
            webview.start()
        finally:
            exiting[0] = True
            desktop_host.host.stop()
            server.should_exit = True
            thread.join(timeout=3.0)
        return 0
    except Exception as exc:
        if smoke_mode:
            raise
        tb = traceback.format_exc()
        try:
            from app.config import get_data_dir

            (get_data_dir() / "app.log").write_text(tb, encoding="utf-8")
        except Exception:
            pass
        _show_error_popup(f"Personal Life Hub failed to start:\n\n{exc}")
        return 1
    finally:
        sys.stdout = orig_out
        sys.stderr = orig_err
        if log_handle is not None:
            try:
                log_handle.close()
            except Exception:
                pass
        if temp_smoke_dir:
            shutil.rmtree(temp_smoke_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
