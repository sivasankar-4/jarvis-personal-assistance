"""Tests for deterministic UI Automation element lookup (TASK-027E)."""

import unittest
from unittest.mock import patch

import jarvis_uia
from jarvis_uia import _match_level, _normalise, _normalise_query, find_element_point


class FakeRect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeElementInfo:
    def __init__(self, name="", class_name="", rectangle=None, handle=None, automation_id=""):
        self.name = name
        self.class_name = class_name
        self.rectangle = rectangle or FakeRect(0, 0, 10, 10)
        self.handle = handle
        self.automation_id = automation_id


class FakeElement:
    def __init__(self, name="", class_name="", rectangle=None, handle=None, children=None):
        self.element_info = FakeElementInfo(
            name=name, class_name=class_name, rectangle=rectangle, handle=handle
        )
        self._children = children or []

    def children(self):
        return list(self._children)


class FakeDesktop:
    def __init__(self, windows=None):
        self._windows = windows or []

    def windows(self):
        return list(self._windows)

    def window(self, handle=None):
        for w in self._windows:
            if getattr(w.element_info, "handle", None) == handle:
                class WindowWrapper:
                    def wrapper_object(self_):
                        return w
                return WindowWrapper()
        class DefaultWrapper:
            def wrapper_object(self_):
                return None
        return DefaultWrapper()


class JarvisUiaTests(unittest.TestCase):
    def test_01_normalise_query(self):
        self.assertEqual(_normalise_query("the Start button"), "start")
        self.assertEqual(_normalise_query("the clock"), "clock")
        self.assertEqual(_normalise_query("the File Explorer icon"), "file explorer")

    def test_02_match_level(self):
        self.assertEqual(_match_level("start", "start"), 0)
        self.assertEqual(_match_level("file explorer pinned", "file explorer"), 1)
        self.assertEqual(_match_level("start", "start menu"), 2)
        self.assertIsNone(_match_level("start", "art"))

        real_clock_name = "Clock \u200e10:58 AM \u200e"
        norm_clock = _normalise(real_clock_name)
        self.assertEqual(_match_level(norm_clock, "clock"), 1)

    def test_03_fake_tray_from_real_dump(self):
        start_el = FakeElement(name="Start", rectangle=FakeRect(600, 1020, 656, 1080))
        clock_el = FakeElement(name="Clock \u200e10:58 AM \u200e", rectangle=FakeRect(1813, 1020, 1900, 1080))
        explorer_el = FakeElement(name="File Explorer pinned", rectangle=FakeRect(991, 1020, 1046, 1080))
        tray = FakeElement(
            name="",
            class_name="Shell_TrayWnd",
            handle=1001,
            children=[start_el, clock_el, explorer_el],
        )
        desktop = FakeDesktop(windows=[tray])

        with patch("jarvis_uia._get_desktop", return_value=desktop), \
             patch("jarvis_uia._foreground_window", return_value=None):
            self.assertEqual(find_element_point("Start", "screen"), (628, 1050))
            self.assertEqual(find_element_point("the Start button", "screen"), (628, 1050))
            self.assertEqual(find_element_point("clock", "screen"), (1856, 1050))
            self.assertEqual(find_element_point("the clock", "screen"), (1856, 1050))
            self.assertEqual(find_element_point("Explorer", "screen"), (1018, 1050))
            self.assertEqual(find_element_point("the File Explorer icon", "screen"), (1018, 1050))

    def test_04_search_in_tray_and_window_tray_wins(self):
        tray_search = FakeElement(name="Search", rectangle=FakeRect(700, 1020, 800, 1080))
        tray = FakeElement(
            name="",
            class_name="Shell_TrayWnd",
            handle=1001,
            children=[tray_search],
        )
        win_search = FakeElement(name="Search", rectangle=FakeRect(100, 100, 200, 200))
        window = FakeElement(
            name="App Window",
            class_name="AppWnd",
            handle=2002,
            children=[win_search],
        )
        desktop = FakeDesktop(windows=[tray, window])

        with patch("jarvis_uia._get_desktop", return_value=desktop), \
             patch("jarvis_uia._foreground_window", return_value=window):
            # Tray point: (750, 1050), Window point: (150, 150)
            point = find_element_point("Search", "screen")
            self.assertEqual(point, (750, 1050))

    def test_05_tie_and_edge_cases(self):
        # Tie: two "Save" elements at same best level under one scope root -> None
        save1 = FakeElement(name="Save", rectangle=FakeRect(10, 10, 30, 30))
        save2 = FakeElement(name="Save", rectangle=FakeRect(40, 40, 60, 60))
        tied_window = FakeElement(name="Editor", handle=3001, children=[save1, save2])
        desktop = FakeDesktop(windows=[tied_window])

        with patch("jarvis_uia._get_desktop", return_value=desktop), \
             patch("jarvis_uia._foreground_window", return_value=tied_window):
            self.assertIsNone(find_element_point("Save", "active_window"))
            self.assertIsNone(find_element_point("Save", "screen"))

        # Same name in tray AND window is NOT a tie (tray wins)
        tray_save = FakeElement(name="Save", rectangle=FakeRect(10, 10, 30, 30))
        tray = FakeElement(name="", class_name="Shell_TrayWnd", handle=1001, children=[tray_save])
        win_save = FakeElement(name="Save", rectangle=FakeRect(100, 100, 200, 200))
        single_save_window = FakeElement(name="Editor", handle=3002, children=[win_save])
        desktop2 = FakeDesktop(windows=[tray, single_save_window])

        with patch("jarvis_uia._get_desktop", return_value=desktop2), \
             patch("jarvis_uia._foreground_window", return_value=single_save_window):
            self.assertEqual(find_element_point("Save", "screen"), (20, 20))

        # No match -> None
        with patch("jarvis_uia._get_desktop", return_value=desktop2), \
             patch("jarvis_uia._foreground_window", return_value=single_save_window):
            self.assertIsNone(find_element_point("Print", "screen"))

        # Degenerate rect -> skipped
        degen_element = FakeElement(name="Settings", rectangle=FakeRect(10, 10, 10, 10))
        degen_window = FakeElement(name="Window", handle=3003, children=[degen_element])
        desktop3 = FakeDesktop(windows=[degen_window])

        with patch("jarvis_uia._get_desktop", return_value=desktop3), \
             patch("jarvis_uia._foreground_window", return_value=degen_window):
            self.assertIsNone(find_element_point("Settings", "screen"))

        # One degenerate rect + one valid rect -> valid rect chosen without tie
        valid_element = FakeElement(name="Settings", rectangle=FakeRect(20, 20, 40, 40))
        mixed_window = FakeElement(
            name="Window", handle=3004, children=[degen_element, valid_element]
        )
        desktop4 = FakeDesktop(windows=[mixed_window])
        with patch("jarvis_uia._get_desktop", return_value=desktop4), \
             patch("jarvis_uia._foreground_window", return_value=mixed_window):
            self.assertEqual(find_element_point("Settings", "screen"), (30, 30))

    def test_06_get_desktop_raising_returns_none(self):
        with patch("jarvis_uia._get_desktop", side_effect=RuntimeError("COM initialization failure")):
            self.assertIsNone(find_element_point("start", "screen"))
            self.assertIsNone(find_element_point("the Start button", "screen"))


if __name__ == "__main__":
    unittest.main()
