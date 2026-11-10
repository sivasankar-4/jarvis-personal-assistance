"""Deterministic UI Automation element lookup (TASK-027E).

Returns the centre point of a uniquely matched named element, or None
when there is no confident match. Every failure degrades to None so the
caller can fall back to vision; this module never raises.
"""

import re
import time
import unicodedata

_MAX_DEPTH = 8
_TIME_BUDGET = 3.0
_TRAILING_HINTS = (" button", " icon")


def _normalise(text):
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Cc"))
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _normalise_query(description):
    query = _normalise(description)
    if query.startswith("the "):
        query = query[4:]
    for hint in _TRAILING_HINTS:
        if query.endswith(hint):
            query = query[: -len(hint)]
            break
    return query.strip()


def _match_level(name, query):
    """0 exact; 1 name contains query at a word boundary; 2 query contains name; else None."""
    if not name or not query:
        return None
    if name == query:
        return 0
    if re.search(r"(?<!\w)" + re.escape(query) + r"(?!\w)", name):
        return 1
    if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", query):
        return 2
    return None


def _get_desktop():
    from pywinauto import Desktop

    return Desktop(backend="uia")


def _screen_size():
    """(width, height) of the primary display in physical pixels, or None."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        width = int(user32.GetSystemMetrics(0))
        height = int(user32.GetSystemMetrics(1))
        if width > 0 and height > 0:
            return (width, height)
    except Exception:
        pass
    return None


def _foreground_window(desktop):
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        return desktop.window(handle=hwnd).wrapper_object()
    except Exception:
        return None


def _scope_roots(desktop, target):
    foreground = _foreground_window(desktop)
    if target == "active_window":
        return [foreground] if foreground is not None else []
    try:
        windows = desktop.windows()
    except Exception:
        windows = []
    tray = None
    for window in windows:
        try:
            if window.element_info.class_name == "Shell_TrayWnd":
                tray = window
                break
        except Exception:
            continue
    roots = []
    seen = set()
    for candidate in [tray, foreground] + list(windows):
        if candidate is None:
            continue
        try:
            handle = candidate.element_info.handle
        except Exception:
            handle = None
        if handle is not None:
            if handle in seen:
                continue
            seen.add(handle)
        roots.append(candidate)
    return roots


def _search_scope(root, query, deadline):
    """Centre point of the unique best match under root, else None."""
    size = _screen_size()
    best_level = None
    best_point = None
    tied = False
    stack = [(root, 0)]
    while stack:
        if time.monotonic() > deadline:
            return None
        element, depth = stack.pop()
        try:
            info = element.element_info
            name = _normalise(info.name or "")
            if name:
                level = _match_level(name, query)
                if level is not None:
                    rect = info.rectangle
                    if rect.right > rect.left and rect.bottom > rect.top:
                        point = ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                        if size is not None and not (0 <= point[0] < size[0] and 0 <= point[1] < size[1]):
                            pass  # off-screen (minimized/cloaked window): not a candidate
                        elif best_level is None or level < best_level:
                            best_level, best_point, tied = level, point, False
                        elif level == best_level:
                            tied = True
            if depth < _MAX_DEPTH:
                stack.extend((child, depth + 1) for child in element.children())
        except Exception:
            continue
    if best_point is not None and not tied:
        return best_point
    return None


def find_element_point(description, target):
    """(x, y) of the unique best UIA name match, or None. Never raises."""
    try:
        query = _normalise_query(description)
        if not query:
            return None
        desktop = _get_desktop()
        roots = _scope_roots(desktop, target)
        for _attempt in range(2):
            deadline = time.monotonic() + _TIME_BUDGET
            for root in roots:
                point = _search_scope(root, query, deadline)
                if point is not None:
                    return point
            if time.monotonic() <= deadline:
                break
        return None
    except Exception:
        return None
