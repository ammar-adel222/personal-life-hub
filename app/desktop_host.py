"""Win32 system-wide global hotkey, audio feedback, System Tray icon, and non-focus-stealing floating pill."""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import sys
import threading
import time
from typing import Any, Callable

from app import agent, cloud_sync, tts, voice
from app.config import get_tray_icon_path, load_settings

# Win32 Hotkey Modifiers
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# Win32 Window Messages & Constants
WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_HOTKEY = 0x0312
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 1
WM_RELOAD_HOTKEY = WM_USER + 2
WM_UPDATE_PILL = WM_USER + 3
WM_STOP_HOST = WM_USER + 4
WM_SHOW_MAIN = WM_USER + 5
HOST_WINDOW_TITLE = "PersonalLifeHubHost"

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

SM_CXSCREEN = 0
COLOR_WINDOW = 5

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

TPM_BOTTOMALIGN = 0x0020
TPM_LEFTALIGN = 0x0000
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
IDI_APPLICATION = 32512
IDC_ARROW = 32512
TRANSPARENT = 1
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020
DT_END_ELLIPSIS = 0x00008000

PS_SOLID = 0
FW_SEMIBOLD = 600

HOTKEY_ID = 1001
TIMER_TICK_ID = 1
TIMER_AUTOHIDE_ID = 2

MENU_OPEN = 2001
MENU_TOGGLE_REC = 2002
MENU_EXIT = 2003

_VK_MAP: dict[str, int] = {
    "space": 0x20,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "insert": 0x2D,
    "ins": 0x2D,
    "pause": 0x13,
    "grave": 0xC0,
    "`": 0xC0,
}
for _c in range(ord("a"), ord("z") + 1):
    _VK_MAP[chr(_c)] = _c - 32  # A-Z
for _d in range(0, 10):
    _VK_MAP[str(_d)] = 0x30 + _d
for _fn in range(1, 13):
    _VK_MAP[f"f{_fn}"] = 0x70 + (_fn - 1)


def parse_hotkey(hotkey_str: str) -> tuple[int, int]:
    """Parse a human-readable hotkey like 'ctrl+shift+space' into (win32_mods, vk_code)."""
    raw = (hotkey_str or "").strip().lower()
    if not raw:
        raise ValueError("Hotkey cannot be empty.")
    parts = [p.strip() for p in raw.replace("-", "+").split("+") if p.strip()]
    if len(parts) < 2:
        raise ValueError("Hotkey must include at least one modifier (Ctrl, Alt, Shift) and a key.")

    mods = 0
    vk: int | None = None
    for part in parts:
        if part in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part in ("win", "windows", "super", "meta"):
            mods |= MOD_WIN
        else:
            if vk is not None:
                raise ValueError(f"Multiple main keys specified in hotkey '{hotkey_str}'.")
            if part not in _VK_MAP:
                raise ValueError(f"Unsupported hotkey key: '{part}'.")
            vk = _VK_MAP[part]

    if mods == 0 or vk is None:
        raise ValueError("Hotkey must combine Ctrl/Alt/Shift with a main key (e.g. ctrl+shift+space).")
    return mods, vk


def format_hotkey_label(hotkey_str: str) -> str:
    parts = [p.strip() for p in (hotkey_str or "").split("+") if p.strip()]
    out = []
    for p in parts:
        is_fn = 2 <= len(p) <= 3 and p.startswith("f") and p[1:].isdigit()
        out.append(p.upper() if (is_fn or len(p) == 1) else p.title())
    return "+".join(out)


def play_beep(kind: str) -> None:
    """Play subtle non-blocking Windows audio feedback on start/stop/done/error."""
    try:
        settings = load_settings()
        if not settings.get("sound_feedback", True):
            return
    except Exception:
        pass
    if sys.platform != "win32":
        return

    def _run() -> None:
        try:
            import winsound

            if kind == "start":
                winsound.Beep(660, 65)
                winsound.Beep(880, 85)
            elif kind == "stop":
                winsound.Beep(880, 65)
                winsound.Beep(660, 75)
            elif kind == "done":
                winsound.Beep(1046, 95)
            elif kind == "error":
                winsound.Beep(330, 140)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="lifehub-beep").start()


# Shared thread-safe state exposed via /api/voice/global-state
_state_lock = threading.Lock()
_global_voice_state: dict[str, Any] = {
    "hotkey": "ctrl+shift+space",
    "hotkey_registered": False,
    "hotkey_error": "",
    "phase": "idle",  # idle | recording | processing | done | error
    "message": "",
    "elapsed_seconds": 0.0,
    "recording_started_at": 0.0,
    "event_seq": 0,
    "last_result": None,
}


def get_global_voice_state() -> dict[str, Any]:
    with _state_lock:
        snap = dict(_global_voice_state)
        if snap["phase"] == "recording" and snap["recording_started_at"] > 0:
            snap["elapsed_seconds"] = round(
                max(0.0, time.monotonic() - float(snap["recording_started_at"])), 1
            )
        return snap


def _set_global_state(**kwargs: Any) -> None:
    with _state_lock:
        _global_voice_state.update(kwargs)


def _rgb(r: int, g: int, b: int) -> int:
    return r | (g << 8) | (b << 16)


def focus_existing_instance() -> bool:
    """If another Personal Life Hub host window is already running, bring it to front."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        user32.FindWindowW.restype = wt.HWND
        user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.PostMessageW.restype = wt.BOOL
        hwnd = user32.FindWindowW(None, HOST_WINDOW_TITLE)
        if hwnd:
            user32.PostMessageW(hwnd, WM_SHOW_MAIN, 0, 0)
            return True
    except Exception:
        pass
    return False


def _ensure_tray_ico_file() -> str | None:
    """Generate a crisp dark indigo lightning .ico file inside %LOCALAPPDATA% if Pillow is present."""
    try:
        path = get_tray_icon_path()
        if path.exists() and path.stat().st_size > 100:
            return str(path)
        from PIL import Image, ImageDraw

        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([4, 4, 60, 60], radius=16, fill=(99, 102, 241, 255))
        bolt = [(36, 12), (20, 35), (32, 35), (27, 52), (45, 29), (33, 29)]
        draw.polygon(bolt, fill=(255, 255, 255, 255))
        img.save(str(path), format="ICO", sizes=[(16, 16), (32, 32)])
        return str(path)
    except Exception:
        return None


class DesktopHost:
    """Owns the Win32 global hotkey listener, System Tray icon, and floating pill overlay."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._window: Any = None
        self._on_quit: Callable[[], None] | None = None
        self._hwnd: int | None = None
        self._pill_hwnd: int | None = None
        self._tray_added: bool = False
        self._hicon: int | None = None
        self._hfont: int | None = None
        self._running: bool = False
        self._busy_processing: bool = False
        self._wndproc_ref: Any = None
        self._pill_proc_ref: Any = None

    def is_running(self) -> bool:
        return self._running

    def start(self, window: Any = None, on_quit: Callable[[], None] | None = None) -> None:
        if sys.platform != "win32" or self._running:
            return
        self._window = window
        self._on_quit = on_quit
        self._running = True
        self._thread = threading.Thread(
            target=self._win32_thread_main,
            daemon=True,
            name="lifehub-desktop-host",
        )
        self._thread.start()

    def reload_hotkey(self, new_hotkey: str | None = None) -> dict[str, Any]:
        if new_hotkey is not None:
            _set_global_state(hotkey=new_hotkey.strip().lower())
        if sys.platform == "win32" and self._hwnd:
            try:
                ctypes.windll.user32.PostMessageW(wt.HWND(self._hwnd), WM_RELOAD_HOTKEY, 0, 0)
            except Exception:
                pass
        return get_global_voice_state()

    def stop(self) -> None:
        self._running = False
        if sys.platform == "win32" and self._hwnd:
            try:
                ctypes.windll.user32.PostMessageW(wt.HWND(self._hwnd), WM_STOP_HOST, 0, 0)
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.5)
        self._thread = None

    def _trigger_window_show(self) -> None:
        if self._window is not None:
            try:
                self._window.show()
                if hasattr(self._window, "restore"):
                    self._window.restore()
            except Exception:
                pass

    def _request_pill_refresh(self, auto_hide_ms: int = 0) -> None:
        if sys.platform == "win32" and self._hwnd:
            try:
                ctypes.windll.user32.PostMessageW(
                    wt.HWND(self._hwnd), WM_UPDATE_PILL, wt.WPARAM(auto_hide_ms), 0
                )
            except Exception:
                pass

    def toggle_voice_from_hotkey(self) -> None:
        if self._busy_processing:
            return
        st = voice.recorder.status()
        if not st.get("recording"):
            self._start_global_recording()
        else:
            self._stop_and_process_global_recording()

    def _start_global_recording(self) -> None:
        settings = load_settings()
        try:
            voice.recorder.start(
                device=settings.get("mic_device"),
                auto_silence=bool(settings.get("auto_silence", False)),
                silence_seconds=float(settings.get("silence_seconds", 3.5)),
            )
            play_beep("start")
            _set_global_state(
                phase="recording",
                message="Listening…",
                elapsed_seconds=0.0,
                recording_started_at=time.monotonic(),
            )
            self._request_pill_refresh(0)
        except Exception as exc:
            play_beep("error")
            _set_global_state(
                phase="error",
                message=str(exc)[:80],
                recording_started_at=0.0,
            )
            self._request_pill_refresh(3400)

    def _stop_and_process_global_recording(self) -> None:
        self._busy_processing = True
        play_beep("stop")
        _set_global_state(
            phase="processing",
            message="Transcribing & thinking…",
            recording_started_at=0.0,
        )
        self._request_pill_refresh(0)

        def _worker() -> None:
            try:
                wav_bytes = voice.recorder.stop()
                settings = load_settings()
                api_key = settings.get("groq_api_key", "").strip()

                async def _run_pipeline() -> dict[str, Any]:
                    transcript = await voice.transcribe_wav_groq(wav_bytes, api_key)
                    res = await agent.run_agent_turn(transcript, session_id=None, source="voice")
                    return {"transcript": transcript, "agent": res}

                out = asyncio.run(_run_pipeline())
                agent_res = out["agent"]
                reply = str(agent_res.get("reply") or "Saved.").strip()
                actions = agent_res.get("actions") or []
                summary_lbl = (
                    " · ".join(str(a.get("label") or "") for a in actions[:2] if a.get("label"))
                    or reply
                )
                with _state_lock:
                    _global_voice_state["event_seq"] = int(_global_voice_state["event_seq"]) + 1
                    _global_voice_state["phase"] = "done"
                    _global_voice_state["message"] = summary_lbl[:78]
                    _global_voice_state["last_result"] = {
                        "transcript": out["transcript"],
                        "reply": reply,
                        "actions": actions,
                        "navigate_to": agent_res.get("navigate_to"),
                        "is_question": bool(agent_res.get("is_question")),
                        "session_id": agent_res.get("session_id"),
                    }
                play_beep("done")
                cloud_sync.maybe_auto_sync_cloud_folder()
                self._request_pill_refresh(3400)
            except Exception as exc:
                play_beep("error")
                with _state_lock:
                    _global_voice_state["event_seq"] = int(_global_voice_state["event_seq"]) + 1
                    _global_voice_state["phase"] = "error"
                    _global_voice_state["message"] = str(exc)[:78]
                self._request_pill_refresh(3600)
            finally:
                self._busy_processing = False

        threading.Thread(target=_worker, daemon=True, name="lifehub-global-voice").start()

    def _win32_thread_main(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        gdi32 = ctypes.windll.gdi32
        shell32 = ctypes.windll.shell32

        # Configure 64-bit safe argtypes/restypes
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style", wt.UINT),
                ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON),
                ("hCursor", wt.HICON),
                ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR),
            ]

        class PAINTSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hdc", wt.HDC),
                ("fErase", wt.BOOL),
                ("rcPaint", wt.RECT),
                ("fRestore", wt.BOOL),
                ("fIncUpdate", wt.BOOL),
                ("rgbReserved", ctypes.c_byte * 32),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wt.DWORD),
                ("hWnd", wt.HWND),
                ("uID", wt.UINT),
                ("uFlags", wt.UINT),
                ("uCallbackMessage", wt.UINT),
                ("hIcon", wt.HICON),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", wt.DWORD),
                ("dwStateMask", wt.DWORD),
                ("szInfo", ctypes.c_wchar * 256),
                ("uTimeoutOrVersion", wt.UINT),
                ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", wt.DWORD),
                ("guidItem", ctypes.c_byte * 16),
                ("hBalloonIcon", wt.HICON),
            ]

        user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        user32.RegisterClassW.restype = wt.ATOM
        user32.CreateWindowExW.argtypes = [
            wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
        ]
        user32.CreateWindowExW.restype = wt.HWND
        user32.DestroyWindow.argtypes = [wt.HWND]
        user32.DestroyWindow.restype = wt.BOOL
        user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wt.BOOL
        user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
        user32.SetWindowPos.restype = wt.BOOL
        user32.SetWindowRgn.argtypes = [wt.HWND, wt.HRGN, wt.BOOL]
        user32.SetWindowRgn.restype = ctypes.c_int
        user32.InvalidateRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT), wt.BOOL]
        user32.InvalidateRect.restype = wt.BOOL
        user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        user32.GetClientRect.restype = wt.BOOL
        user32.BeginPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.BeginPaint.restype = wt.HDC
        user32.EndPaint.argtypes = [wt.HWND, ctypes.POINTER(PAINTSTRUCT)]
        user32.EndPaint.restype = wt.BOOL
        user32.FillRect.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), wt.HBRUSH]
        user32.FillRect.restype = ctypes.c_int
        user32.DrawTextW.argtypes = [wt.HDC, wt.LPCWSTR, ctypes.c_int, ctypes.POINTER(wt.RECT), wt.UINT]
        user32.DrawTextW.restype = ctypes.c_int
        user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int, ctypes.c_int, wt.UINT]
        user32.LoadImageW.restype = wt.HANDLE
        user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
        user32.LoadIconW.restype = wt.HICON
        user32.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
        user32.LoadCursorW.restype = wt.HICON
        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wt.HMENU
        user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, wt.UINT_PTR if hasattr(wt, "UINT_PTR") else ctypes.c_size_t, wt.LPCWSTR]
        user32.AppendMenuW.restype = wt.BOOL
        user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID]
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.DestroyMenu.argtypes = [wt.HMENU]
        user32.DestroyMenu.restype = wt.BOOL
        user32.SetForegroundWindow.argtypes = [wt.HWND]
        user32.SetForegroundWindow.restype = wt.BOOL
        user32.SetTimer.argtypes = [wt.HWND, ctypes.c_size_t, wt.UINT, wt.LPVOID]
        user32.SetTimer.restype = ctypes.c_size_t
        user32.KillTimer.argtypes = [wt.HWND, ctypes.c_size_t]
        user32.KillTimer.restype = wt.BOOL
        user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
        user32.GetMessageW.restype = wt.BOOL
        user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.TranslateMessage.restype = wt.BOOL
        user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
        user32.RegisterHotKey.restype = wt.BOOL
        user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wt.BOOL
        user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user32.PostMessageW.restype = wt.BOOL

        kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        kernel32.GetModuleHandleW.restype = wt.HINSTANCE

        gdi32.CreateFontW.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD,
            wt.LPCWSTR,
        ]
        gdi32.CreateFontW.restype = wt.HFONT
        gdi32.CreateSolidBrush.argtypes = [wt.COLORREF]
        gdi32.CreateSolidBrush.restype = wt.HBRUSH
        gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wt.COLORREF]
        gdi32.CreatePen.restype = wt.HPEN
        gdi32.GetStockObject.argtypes = [ctypes.c_int]
        gdi32.GetStockObject.restype = wt.HGDIOBJ
        gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        gdi32.SelectObject.restype = wt.HGDIOBJ
        gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
        gdi32.DeleteObject.restype = wt.BOOL
        gdi32.RoundRect.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        gdi32.RoundRect.restype = wt.BOOL
        gdi32.Ellipse.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        gdi32.Ellipse.restype = wt.BOOL
        gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        gdi32.CreateRoundRectRgn.restype = wt.HRGN
        gdi32.SetBkMode.argtypes = [wt.HDC, ctypes.c_int]
        gdi32.SetBkMode.restype = ctypes.c_int
        gdi32.SetTextColor.argtypes = [wt.HDC, wt.COLORREF]
        gdi32.SetTextColor.restype = wt.COLORREF

        shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.Shell_NotifyIconW.restype = wt.BOOL

        hinst = kernel32.GetModuleHandleW(None)
        ico_path = _ensure_tray_ico_file()
        if ico_path:
            self._hicon = user32.LoadImageW(None, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if not self._hicon:
            self._hicon = user32.LoadIconW(None, wt.LPCWSTR(IDI_APPLICATION))

        self._hfont = gdi32.CreateFontW(
            -13, 0, 0, 0, FW_SEMIBOLD, 0, 0, 0, 1, 0, 0, 5, 0, "Segoe UI"
        )

        host_ref = self

        def _register_current_hotkey(hwnd: int) -> None:
            user32.UnregisterHotKey(hwnd, HOTKEY_ID)
            hk = load_settings().get("global_hotkey", "ctrl+shift+space").strip().lower()
            if not hk:
                _set_global_state(hotkey="", hotkey_registered=False, hotkey_error="Disabled in Settings")
                return
            try:
                mods, vk = parse_hotkey(hk)
                ok = user32.RegisterHotKey(hwnd, HOTKEY_ID, mods | MOD_NOREPEAT, vk)
                if ok:
                    _set_global_state(hotkey=hk, hotkey_registered=True, hotkey_error="")
                else:
                    err = kernel32.GetLastError()
                    _set_global_state(
                        hotkey=hk,
                        hotkey_registered=False,
                        hotkey_error=f"Hotkey busy in another app (Win32 error {err})",
                    )
            except ValueError as exc:
                _set_global_state(hotkey=hk, hotkey_registered=False, hotkey_error=str(exc))

        def _show_or_hide_pill(auto_hide_ms: int = 0) -> None:
            pill = host_ref._pill_hwnd
            if not pill:
                return
            user32.KillTimer(host_ref._hwnd, TIMER_AUTOHIDE_ID)
            settings = load_settings()
            snap = get_global_voice_state()
            if snap["phase"] == "idle" or not settings.get("floating_indicator", True):
                user32.KillTimer(host_ref._hwnd, TIMER_TICK_ID)
                user32.ShowWindow(pill, SW_HIDE)
                return

            screen_w = user32.GetSystemMetrics(SM_CXSCREEN) or 1280
            pill_w, pill_h = 330, 42
            pos_x = max(12, (screen_w - pill_w) // 2)
            pos_y = 18
            user32.SetWindowPos(
                pill,
                wt.HWND(HWND_TOPMOST),
                pos_x,
                pos_y,
                pill_w,
                pill_h,
                SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
            user32.ShowWindow(pill, SW_SHOWNOACTIVATE)
            user32.InvalidateRect(pill, None, True)
            if snap["phase"] == "recording":
                user32.SetTimer(host_ref._hwnd, TIMER_TICK_ID, 250, None)
            else:
                user32.KillTimer(host_ref._hwnd, TIMER_TICK_ID)
            if auto_hide_ms > 0:
                user32.SetTimer(host_ref._hwnd, TIMER_AUTOHIDE_ID, auto_hide_ms, None)

        def _pill_wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_PAINT:
                ps = PAINTSTRUCT()
                hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
                try:
                    rc = wt.RECT()
                    user32.GetClientRect(hwnd, ctypes.byref(rc))
                    bg_brush = gdi32.CreateSolidBrush(_rgb(19, 24, 35))
                    user32.FillRect(hdc, ctypes.byref(rc), bg_brush)
                    gdi32.DeleteObject(bg_brush)

                    snap = get_global_voice_state()
                    phase = snap["phase"]
                    if phase == "recording":
                        accent = _rgb(239, 68, 68)
                        label = (
                            f"Recording {snap['elapsed_seconds']:.1f}s · "
                            f"{format_hotkey_label(snap['hotkey'])} to stop"
                        )
                    elif phase == "processing":
                        accent = _rgb(99, 102, 241)
                        label = snap["message"] or "Transcribing & filing…"
                    elif phase == "done":
                        accent = _rgb(16, 185, 129)
                        label = f"Saved: {snap['message']}" if snap["message"] else "Saved!"
                    else:
                        accent = _rgb(239, 68, 68)
                        label = snap["message"] or "Voice error"

                    pen = gdi32.CreatePen(PS_SOLID, 2, accent)
                    old_pen = gdi32.SelectObject(hdc, pen)
                    null_brush = gdi32.GetStockObject(5)  # HOLLOW_BRUSH
                    old_brush = gdi32.SelectObject(hdc, null_brush)
                    gdi32.RoundRect(hdc, 1, 1, rc.right - 1, rc.bottom - 1, 16, 16)
                    gdi32.SelectObject(hdc, old_pen)
                    gdi32.SelectObject(hdc, old_brush)
                    gdi32.DeleteObject(pen)

                    # Status indicator dot
                    dot_brush = gdi32.CreateSolidBrush(accent)
                    dot_pen = gdi32.CreatePen(PS_SOLID, 1, accent)
                    gdi32.SelectObject(hdc, dot_brush)
                    gdi32.SelectObject(hdc, dot_pen)
                    gdi32.Ellipse(hdc, 14, 15, 24, 25)
                    gdi32.DeleteObject(dot_brush)
                    gdi32.DeleteObject(dot_pen)

                    gdi32.SetBkMode(hdc, TRANSPARENT)
                    gdi32.SetTextColor(hdc, _rgb(230, 235, 245))
                    if host_ref._hfont:
                        gdi32.SelectObject(hdc, host_ref._hfont)
                    text_rc = wt.RECT(32, 0, rc.right - 12, rc.bottom)
                    user32.DrawTextW(
                        hdc,
                        label,
                        -1,
                        ctypes.byref(text_rc),
                        DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
                    )
                finally:
                    user32.EndPaint(hwnd, ctypes.byref(ps))
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        def _show_tray_menu(hwnd: int) -> None:
            menu = user32.CreatePopupMenu()
            is_rec = voice.recorder.status().get("recording", False)
            rec_label = "⏹ Stop Voice Recording" if is_rec else "🎙️ Start Voice Recording"
            user32.AppendMenuW(menu, MF_STRING, MENU_OPEN, "Open Personal Life Hub")
            user32.AppendMenuW(menu, MF_STRING, MENU_TOGGLE_REC, rec_label)
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, MENU_EXIT, "Exit")

            pt = wt.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            user32.SetForegroundWindow(hwnd)
            cmd = user32.TrackPopupMenu(
                menu,
                TPM_LEFTALIGN | TPM_BOTTOMALIGN | TPM_RETURNCMD | TPM_RIGHTBUTTON,
                pt.x,
                pt.y,
                0,
                hwnd,
                None,
            )
            user32.DestroyMenu(menu)
            if cmd == MENU_OPEN:
                host_ref._trigger_window_show()
            elif cmd == MENU_TOGGLE_REC:
                host_ref.toggle_voice_from_hotkey()
            elif cmd == MENU_EXIT:
                if host_ref._on_quit:
                    host_ref._on_quit()

        def _host_wndproc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
            if msg == WM_HOTKEY and wparam == HOTKEY_ID:
                host_ref.toggle_voice_from_hotkey()
                return 0
            if msg == WM_RELOAD_HOTKEY:
                _register_current_hotkey(hwnd)
                return 0
            if msg == WM_UPDATE_PILL:
                _show_or_hide_pill(int(wparam or 0))
                return 0
            if msg == WM_TIMER:
                if wparam == TIMER_TICK_ID:
                    st = voice.recorder.status()
                    if st.get("auto_stopped") and not host_ref._busy_processing:
                        host_ref._stop_and_process_global_recording()
                    elif host_ref._pill_hwnd:
                        user32.InvalidateRect(host_ref._pill_hwnd, None, True)
                elif wparam == TIMER_AUTOHIDE_ID:
                    user32.KillTimer(hwnd, TIMER_AUTOHIDE_ID)
                    _set_global_state(phase="idle", message="")
                    if host_ref._pill_hwnd:
                        user32.ShowWindow(host_ref._pill_hwnd, SW_HIDE)
                return 0
            if msg == WM_TRAYICON:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    host_ref._trigger_window_show()
                elif event == WM_RBUTTONUP:
                    _show_tray_menu(hwnd)
                return 0
            if msg == WM_SHOW_MAIN:
                host_ref._trigger_window_show()
                return 0
            if msg == WM_STOP_HOST:
                user32.UnregisterHotKey(hwnd, HOTKEY_ID)
                if host_ref._tray_added:
                    nid = NOTIFYICONDATAW()
                    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                    nid.hWnd = hwnd
                    nid.uID = 1
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
                    host_ref._tray_added = False
                if host_ref._pill_hwnd:
                    user32.DestroyWindow(host_ref._pill_hwnd)
                    host_ref._pill_hwnd = None
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc_ref = WNDPROC(_host_wndproc)
        self._pill_proc_ref = WNDPROC(_pill_wndproc)

        host_cls = f"PersonalLifeHubHost_{id(self)}"
        pill_cls = f"PersonalLifeHubPill_{id(self)}"

        wc_host = WNDCLASSW()
        wc_host.lpfnWndProc = self._wndproc_ref
        wc_host.hInstance = hinst
        wc_host.lpszClassName = host_cls
        user32.RegisterClassW(ctypes.byref(wc_host))

        wc_pill = WNDCLASSW()
        wc_pill.lpfnWndProc = self._pill_proc_ref
        wc_pill.hInstance = hinst
        wc_pill.hCursor = user32.LoadCursorW(None, wt.LPCWSTR(IDC_ARROW))
        wc_pill.lpszClassName = pill_cls
        user32.RegisterClassW(ctypes.byref(wc_pill))

        self._hwnd = user32.CreateWindowExW(
            0, host_cls, HOST_WINDOW_TITLE, 0, 0, 0, 0, 0, None, None, hinst, None
        )
        self._pill_hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            pill_cls,
            "Recording Indicator",
            WS_POPUP,
            400,
            20,
            330,
            42,
            None,
            None,
            hinst,
            None,
        )
        if self._pill_hwnd:
            rgn = gdi32.CreateRoundRectRgn(0, 0, 331, 43, 16, 16)
            user32.SetWindowRgn(self._pill_hwnd, rgn, True)

        if self._hwnd:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAYICON
            nid.hIcon = self._hicon
            hk_lbl = format_hotkey_label(load_settings().get("global_hotkey", "ctrl+shift+space"))
            nid.szTip = (f"Personal Life Hub ({hk_lbl} to Speak)" if hk_lbl else "Personal Life Hub")[:127]
            self._tray_added = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
            _register_current_hotkey(self._hwnd)

        msg_struct = wt.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg_struct), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg_struct))
            user32.DispatchMessageW(ctypes.byref(msg_struct))

        if self._hfont:
            gdi32.DeleteObject(self._hfont)
            self._hfont = None


host = DesktopHost()
