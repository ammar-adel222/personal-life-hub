"""Native Windows file & folder picker dialogs with safe fallback."""

from __future__ import annotations

import subprocess
import sys
from typing import Any

_active_window: Any = None


def set_active_window(window: Any) -> None:
    """Register the active pywebview window so dialogs can attach cleanly."""
    global _active_window
    _active_window = window


def _run_tk_dialog(script: str) -> str | None:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        out = (proc.stdout or "").strip()
        return out or None
    except Exception:
        return None


def pick_folder() -> str | None:
    """Prompt the user to pick a local folder and return its path, or None if cancelled."""
    if _active_window is not None:
        try:
            import webview

            result = _active_window.create_file_dialog(webview.FileDialog.FOLDER)
            if result and len(result) > 0:
                return str(result[0])
            return None
        except Exception:
            pass

    script = (
        "import tkinter as tk; from tkinter import filedialog; "
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True); "
        "p = filedialog.askdirectory(title='Select Notes Sync Folder'); "
        "print(p or ''); r.destroy()"
    )
    return _run_tk_dialog(script)


def pick_image_file() -> str | None:
    """Prompt the user to select an image file and return its path, or None if cancelled."""
    if _active_window is not None:
        try:
            import webview

            result = _active_window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("Image files (*.png;*.jpg;*.jpeg;*.webp;*.gif)", "All files (*.*)"),
            )
            if result and len(result) > 0:
                return str(result[0])
            return None
        except Exception:
            pass

    script = (
        "import tkinter as tk; from tkinter import filedialog; "
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True); "
        "p = filedialog.askopenfilename(title='Select Reference Image', "
        "filetypes=[('Images', '*.png *.jpg *.jpeg *.webp *.gif'), ('All files', '*.*')]); "
        "print(p or ''); r.destroy()"
    )
    return _run_tk_dialog(script)


def pick_json_file() -> str | None:
    """Prompt the user to select a .json file (e.g. Google Drive OAuth credentials.json)."""
    if _active_window is not None:
        try:
            import webview

            result = _active_window.create_file_dialog(
                webview.FileDialog.OPEN,
                allow_multiple=False,
                file_types=("JSON files (*.json)", "All files (*.*)"),
            )
            if result and len(result) > 0:
                return str(result[0])
            return None
        except Exception:
            pass

    script = (
        "import tkinter as tk; from tkinter import filedialog; "
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True); "
        "p = filedialog.askopenfilename(title='Select Google credentials.json', "
        "filetypes=[('JSON files', '*.json'), ('All files', '*.*')]); "
        "print(p or ''); r.destroy()"
    )
    return _run_tk_dialog(script)
