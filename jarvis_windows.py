"""Small, Windows-native desktop control primitives for Jarvis.

This module does not interpret user language or call an AI model.  It validates
parameters, asks Windows to perform narrowly defined actions, and returns an
honest result that callers can report or audit.
"""

from __future__ import annotations

import ctypes
import json
import ntpath
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


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
}
_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004


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


def _is_windows() -> bool:
    return os.name == "nt"


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


def open_path(target: str, base_directory: Path | str | None = None) -> ToolResult:
    try:
        path = validate_path(target, base_directory)
    except (OSError, ValueError):
        return _result("open_path", False, "I couldn't find that file or folder.")
    if not _is_windows():
        return _result("open_path", False, "Desktop control is available only on Windows.")
    try:
        if path.is_dir():
            process = subprocess.Popen(["explorer.exe", str(path)], close_fds=True)
            return _result("open_path", True, f"Started File Explorer for {path.name or path.drive}.", pid=process.pid, kind="folder")
        os.startfile(str(path))
        return _result("open_path", True, f"Asked Windows to open {path.name} with its default application.", kind="file")
    except OSError:
        return _result("open_path", False, f"Windows could not open {path.name}.")


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
        typed_count = 0
        for character in text:
            down = _Input(type=1, ki=_KeyBdInput(0, ord(character), _KEYEVENTF_UNICODE, 0, 0))
            up = _Input(type=1, ki=_KeyBdInput(0, ord(character), _KEYEVENTF_UNICODE | _KEYUP, 0, 0))
            if ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_Input)) != 1:
                err = ctypes.GetLastError()
                raise OSError(f"SendInput key down failed (error code {err})")
            if ctypes.windll.user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_Input)) != 1:
                err = ctypes.GetLastError()
                raise OSError(f"SendInput key up failed (error code {err})")
            typed_count += 1
        return _result("type_text", True, f"Typed {typed_count} characters into {title}.", window=title, characters=typed_count)
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
