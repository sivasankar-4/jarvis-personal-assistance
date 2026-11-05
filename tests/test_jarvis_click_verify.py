"""Tests for mechanical screenshot-based click verification."""

import ctypes
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

import jarvis_policy
import jarvis_tools
import jarvis_windows


class ClickVerifyTests(unittest.TestCase):
    def setUp(self):
        self.position = [0, 0]
        self.user32 = MagicMock()
        self.user32.GetSystemMetrics.side_effect = lambda index: 1920 if index == 0 else 1080

        def set_cursor(x, y):
            self.position[:] = [x, y]
            return True

        def get_cursor(ptr):
            point = ctypes.cast(ptr, ctypes.POINTER(jarvis_windows._POINT)).contents
            point.x, point.y = self.position
            return True

        self.user32.SetCursorPos.side_effect = set_cursor
        self.user32.GetCursorPos.side_effect = get_cursor
        windll = MagicMock()
        windll.user32 = self.user32
        patcher = patch.object(jarvis_windows.ctypes, "windll", windll, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _image():
        return Image.new("RGB", (100, 80), (0, 0, 0))

    def _run(self, before, after, foreground=("Desktop", "Desktop"), **kwargs):
        grabber = MagicMock()
        grabber.grab.side_effect = [before, after]
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.ImageGrab", grabber), \
             patch("jarvis_windows.foreground_window_title", side_effect=foreground), \
             patch("jarvis_windows.time.sleep"):
            result = jarvis_windows.click_and_verify(100, 200, "left", 1, **kwargs)
        return result, grabber

    def test_identical_images_no_change(self):
        before = self._image()
        self.assertEqual(jarvis_windows._diff_summary(before, before.copy()), (False, 0.0, None))

    def test_drawn_rectangle_detected(self):
        before = self._image()
        after = before.copy()
        ImageDraw.Draw(after).rectangle((10, 10, 29, 24), fill="white")
        changed, percent, region = jarvis_windows._diff_summary(before, after)
        self.assertTrue(changed)
        self.assertEqual(region, [10, 10, 30, 25])
        self.assertEqual(percent, round(100 * 300 / 8000, 2))

    def test_sub_threshold_noise_ignored(self):
        before = self._image()
        after = before.copy()
        pixels = after.load()
        for index in range(500):
            pixels[index % 100, index // 100] = (10, 10, 10)
        self.assertEqual(jarvis_windows._diff_summary(before, after), (False, 0.0, None))

    def test_click_with_change(self):
        before = self._image()
        after = before.copy()
        ImageDraw.Draw(after).rectangle((10, 10, 29, 24), fill="white")
        result, _ = self._run(before, after)
        self.assertTrue(result.ok)
        self.assertTrue(result.data["changed"])
        self.assertEqual(result.data["change_region"], [10, 10, 30, 25])
        self.assertTrue(result.data["cursor_verified"])
        self.assertIn("The screen changed", result.message)

    def test_click_no_change(self):
        result, _ = self._run(self._image(), self._image())
        self.assertTrue(result.ok)
        self.assertFalse(result.data["changed"])
        self.assertIn("No visible screen change", result.message)

    def test_foreground_change_reported(self):
        result, _ = self._run(self._image(), self._image(), foreground=("Notepad", "Calculator"))
        self.assertTrue(result.ok)
        self.assertEqual(result.data["foreground_before"], "Notepad")
        self.assertEqual(result.data["foreground_after"], "Calculator")
        self.assertIn("Calculator", result.message)

    def test_failed_click_propagates(self):
        before = self._image()
        grabber = MagicMock()
        grabber.grab.return_value = before
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.ImageGrab", grabber), \
             patch("jarvis_windows.foreground_window_title", return_value="Desktop"), \
             patch("jarvis_windows.time.sleep"):
            result = jarvis_windows.click_and_verify(5000, 10, "left", 1)
        self.assertFalse(result.ok)
        self.assertIn("outside the screen", result.message)
        grabber.grab.assert_called_once_with()

    def test_non_windows_and_pillow_missing(self):
        with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=False):
            result = jarvis_windows.click_and_verify(1, 1, "left", 1)
        self.assertFalse(result.ok)
        self.assertIn("Windows", result.message)
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.ImageGrab", None):
            result = jarvis_windows.click_and_verify(1, 1, "left", 1)
        self.assertFalse(result.ok)
        self.assertIn("Pillow", result.message)

    def test_policy_moderate_and_dispatch(self):
        self.assertEqual(jarvis_policy.classify_risk("click_and_verify"), jarvis_policy.RiskLevel.MODERATE)
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows.click_and_verify", return_value=jarvis_windows.ToolResult(True, "stub")) as click:
            result = jarvis_tools.execute_tool(
                "click_and_verify", {"x": 1, "y": 2, "button": "left", "clicks": 1}
            )
        self.assertTrue(result.ok)
        click.assert_called_once_with(x=1, y=2, button="left", clicks=1)


if __name__ == "__main__":
    unittest.main()
