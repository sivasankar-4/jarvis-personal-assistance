"""Small, Windows-native desktop control primitives for Jarvis.

This module does not interpret user language or call an AI model.  It validates
parameters, asks Windows to perform narrowly defined actions, and returns an
honest result that callers can report or audit.
"""

from __future__ import annotations

import ctypes
import datetime
import json
import ntpath
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from PIL import ImageChops, ImageGrab
except ImportError:
    ImageChops = None
    ImageGrab = None


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    message: str
    data: dict = field(default_factory=dict)




@dataclass(frozen=True)
class AppInfo:
    name: str
    executable: str
    source: str


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    process_name: str
    class_name: str
    title: str
    is_foreground: bool


_ALIASES = {
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "code": "visual studio code",
    "chrome": "google chrome",
    "explorer": "file explorer",
}
_COMMON_APPS = {
    "google chrome": ("chrome", "chrome.exe"),
    "microsoft edge": ("msedge", "msedge.exe"),
    "file explorer": ("explorer", "explorer.exe"),
    "notepad": ("notepad", "notepad.exe"),
    "calculator": ("calc", "calc.exe"),
}
_EXPECTED_WINDOW_PROCESSES = {
    "notepad": "Notepad.exe",
    "calculator": "CalculatorApp.exe",
    "paint": "mspaint.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
}
_SAFE_SHORTCUTS = {
    "ctrl+a": (0x11, 0x41), "ctrl+c": (0x11, 0x43), "ctrl+f": (0x11, 0x46),
    "ctrl+s": (0x11, 0x53), "ctrl+v": (0x11, 0x56), "ctrl+x": (0x11, 0x58),
    "ctrl+z": (0x11, 0x5A), "alt+tab": (0x12, 0x09), "alt+f4": (0x12, 0x73),
    "win+d": (0x5B, 0x44),
    "enter": (0x0D,), "tab": (0x09,), "escape": (0x1B,), "backspace": (0x08,),
}
_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_WM_GETTEXT = 0x000D
_WM_GETTEXTLENGTH = 0x000E
_EM_REPLACESEL = 0x00C2
_EDIT_CONTROL_CLASSES = ("RichEditD2DPT", "RichEdit50W", "RichEdit20W", "Edit")
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_DIFF_PIXEL_THRESHOLD = 24
_DIFF_MIN_CHANGED_PIXELS = 100
_FO_DELETE = 0x0003
_FOF_ALLOWUNDO = 0x0040
_FOF_NOCONFIRMATION = 0x0010
_FOF_NOERRORUI = 0x0400
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [
        ("mi", _MouseInput),
        ("ki", _KeyBdInput),
        ("hi", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", ctypes.c_ulong), ("union", _InputUnion)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def _is_windows() -> bool:
    return os.name == "nt"


def _open_clipboard() -> bool:
    for attempt in range(3):
        if ctypes.windll.user32.OpenClipboard(0):
            return True
        if attempt < 2:
            time.sleep(0.05)
    return False


def _clipboard_prototypes() -> None:
    """Declare argtypes/restype for every Win32 function used by the clipboard
    helpers.  Assignments are idempotent; call at the top of each helper."""
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p

    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p

    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = ctypes.c_bool

    user32.OpenClipboard.argtypes = [ctypes.c_void_p]

    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p

    user32.GetClipboardData.argtypes = [ctypes.c_uint]
    user32.GetClipboardData.restype = ctypes.c_void_p


def _set_clipboard_text(text: str) -> bool:
    """Set Unicode clipboard text using movable global memory."""
    opened = False
    try:
        _clipboard_prototypes()
        if not _open_clipboard():
            return False
        opened = True
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.EmptyClipboard():
            return False
        payload = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(payload))
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return False
        try:
            ctypes.memmove(pointer, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(handle)
        return bool(user32.SetClipboardData(_CF_UNICODETEXT, handle))
    except Exception:
        return False
    finally:
        if opened:
            ctypes.windll.user32.CloseClipboard()


def _get_clipboard_text() -> str | None:
    """Return Unicode clipboard text, or None when it is unavailable."""
    opened = False
    try:
        _clipboard_prototypes()
        if not _open_clipboard():
            return None
        opened = True
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(_CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
    except Exception:
        return None
    finally:
        if opened:
            ctypes.windll.user32.CloseClipboard()


def write_clipboard(text: str) -> ToolResult:
    if not _is_windows():
        return _result("write_clipboard", False, "Desktop control is available only on Windows.")
    if len(text) > 100000:
        return _result("write_clipboard", False, "That text is too long to copy (limit 100,000 characters).")
    if not _set_clipboard_text(text):
        return _result("write_clipboard", False, "I couldn't open the clipboard.")
    if _get_clipboard_text() != text:
        return _result("write_clipboard", False, "I copied the text but could not confirm it is on the clipboard.")
    message = "Cleared the clipboard." if not text else f"Copied {len(text)} characters to the clipboard."
    return _result("write_clipboard", True, message, characters=len(text), verified=True)


def read_clipboard() -> ToolResult:
    if not _is_windows():
        return _result("read_clipboard", False, "Desktop control is available only on Windows.")
    text = _get_clipboard_text()
    if text is None:
        return _result("read_clipboard", False, "The clipboard doesn't contain text right now.")
    truncated = len(text) > 20000
    visible = text[:20000] + " […truncated]" if truncated else text
    return _result("read_clipboard", True, f"Clipboard text ({len(text)} characters): {visible}", characters=len(text), truncated=truncated)


def _enable_dpi_awareness() -> bool:
    """Best-effort DPI setup that must run before window metrics are read.

    Failure leaves the process in legacy logical-coordinate mode and tools still work.
    """
    if not _is_windows():
        return False
    try:
        user32 = ctypes.windll.user32
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.restype = ctypes.c_bool
        if set_context(ctypes.c_void_p(-4)):
            return True
    except Exception:
        pass
    try:
        result = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if result in (0, 0x80070005):
            return True
    except Exception:
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return True
    except Exception:
        pass
    return False


DPI_AWARENESS_ENABLED = _enable_dpi_awareness()


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def _audit_path() -> Path:
    override = os.environ.get("JARVIS_AUDIT_LOG")
    if override:
        return Path(override)
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "Jarvis" / "actions.jsonl"


def audit_action(
    tool: str,
    result: ToolResult,
    risk_level: str = "LOW",
    approval_status: str = "AUTONOMOUS",
) -> None:
    """Record execution metadata safely without sensitive private content."""
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if not result.ok and approval_status in ("PENDING", "NOT_APPROVED"):
            exec_status = "BLOCKED"
        elif result.ok:
            exec_status = "SUCCESS"
        else:
            exec_status = "FAILED"

        sensitive_keys = {
            "password", "passwd", "token", "api_key", "secret", "auth",
            "body", "message_body", "email_body", "text", "content", "url", "path",
        }
        safe_data = {}
        if isinstance(result.data, dict):
            for k, v in result.data.items():
                if k.lower() not in sensitive_keys:
                    safe_data[k] = v

        event = {
            "timestamp": int(time.time()),
            "tool": tool,
            "risk_classification": risk_level,
            "approval_status": approval_status,
            "execution_status": exec_status,
            "metadata": safe_data,
        }
        with path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(event) + "\n")
    except OSError:
        # Audit availability must not prevent a safe, user-requested action.
        pass


def _result(tool: str, ok: bool, message: str, **data) -> ToolResult:
    result = ToolResult(ok, message, data)
    audit_action(tool, result)
    return result


def _window_text(hwnd: int) -> str:
    try:
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value.strip()
    except (AttributeError, OSError):
        return ""


def _window_class_name(hwnd: int) -> str:
    try:
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value
    except (AttributeError, OSError):
        return ""


def _window_process_id(hwnd: int) -> int:
    try:
        process_id = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        return int(process_id.value)
    except (AttributeError, OSError):
        return 0


def _process_name(pid: int) -> str:
    if not pid:
        return ""
    handle = None
    try:
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return ""
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_uint32(len(buffer))
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return ntpath.basename(buffer.value)
    except Exception:
        return ""
    finally:
        if handle:
            try:
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass


def _window_is_cloaked(hwnd: int) -> bool:
    try:
        cloaked = ctypes.c_uint32()
        result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        return result == 0 and bool(cloaked.value)
    except (AttributeError, OSError):
        return False


def _packaged_window_process(hwnd: int, host_pid: int) -> tuple[int, str]:
    if _window_class_name(hwnd) != "ApplicationFrameWindow":
        return host_pid, _process_name(host_pid)

    resolved = (host_pid, _process_name(host_pid))
    try:
        user32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(child_hwnd, _lparam):
            child_pid = _window_process_id(child_hwnd)
            if (
                child_pid
                and child_pid != host_pid
                and _window_class_name(child_hwnd) == "Windows.UI.Core.CoreWindow"
            ):
                resolved = (child_pid, _process_name(child_pid))
                callback.resolved = resolved
                return False
            return True

        callback.resolved = resolved
        user32.EnumChildWindows(hwnd, callback, 0)
        return callback.resolved
    except (AttributeError, OSError, TypeError):
        return resolved


def observe_windows() -> list[WindowInfo]:
    """Return visible, titled, non-cloaked top-level windows."""
    if not _is_windows():
        return []
    try:
        user32 = ctypes.windll.user32
        foreground = int(user32.GetForegroundWindow())
        windows: list[WindowInfo] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd) or _window_is_cloaked(hwnd):
                return True
            title = _window_text(hwnd)
            if not title:
                return True
            host_pid = _window_process_id(hwnd)
            pid, process_name = _packaged_window_process(hwnd, host_pid)
            windows.append(
                WindowInfo(
                    hwnd=int(hwnd),
                    pid=pid,
                    process_name=process_name,
                    class_name=_window_class_name(hwnd),
                    title=title,
                    is_foreground=int(hwnd) == foreground,
                )
            )
            return True

        user32.EnumWindows(callback, 0)
        return windows
    except (AttributeError, OSError, TypeError):
        return []


def list_windows() -> ToolResult:
    if not _is_windows():
        return _result("list_windows", False, "Window observation is available only on Windows.")
    windows = observe_windows()
    foreground = next((window.process_name for window in windows if window.is_foreground), "unknown")
    summary = f"Found {len(windows)} windows; foreground process: {foreground}."
    return _result(
        "list_windows",
        True,
        summary,
        count=len(windows),
        processes=sorted({window.process_name for window in windows}),
    )


def wait_for_window(process_name: str, timeout_seconds: int) -> ToolResult:
    if not _is_windows():
        return _result("wait_for_window", False, "Window observation is available only on Windows.")
    target = process_name.strip().lower()
    if target.endswith(".exe"):
        target = target[:-4]
    timeout = max(1, min(15, timeout_seconds))
    started = time.monotonic()
    while True:
        for window in observe_windows():
            candidate = window.process_name.lower()
            if candidate.endswith(".exe"):
                candidate = candidate[:-4]
            if candidate == target:
                elapsed = time.monotonic() - started
                return _result(
                    "wait_for_window",
                    True,
                    f"Found a window for {window.process_name}.",
                    found=True,
                    process=window.process_name,
                    pid=window.pid,
                    elapsed_seconds=elapsed,
                    foreground=window.is_foreground,
                )
        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            return _result(
                "wait_for_window",
                False,
                f"Timed out waiting for process {process_name}.",
                found=False,
                process="",
                pid=None,
                elapsed_seconds=elapsed,
                foreground=False,
            )
        time.sleep(0.25)


def _app_paths_from_registry() -> list[AppInfo]:
    if not _is_windows():
        return []
    try:
        import winreg
    except ImportError:
        return []
    found = []
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    locations = (r"Software\Microsoft\Windows\CurrentVersion\App Paths",)
    for root in roots:
        for location in locations:
            try:
                with winreg.OpenKey(root, location) as parent:
                    index = 0
                    while True:
                        try:
                            key_name = winreg.EnumKey(parent, index)
                            index += 1
                        except OSError:
                            break
                        try:
                            with winreg.OpenKey(parent, key_name) as child:
                                value, _ = winreg.QueryValueEx(child, None)
                            executable = str(value).strip().strip('"')
                            candidate = Path(executable)
                            if candidate.is_file() and candidate.suffix.lower() == ".exe":
                                found.append(AppInfo(candidate.stem, str(candidate), "registry"))
                        except OSError:
                            continue
            except OSError:
                continue
    return found


def _vs_code_locations() -> list[Path]:
    """Return supported per-user and machine-wide VS Code executable paths."""
    roots = [
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramW6432"),
        os.environ.get("ProgramFiles(x86)"),
    ]
    locations = []
    for root in dict.fromkeys(root for root in roots if root):
        locations.append(Path(root) / "Programs" / "Microsoft VS Code" / "Code.exe")
        locations.append(Path(root) / "Microsoft VS Code" / "Code.exe")
    return locations


def discover_applications() -> list[AppInfo]:
    """Discover registered, installed, and PATH applications without a shell."""
    apps = {info.executable.lower(): info for info in _app_paths_from_registry()}
    for location in _vs_code_locations():
        if location.is_file():
            apps.setdefault(str(location).lower(), AppInfo("Visual Studio Code", str(location), "installed location"))
    for friendly_name, candidates in _COMMON_APPS.items():
        for candidate in candidates:
            executable = shutil.which(candidate)
            if executable:
                apps.setdefault(executable.lower(), AppInfo(friendly_name, executable, "PATH"))
                break
    return list(apps.values())


def find_application(name: str) -> AppInfo | None:
    if not isinstance(name, str) or not name.strip():
        return None
    requested = _normalize_name(_ALIASES.get(_normalize_name(name), name))
    apps = discover_applications()
    for app in apps:
        if requested == _normalize_name(app.name) or requested == _normalize_name(Path(app.executable).stem):
            return app
    matches = [app for app in apps if requested in _normalize_name(app.name)]
    return matches[0] if len(matches) == 1 else None


def launch_application(name: str) -> ToolResult:
    app = find_application(name)
    if not app:
        return _result("launch_application", False, f"I couldn't find an installed application named {name}.")
    try:
        process = subprocess.Popen([app.executable], close_fds=True)
    except OSError as exc:
        print(f"[Jarvis Windows] launch failed for {app.name} ({type(exc).__name__}, source={app.source})")
        return _result("launch_application", False, f"Windows could not start {app.name}.", error_type=type(exc).__name__)
    expected_process = _EXPECTED_WINDOW_PROCESSES.get(_normalize_name(app.name))
    if expected_process:
        try:
            verification = wait_for_window(expected_process, 5)
        except Exception:
            verification = None
        if verification and verification.ok:
            return _result(
                "launch_application",
                True,
                f"Opened {app.name}; window observed.",
                pid=verification.data.get("pid"),
                process=verification.data.get("process", expected_process),
                verified=True,
                source=app.source,
            )
        return _result(
            "launch_application",
            True,
            f"Launched {app.name} but could not confirm a window.",
            verified=False,
        )
    return _result("launch_application", True, f"Opened {app.name}.", pid=process.pid, source=app.source, verified=False)


def validate_path(target: str, base_directory: Path | str | None = None) -> Path:
    if not isinstance(target, str) or not target.strip() or "\x00" in target:
        raise ValueError("The path is empty or invalid.")
    requested = Path(target).expanduser()
    is_relative = not requested.is_absolute()
    candidate = requested
    base = None
    if is_relative:
        base = Path(base_directory or Path.cwd()).resolve()
        candidate = base / candidate
    resolved = candidate.resolve(strict=True)
    if is_relative and not resolved.is_relative_to(base):
        raise ValueError("Relative paths must stay inside the selected folder.")
    return resolved


def open_path(path: str, base_directory: Path | str | None = None) -> ToolResult:
    try:
        resolved = validate_path(path, base_directory)
    except (OSError, ValueError):
        return _result("open_path", False, "I couldn't find that file or folder.")
    if not _is_windows():
        return _result("open_path", False, "Desktop control is available only on Windows.")
    try:
        if resolved.is_dir():
            process = subprocess.Popen(["explorer.exe", str(resolved)], close_fds=True)
            return _result("open_path", True, f"Started File Explorer for {resolved.name or resolved.drive}.", pid=process.pid, kind="folder")
        os.startfile(str(resolved))
        return _result("open_path", True, f"Asked Windows to open {resolved.name} with its default application.", kind="file")
    except OSError:
        return _result("open_path", False, f"Windows could not open {resolved.name}.")


def open_website(url: str) -> ToolResult:
    if not isinstance(url, str) or not re.match(r"^https?://[^\s]+$", url, re.I):
        return _result("open_website", False, "I need a valid http or https website address.")
    if not _is_windows():
        return _result("open_website", False, "Desktop control is available only on Windows.")
    try:
        code = ctypes.windll.shell32.ShellExecuteW(None, "open", url, None, None, 1)
        if code <= 32:
            return _result("open_website", False, "Windows did not accept that website request.")
        lower_url = url.lower()
        if "youtube.com" in lower_url:
            site_name = "YouTube"
        elif "google.com" in lower_url:
            site_name = "Google"
        elif "github.com" in lower_url:
            site_name = "GitHub"
        elif "whatsapp.com" in lower_url:
            site_name = "WhatsApp"
        elif "gmail.com" in lower_url:
            site_name = "Gmail"
        else:
            site_name = None

        if site_name:
            return _result("open_website", True, f"Opened {site_name}.")
        return _result("open_website", True, "Opened website.")
    except (AttributeError, OSError):
        return _result("open_website", False, "Windows could not open the default browser.")


def foreground_window_title() -> str:
    if not _is_windows():
        return ""
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return ""
        buffer = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value.strip()
    except (AttributeError, OSError):
        return ""


def send_shortcut(shortcut: str) -> ToolResult:
    normalized = shortcut.lower().replace(" ", "")
    keys = _SAFE_SHORTCUTS.get(normalized)
    title = foreground_window_title()
    if not keys or not title:
        message = "I need a supported shortcut and an active application window."
        return _result("send_shortcut", False, message)
    try:
        for key in keys:
            ctypes.windll.user32.keybd_event(key, 0, 0, 0)
        for key in reversed(keys):
            ctypes.windll.user32.keybd_event(key, 0, _KEYUP, 0)
        return _result("send_shortcut", True, f"Sent {normalized} to {title}.", window=title)
    except (AttributeError, OSError):
        return _result("send_shortcut", False, "Windows could not send that shortcut.")


def verify_window_target(expected_window: str, title: str) -> bool:
    """Strictly verify that the foreground window matches the intended target.
    
    Correctly accounts for variations like 'Untitled - Notepad' or '*Untitled - Notepad'
    without weakening validation for mismatched windows.
    """
    if not isinstance(expected_window, str) or not isinstance(title, str):
        return False
    expected = re.sub(r"[^a-z0-9]+", " ", expected_window.lower()).strip()
    actual = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
    if not expected or not actual:
        return False

    resolved_expected = _ALIASES.get(expected, expected)

    if expected in actual or resolved_expected in actual:
        return True

    exp_words = [w for w in expected.split() if len(w) > 1]
    actual_words = set(actual.split())
    if exp_words and all(w in actual_words for w in exp_words):
        return True

    return False


def _find_edit_control(hwnd) -> int:
    """Return the largest visible native text control under hwnd, if any."""
    try:
        user32 = ctypes.windll.user32
        candidates = []

        def consider(candidate):
            if not user32.IsWindowVisible(candidate):
                return
            if _window_class_name(candidate).lower() not in {name.lower() for name in _EDIT_CONTROL_CLASSES}:
                return
            rect = _RECT()
            if not user32.GetWindowRect(candidate, ctypes.byref(rect)):
                return
            candidates.append(((rect.right - rect.left) * (rect.bottom - rect.top), candidate))

        consider(hwnd)
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def callback(child, _lparam):
            consider(child)
            return True

        user32.EnumChildWindows(hwnd, callback_type(callback), 0)
        return max(candidates, default=(0, 0))[1]
    except (AttributeError, OSError, TypeError):
        return 0


def _get_control_text(hwnd) -> str | None:
    """Read native edit-control content, or return None when it cannot be read."""
    try:
        user32 = ctypes.windll.user32
        length = user32.SendMessageW(hwnd, _WM_GETTEXTLENGTH, 0, 0)
        if length < 0:
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        if user32.SendMessageW(hwnd, _WM_GETTEXT, length + 1, buffer) < 0:
            return None
        return buffer.value
    except (AttributeError, OSError, TypeError):
        return None


def type_text(text: str, expected_window: str) -> ToolResult:
    """Type explicit text only when the named target is actually focused."""
    if not isinstance(text, str) or not text:
        return _result("type_text", False, "There is no text to type.")
    if not isinstance(expected_window, str) or not expected_window.strip():
        return _result("type_text", False, "Please name the focused application before I type.")
    title = foreground_window_title()
    if not title:
        return _result("type_text", False, f"I did not type because there is no focused window (expected: {expected_window}).")
    if not verify_window_target(expected_window, title):
        return _result(
            "type_text",
            False,
            f"I did not type because {expected_window} is not the focused window (current window: '{title}').",
            window=title,
        )
    if not _is_windows():
        return _result("type_text", False, "Desktop control is available only on Windows.")

    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        edit = _find_edit_control(hwnd)
        if not edit:
            return _result("type_text", False, f"I couldn't find a text area in {title} to type into.")
        before = _get_control_text(edit)
        insert_text = text.replace("\n", "\r\n")
        ctypes.windll.user32.SendMessageW(edit, _EM_REPLACESEL, 1, insert_text)
        after = _get_control_text(edit)
        verified = after is not None and text in after.replace("\r\n", "\n")
        if before is not None and after == before:
            verified = False
        if not verified:
            return _result("type_text", False, "I sent the text but could not confirm it landed correctly.", window=title)
        return _result("type_text", True, f"Typed {len(text)} characters into {title}.", window=title, characters=len(text), verified=True)
    except (AttributeError, OSError) as exc:
        return _result("type_text", False, f"Windows could not type into {title}: {exc}", window=title, error=str(exc))


def find_files(query: str, directory: str) -> ToolResult:
    """Search for files and folders by name (case-insensitive substring match)."""
    if not isinstance(query, str) or not query.strip():
        return _result("find_files", False, "Please tell me what to search for.")
    if not _is_windows():
        return _result("find_files", False, "Desktop control is available only on Windows.")
    search_query = query.strip().lower()
    if not isinstance(directory, str) or not directory.strip():
        search_dir = Path.home()
    else:
        try:
            search_dir = validate_path(directory)
        except (OSError, ValueError):
            return _result("find_files", False, f"I couldn't find the folder {directory}.")
    if not search_dir.is_dir():
        return _result("find_files", False, f"{search_dir.name} is not a folder.")
    matches: list[Path] = []
    cap = 25
    try:
        for root, dirs, files in os.walk(search_dir):
            try:
                entries = dirs + files
            except Exception:
                continue
            for entry in entries:
                if search_query in entry.lower():
                    matches.append(Path(root) / entry)
                    if len(matches) >= cap:
                        break
            if len(matches) >= cap:
                break
    except (OSError, PermissionError):
        pass
    if not matches:
        return _result(
            "find_files", False,
            f"I couldn't find anything matching '{query}' in {search_dir.name or search_dir.drive}.",
        )
    matches.sort()
    display_limit = 10
    lines = [str(m) for m in matches[:display_limit]]
    msg_parts = [f"Found {'at least ' if len(matches) >= cap else ''}{len(matches)} match{'es' if len(matches) != 1 else ''}:"]
    msg_parts.extend(lines)
    if len(matches) > display_limit:
        msg_parts.append(f"+{len(matches) - display_limit} more.")
    return _result(
        "find_files", True, "\n".join(msg_parts),
        count=len(matches),
        matches=[m.name for m in matches],
    )


def create_folder(path: str) -> ToolResult:
    """Create a single new folder. Does not create intermediate directories."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return _result("create_folder", False, "The folder path is empty or invalid.")
    if not _is_windows():
        return _result("create_folder", False, "Desktop control is available only on Windows.")
    requested = Path(path).expanduser()
    folder_name = requested.name
    if not folder_name or folder_name in (".", ".."):
        return _result("create_folder", False, "The folder name is invalid.")
    if "/" in folder_name or "\\" in folder_name or "\x00" in folder_name:
        return _result("create_folder", False, "The folder name contains invalid characters.")
    try:
        parent = validate_path(str(requested.parent))
    except (OSError, ValueError):
        return _result("create_folder", False, f"The parent folder does not exist.")
    target = parent / folder_name
    if target.exists():
        return _result(
            "create_folder", False,
            f"A file or folder named {folder_name} already exists in {parent.name or parent.drive}.",
        )
    try:
        target.mkdir(parents=False)
    except OSError as exc:
        return _result("create_folder", False, f"Windows could not create the folder {folder_name}.")
    if target.is_dir():
        return _result(
            "create_folder", True,
            f"Created folder {folder_name} in {parent.name or parent.drive}.",
            kind="folder", verified=True,
        )
    return _result("create_folder", False, f"The folder {folder_name} could not be verified after creation.")


def _validate_destination(source: Path, destination_str: str, tool: str) -> tuple[Path, ToolResult | None]:
    """Resolve a destination path for move/copy, returning (final_path, error_or_None).

    If destination is an existing folder the item keeps its original name inside it.
    Otherwise destination is treated as the full new path whose parent must exist.
    """
    try:
        dest = validate_path(destination_str)
    except (OSError, ValueError):
        # destination does not exist yet — treat as a new name under its parent
        dest_path = Path(destination_str).expanduser()
        dest_name = dest_path.name
        if not dest_name or dest_name in (".", "..") or "\x00" in dest_name or "/" in dest_name or "\\" in dest_name:
            return Path(), _result(tool, False, "The destination name is invalid.")
        try:
            dest_parent = validate_path(str(dest_path.parent))
        except (OSError, ValueError):
            return Path(), _result(tool, False, "The destination folder does not exist.")
        return dest_parent / dest_name, None

    # destination exists
    if dest.is_dir():
        return dest / source.name, None
    # destination exists as a file — would overwrite
    return dest, None


def move_path(source: str, destination: str) -> ToolResult:
    """Move a file or folder. Never overwrites an existing item."""
    try:
        src = validate_path(source)
    except (OSError, ValueError):
        return _result("move_path", False, "I couldn't find the source file or folder.")
    if not _is_windows():
        return _result("move_path", False, "Desktop control is available only on Windows.")
    final_dest, err = _validate_destination(src, destination, "move_path")
    if err is not None:
        return err
    if src == final_dest:
        return _result("move_path", False, f"{src.name} is already in that location.")
    if src.is_dir() and src in final_dest.parents:
        return _result("move_path", False, "I can't move a folder into itself.")
    if final_dest.exists():
        return _result(
            "move_path", False,
            f"A file or folder named {final_dest.name} already exists in {final_dest.parent.name or final_dest.parent.drive}; I didn't move anything.",
        )
    try:
        shutil.move(str(src), str(final_dest))
    except OSError:
        return _result("move_path", False, f"Windows could not move {src.name}.")
    if final_dest.exists() and not src.exists():
        kind = "folder" if final_dest.is_dir() else "file"
        return _result(
            "move_path", True,
            f"Moved {src.name} to {final_dest.parent.name or final_dest.parent.drive}.",
            kind=kind, verified=True,
        )
    return _result("move_path", False, f"The move of {src.name} could not be verified.")


def copy_path(source: str, destination: str) -> ToolResult:
    """Copy a file or folder. Never overwrites an existing item."""
    try:
        src = validate_path(source)
    except (OSError, ValueError):
        return _result("copy_path", False, "I couldn't find the source file or folder.")
    if not _is_windows():
        return _result("copy_path", False, "Desktop control is available only on Windows.")
    final_dest, err = _validate_destination(src, destination, "copy_path")
    if err is not None:
        return err
    if src.is_dir() and src in final_dest.parents:
        return _result("copy_path", False, "I can't copy a folder into itself.")
    if final_dest.exists():
        return _result(
            "copy_path", False,
            f"A file or folder named {final_dest.name} already exists in {final_dest.parent.name or final_dest.parent.drive}; I didn't copy anything.",
        )
    try:
        if src.is_dir():
            shutil.copytree(str(src), str(final_dest))
        else:
            shutil.copy2(str(src), str(final_dest))
    except OSError:
        return _result("copy_path", False, f"Windows could not copy {src.name}.")
    if final_dest.exists():
        kind = "folder" if final_dest.is_dir() else "file"
        return _result(
            "copy_path", True,
            f"Copied {src.name} to {final_dest.parent.name or final_dest.parent.drive}.",
            kind=kind, verified=True,
        )
    return _result("copy_path", False, f"The copy of {src.name} could not be verified.")


def rename_path(source: str, new_name: str) -> ToolResult:
    """Rename a file or folder in place. Never overwrites an existing item."""
    try:
        src = validate_path(source)
    except (OSError, ValueError):
        return _result("rename_path", False, "I couldn't find the file or folder to rename.")
    if not _is_windows():
        return _result("rename_path", False, "Desktop control is available only on Windows.")
    if not isinstance(new_name, str) or not new_name.strip():
        return _result("rename_path", False, "Please provide a new name.")
    clean_name = new_name.strip()
    if "/" in clean_name or "\\" in clean_name or "\x00" in clean_name or clean_name in (".", ".."):
        return _result("rename_path", False, "The new name must be a simple name without path separators.")
    dest = src.parent / clean_name
    if dest.exists():
        return _result(
            "rename_path", False,
            f"A file or folder named {clean_name} already exists in {src.parent.name or src.parent.drive}.",
        )
    try:
        src.rename(dest)
    except OSError:
        return _result("rename_path", False, f"Windows could not rename {src.name}.")
    if dest.exists():
        return _result(
            "rename_path", True,
            f"Renamed {src.name} to {clean_name}.",
            kind="folder" if dest.is_dir() else "file", verified=True,
        )
    return _result("rename_path", False, f"The rename of {src.name} could not be verified.")


def _send_to_recycle_bin(resolved: Path) -> tuple[bool, str]:
    """Ask the Windows shell to move a file or folder into the Recycle Bin."""
    source_buffer = ctypes.create_unicode_buffer(str(resolved) + "\x00")
    operation = _SHFILEOPSTRUCTW()
    operation.wFunc = _FO_DELETE
    operation.pFrom = ctypes.cast(source_buffer, ctypes.c_void_p)
    operation.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_NOERRORUI
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    except (AttributeError, OSError, TypeError):
        return False, f"Windows could not move {resolved.name} to the Recycle Bin."
    if result != 0 or operation.fAnyOperationsAborted:
        return False, f"Windows could not move {resolved.name} to the Recycle Bin."
    return True, ""


def delete_file(path: str) -> ToolResult:
    """Move an existing file or folder to the Windows Recycle Bin."""
    if not _is_windows():
        return _result("delete_file", False, "File deletion is available only on Windows.")
    try:
        resolved = validate_path(path)
    except (OSError, ValueError):
        return _result("delete_file", False, f"I couldn't find {path}.")
    if resolved.parent == resolved:
        return _result("delete_file", False, "I can't delete a whole drive.")
    if resolved == Path.home():
        return _result("delete_file", False, "I can't delete your home folder.")
    if resolved == Path(os.environ.get("SystemRoot", r"C:\Windows")):
        return _result("delete_file", False, "I can't delete the Windows folder.")

    is_directory = resolved.is_dir()
    ok, message = _send_to_recycle_bin(resolved)
    if not ok:
        return _result("delete_file", False, message)
    if resolved.exists():
        return _result("delete_file", False, f"Windows reported success but {resolved.name} is still there.")
    return _result(
        "delete_file", True, f"Moved {resolved.name} to the Recycle Bin.",
        name=resolved.name, is_directory=is_directory, verified=True,
    )


def close_application(name: str) -> ToolResult:
    """Close an application window safely with honest status reporting."""
    if not isinstance(name, str) or not name.strip():
        return _result("close_application", False, "Please provide the name of the application to close.")
    app = find_application(name)
    if not app:
        return _result("close_application", False, f"I couldn't find an installed application named {name}.")
    if not _is_windows():
        return _result("close_application", False, "Desktop control is available only on Windows.")
    try:
        exe_name = Path(app.executable).name
        proc = subprocess.run(["taskkill", "/IM", exe_name], capture_output=True, text=True)
        if proc.returncode == 0:
            return _result("close_application", True, f"Closed {app.name}.", app=app.name)
        return _result("close_application", False, f"Windows could not close {app.name}.", error=proc.stderr.strip())
    except OSError as exc:
        return _result("close_application", False, f"Windows could not close {app.name}.", error_type=type(exc).__name__)


# Windows media virtual key codes — no extra dependency required.
_VK_VOLUME_MUTE = 0xAD
_VK_VOLUME_DOWN = 0xAE
_VK_VOLUME_UP = 0xAF

_VOLUME_ACTIONS: dict[str, int] = {
    "up": _VK_VOLUME_UP,
    "down": _VK_VOLUME_DOWN,
    "mute": _VK_VOLUME_MUTE,
}


def _screenshots_dir() -> Path:
    return Path(__file__).resolve().parent / "screenshots"


def take_screenshot(target: str) -> ToolResult:
    """Capture the primary display or foreground window as a local PNG."""
    if target not in ("screen", "active_window"):
        return _result("take_screenshot", False, "I can capture 'screen' or 'active_window'.")
    if not _is_windows():
        return _result("take_screenshot", False, "Screenshots are available only on Windows.")
    if ImageGrab is None:
        return _result("take_screenshot", False, "Screenshots need Pillow. Run install.bat to install it.")

    try:
        if target == "screen":
            image = ImageGrab.grab()
        else:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return _result("take_screenshot", False, "There is no active window to capture.")
            rect = _RECT()
            if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return _result("take_screenshot", False, "I couldn't get the active window's position.")
            if rect.right <= rect.left or rect.bottom <= rect.top:
                return _result("take_screenshot", False, "I couldn't get the active window's position.")
            image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))

        directory = _screenshots_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stem = datetime.datetime.now().strftime("screenshot_%Y%m%d_%H%M%S")
        path = directory / f"{stem}.png"
        suffix = 2
        while path.exists():
            path = directory / f"{stem}_{suffix}.png"
            suffix += 1
        image.save(path)
    except OSError:
        return _result("take_screenshot", False, "Windows could not save the screenshot.")

    if not path.exists():
        return _result("take_screenshot", False, "The screenshot file was not created.")
    captured = "screen" if target == "screen" else "active window"
    return _result(
        "take_screenshot", True,
        f"Saved a screenshot of the {captured} as {path.name}.",
        file_name=path.name, width=image.size[0], height=image.size[1], target=target,
    )


def click_at(x: int, y: int, button: str, clicks: int) -> ToolResult:
    """Move to a screen coordinate and click, verifying cursor placement."""
    if not _is_windows():
        return _result("click_at", False, "Mouse control is available only on Windows.")
    if button not in ("left", "right"):
        return _result("click_at", False, "I can click the 'left' or 'right' mouse button.")
    if clicks not in (1, 2):
        return _result("click_at", False, "I can click once or double-click.")
    if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int) or not isinstance(y, int):
        return _result("click_at", False, "Click coordinates must be whole numbers.")

    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    if not (0 <= x < width and 0 <= y < height):
        return _result("click_at", False, f"({x}, {y}) is outside the screen ({width}x{height}).")
    if not user32.SetCursorPos(x, y):
        return _result("click_at", False, "Windows could not move the mouse.")

    down_flag, up_flag = (
        (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP)
        if button == "left"
        else (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP)
    )
    time.sleep(0.05)
    for index in range(clicks):
        user32.mouse_event(down_flag, 0, 0, 0, 0)
        user32.mouse_event(up_flag, 0, 0, 0, 0)
        if index < clicks - 1:
            time.sleep(0.15)

    point = _POINT()
    user32.GetCursorPos(ctypes.byref(point))
    verified = point.x == x and point.y == y
    message = (
        f"Clicked the {button} mouse button at ({x}, {y})."
        if clicks == 1
        else f"Double-clicked the {button} mouse button at ({x}, {y})."
    )
    return _result("click_at", True, message, x=x, y=y, button=button, clicks=clicks, verified=verified)


def _diff_summary(before, after) -> tuple[bool, float, list | None]:
    """Summarize meaningful visual differences between two Pillow images."""
    if before.size != after.size:
        width = max(before.size[0], after.size[0])
        height = max(before.size[1], after.size[1])
        return True, 100.0, [0, 0, width, height]
    diff = ImageChops.difference(before.convert("RGB"), after.convert("RGB")).convert("L")
    mask = diff.point(lambda pixel: 255 if pixel > _DIFF_PIXEL_THRESHOLD else 0)
    changed_pixels = mask.histogram()[255]
    width, height = before.size
    region = mask.getbbox()
    return (
        changed_pixels >= _DIFF_MIN_CHANGED_PIXELS,
        round(100.0 * changed_pixels / (width * height), 2),
        list(region) if region else None,
    )


def click_and_verify(x: int, y: int, button: str, clicks: int) -> ToolResult:
    """Click once or twice, then report mechanical visual changes only."""
    if not _is_windows():
        return _result("click_and_verify", False, "Mouse control is available only on Windows.")
    if ImageGrab is None:
        return _result("click_and_verify", False, "Click verification needs Pillow. Run install.bat to install it.")
    try:
        before_image = ImageGrab.grab()
    except OSError:
        return _result("click_and_verify", False, "Windows could not capture the screen to verify the click.")
    foreground_before = foreground_window_title()

    click_result = click_at(x, y, button, clicks)
    if not click_result.ok:
        return click_result

    try:
        time.sleep(0.5)
        after_image = ImageGrab.grab()
    except OSError:
        return _result("click_and_verify", False, "Windows could not capture the screen to verify the click.")
    foreground_after = foreground_window_title()
    changed, percent, region = _diff_summary(before_image, after_image)
    if changed:
        message = (
            f"{click_result.message} The screen changed around "
            f"({region[0]}, {region[1]})-({region[2]}, {region[3]}), covering {percent}% of the screen."
        )
    else:
        message = f"{click_result.message} No visible screen change detected."
    if foreground_after != foreground_before:
        message += f" The active window is now '{foreground_after}'."
    return _result(
        "click_and_verify", True, message,
        x=x, y=y, button=button, clicks=clicks,
        cursor_verified=click_result.data.get("verified"),
        changed=changed, changed_percent=percent, change_region=region,
        foreground_before=foreground_before, foreground_after=foreground_after,
    )


def adjust_volume(action: str) -> ToolResult:
    """Adjust system volume using Windows media keys.

    action must be one of: 'up', 'down', 'mute'.
    'up' and 'down' send 5 media-key presses per call; 'mute' sends 1.
    """
    if not isinstance(action, str):
        return _result("adjust_volume", False, "Volume action must be a string ('up', 'down', or 'mute').")
    normalised = action.strip().lower()
    vk = _VOLUME_ACTIONS.get(normalised)
    if vk is None:
        return _result(
            "adjust_volume", False,
            f"I don't understand the volume action '{action}'. "
            "Please say 'up', 'down', or 'mute'.",
        )
    if not _is_windows():
        return _result("adjust_volume", False, "Volume control is available only on Windows.")
    presses = 1 if normalised == "mute" else 5
    try:
        user32 = ctypes.windll.user32
        for _ in range(presses):
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, _KEYUP, 0)
    except (AttributeError, OSError) as exc:
        return _result("adjust_volume", False, f"Windows could not change the volume: {exc}")
    messages = {
        "up":   "Volume increased.",
        "down": "Volume decreased.",
        "mute": "Volume muted (or unmuted).",
    }
    return _result("adjust_volume", True, messages[normalised], action=normalised, presses=presses)
