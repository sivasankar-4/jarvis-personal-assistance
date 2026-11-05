"""Tests for the direct, coordinate-based Windows mouse click tool."""

import ctypes
import unittest
from unittest.mock import MagicMock, patch

import jarvis_policy
import jarvis_tools
import jarvis_windows


class MouseClickTests(unittest.TestCase):
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

    def _click(self, x=100, y=200, button="left", clicks=1):
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.time.sleep"):
            return jarvis_windows.click_at(x, y, button, clicks)

    def _flags(self):
        return [call.args[0] for call in self.user32.mouse_event.call_args_list]

    def test_left_single_click(self):
        result = self._click()
        self.assertTrue(result.ok)
        self.user32.SetCursorPos.assert_called_once_with(100, 200)
        self.assertEqual(self._flags(), [jarvis_windows._MOUSEEVENTF_LEFTDOWN, jarvis_windows._MOUSEEVENTF_LEFTUP])
        self.assertTrue(result.data["verified"])
        self.assertEqual(result.data["button"], "left")
        self.assertEqual(result.data["clicks"], 1)

    def test_double_click(self):
        result = self._click(clicks=2)
        self.assertTrue(result.ok)
        self.assertEqual(self._flags(), [
            jarvis_windows._MOUSEEVENTF_LEFTDOWN, jarvis_windows._MOUSEEVENTF_LEFTUP,
            jarvis_windows._MOUSEEVENTF_LEFTDOWN, jarvis_windows._MOUSEEVENTF_LEFTUP,
        ])

    def test_right_click_flags(self):
        result = self._click(button="right")
        self.assertTrue(result.ok)
        self.assertEqual(self._flags(), [jarvis_windows._MOUSEEVENTF_RIGHTDOWN, jarvis_windows._MOUSEEVENTF_RIGHTUP])

    def test_out_of_bounds_refused(self):
        for x, y in ((1920, 10), (-1, 10), (10, 1080)):
            with self.subTest(x=x, y=y):
                result = self._click(x=x, y=y)
                self.assertFalse(result.ok)
                self.assertIn("outside the screen", result.message)
        self.user32.mouse_event.assert_not_called()

    def test_bool_coordinate_refused(self):
        result = self._click(x=True, y=10)
        self.assertFalse(result.ok)
        self.assertIn("whole numbers", result.message)

    def test_unknown_button_refused(self):
        result = self._click(button="middle")
        self.assertFalse(result.ok)
        self.user32.mouse_event.assert_not_called()

    def test_clicks_three_refused(self):
        result = self._click(clicks=3)
        self.assertFalse(result.ok)
        self.user32.mouse_event.assert_not_called()

    def test_cursor_mismatch_reports_unverified(self):
        def mismatched_cursor(ptr):
            point = ctypes.cast(ptr, ctypes.POINTER(jarvis_windows._POINT)).contents
            point.x, point.y = 0, 0
            return True

        self.user32.GetCursorPos.side_effect = mismatched_cursor
        result = self._click()
        self.assertTrue(result.ok)
        self.assertFalse(result.data["verified"])

    def test_non_windows_refused(self):
        with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=False):
            result = jarvis_windows.click_at(100, 200, "left", 1)
        self.assertFalse(result.ok)
        self.assertIn("Windows", result.message)

    def test_policy_moderate_and_dispatch(self):
        self.assertEqual(jarvis_policy.classify_risk("click_at"), jarvis_policy.RiskLevel.MODERATE)
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows.click_at", return_value=jarvis_windows.ToolResult(True, "stub")) as click:
            result = jarvis_tools.execute_tool(
                "click_at", {"x": 1, "y": 2, "button": "left", "clicks": 1}
            )
        self.assertTrue(result.ok)
        click.assert_called_once_with(x=1, y=2, button="left", clicks=1)


if __name__ == "__main__":
    unittest.main()
