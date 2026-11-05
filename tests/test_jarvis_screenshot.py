"""Tests for the read-only Windows screenshot tool."""

import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import jarvis_policy
import jarvis_tools
import jarvis_windows


class _FakeImage:
    size = (1920, 1080)

    def __init__(self):
        self.saved_path = None

    def save(self, path):
        self.saved_path = Path(path)
        self.saved_path.touch()


class ScreenshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def _capture(self, target, image=None):
        image = image or _FakeImage()
        grabber = MagicMock()
        grabber.grab.return_value = image
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.ImageGrab", grabber), \
             patch("jarvis_windows._screenshots_dir", return_value=self.directory):
            result = jarvis_windows.take_screenshot(target)
        return result, grabber, image

    def test_screen_capture_success(self):
        result, grabber, _ = self._capture("screen")
        self.assertTrue(result.ok)
        grabber.grab.assert_called_once_with()
        self.assertIn(result.data["file_name"], result.message)
        self.assertEqual(result.data["width"], 1920)
        self.assertEqual(result.data["height"], 1080)
        self.assertEqual(result.data["target"], "screen")
        self.assertFalse(any("path" in key.lower() for key in result.data))

    def test_active_window_capture_uses_rect(self):
        image = _FakeImage()
        grabber = MagicMock()
        grabber.grab.return_value = image
        windll = MagicMock()
        windll.user32.GetForegroundWindow.return_value = 1234

        def get_rect(_hwnd, rect_ptr):
            rect = rect_ptr._obj
            rect.left, rect.top, rect.right, rect.bottom = 10, 20, 810, 620
            return True

        windll.user32.GetWindowRect.side_effect = get_rect
        with patch.object(jarvis_windows.ctypes, "windll", windll, create=True), \
             patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.ImageGrab", grabber), \
             patch("jarvis_windows._screenshots_dir", return_value=self.directory):
            result = jarvis_windows.take_screenshot("active_window")
        self.assertTrue(result.ok)
        grabber.grab.assert_called_once_with(bbox=(10, 20, 810, 620))

    def test_active_window_none_fails(self):
        windll = MagicMock()
        windll.user32.GetForegroundWindow.return_value = 0
        with patch.object(jarvis_windows.ctypes, "windll", windll, create=True), \
             patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.ImageGrab", MagicMock()):
            result = jarvis_windows.take_screenshot("active_window")
        self.assertFalse(result.ok)
        self.assertIn("no active window", result.message.lower())

    def test_unknown_target_fails(self):
        result, _, _ = self._capture("desktop")
        self.assertFalse(result.ok)

    def test_non_windows_refused(self):
        with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=False):
            result = jarvis_windows.take_screenshot("screen")
        self.assertFalse(result.ok)
        self.assertIn("Windows", result.message)

    def test_pillow_missing_fails_honestly(self):
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.ImageGrab", None):
            result = jarvis_windows.take_screenshot("screen")
        self.assertFalse(result.ok)
        self.assertIn("Pillow", result.message)

    def test_filename_collision_gets_suffix(self):
        frozen = datetime.datetime(2026, 11, 5, 12, 30, 0)
        original = self.directory / "screenshot_20261105_123000.png"
        original.write_bytes(b"existing")

        class _FrozenDatetime:
            @classmethod
            def now(cls):
                return frozen

        image = _FakeImage()
        grabber = MagicMock()
        grabber.grab.return_value = image
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.ImageGrab", grabber), \
             patch("jarvis_windows._screenshots_dir", return_value=self.directory), \
             patch("jarvis_windows.datetime.datetime", _FrozenDatetime):
            result = jarvis_windows.take_screenshot("screen")
        self.assertTrue(result.ok)
        self.assertTrue(result.data["file_name"].endswith("_2.png"))
        self.assertEqual(original.read_bytes(), b"existing")

    def test_policy_low_and_registered(self):
        self.assertEqual(jarvis_policy.classify_risk("take_screenshot"), jarvis_policy.RiskLevel.LOW)
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows.take_screenshot", return_value=jarvis_windows.ToolResult(True, "stub")) as capture:
            result = jarvis_tools.execute_tool("take_screenshot", {"target": "screen"})
        self.assertTrue(result.ok)
        capture.assert_called_once_with(target="screen")


if __name__ == "__main__":
    unittest.main()
