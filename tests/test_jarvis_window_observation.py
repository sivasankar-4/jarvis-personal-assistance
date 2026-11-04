import unittest
from unittest.mock import MagicMock, patch

import jarvis_policy
import jarvis_tools
import jarvis_windows


class WindowObservationTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)

    def test_observe_windows_filters_cloaked_untitled_and_invisible(self):
        user32 = MagicMock()
        user32.GetForegroundWindow.return_value = 1
        user32.IsWindowVisible.side_effect = {1: True, 2: True, 3: False, 4: True}.__getitem__
        user32.EnumWindows.side_effect = lambda callback, _param: [
            callback(hwnd, 0) for hwnd in (1, 2, 3, 4)
        ]
        with patch("jarvis_windows._is_windows", return_value=True), patch(
            "jarvis_windows.ctypes.windll.user32", user32
        ), patch("jarvis_windows._window_is_cloaked", side_effect={1: False, 2: True, 3: False, 4: False}.__getitem__), patch(
            "jarvis_windows._window_text", side_effect={1: "One", 2: "Two", 3: "Three", 4: ""}.__getitem__
        ), patch("jarvis_windows._window_process_id", return_value=10), patch(
            "jarvis_windows._packaged_window_process", return_value=(10, "one.exe")
        ), patch("jarvis_windows._window_class_name", return_value="Window"), patch(
            "jarvis_windows.audit_action"
        ):
            result = jarvis_windows.observe_windows()
        self.assertEqual([window.hwnd for window in result], [1])
        self.assertEqual(result[0].title, "One")
        self.assertTrue(result[0].is_foreground)

    def test_application_frame_resolves_core_window_and_falls_back(self):
        user32 = MagicMock()
        user32.EnumChildWindows.side_effect = lambda hwnd, callback, param: callback(20, param)
        with patch("jarvis_windows.ctypes.windll.user32", user32), patch(
            "jarvis_windows._window_class_name", side_effect=lambda hwnd: {
                10: "ApplicationFrameWindow",
                20: "Windows.UI.Core.CoreWindow",
            }[hwnd]
        ), patch("jarvis_windows._window_process_id", side_effect=lambda hwnd: {20: 22}[hwnd]), patch(
            "jarvis_windows._process_name", side_effect=lambda pid: {11: "host.exe", 22: "app.exe"}[pid]
        ):
            self.assertEqual(jarvis_windows._packaged_window_process(10, 11), (22, "app.exe"))

        with patch("jarvis_windows._window_class_name", return_value="ApplicationFrameWindow"), patch(
            "jarvis_windows._window_process_id", return_value=11
        ), patch("jarvis_windows._process_name", return_value="host.exe"), patch(
            "jarvis_windows.ctypes.windll.user32.EnumChildWindows", side_effect=OSError
        ):
            self.assertEqual(jarvis_windows._packaged_window_process(10, 11), (11, "host.exe"))

    def test_process_name_lookup_failure_returns_empty_and_closes_handle(self):
        kernel32 = MagicMock()
        kernel32.OpenProcess.return_value = 42
        kernel32.QueryFullProcessImageNameW.return_value = 0
        with patch("jarvis_windows.ctypes.windll.kernel32", kernel32):
            self.assertEqual(jarvis_windows._process_name(123), "")
        kernel32.CloseHandle.assert_called_once_with(42)

    def test_list_windows_does_not_expose_titles(self):
        windows = [
            jarvis_windows.WindowInfo(1, 10, "z.exe", "C", "Secret title", False),
            jarvis_windows.WindowInfo(2, 20, "a.exe", "C", "Another title", True),
        ]
        with patch("jarvis_windows._is_windows", return_value=True), patch(
            "jarvis_windows.observe_windows", return_value=windows
        ), patch("jarvis_windows.audit_action"):
            result = jarvis_windows.list_windows()
        self.assertEqual(result.data, {"count": 2, "processes": ["a.exe", "z.exe"]})
        self.assertNotIn("title", result.message.lower())
        self.assertNotIn("Secret title", str(result))

    def test_wait_for_window_found_and_exe_tolerance(self):
        window = jarvis_windows.WindowInfo(7, 42, "Demo.EXE", "C", "Private", True)
        with patch("jarvis_windows._is_windows", return_value=True), patch(
            "jarvis_windows.observe_windows", return_value=[window]
        ), patch("jarvis_windows.audit_action"):
            result = jarvis_windows.wait_for_window("demo", 3)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["pid"], 42)
        self.assertTrue(result.data["foreground"])
        self.assertNotIn("Private", result.message)

    def test_wait_for_window_timeout_clamps_and_returns_safe_data(self):
        with patch("jarvis_windows._is_windows", return_value=True), patch(
            "jarvis_windows.observe_windows", return_value=[]
        ), patch("jarvis_windows.time.monotonic", side_effect=[0, 0, 1, 15]), patch(
            "jarvis_windows.time.sleep"
        ) as sleep, patch("jarvis_windows.audit_action"):
            result = jarvis_windows.wait_for_window("missing.exe", 99)
        self.assertFalse(result.ok)
        self.assertEqual(result.data["pid"], None)
        self.assertFalse(result.data["found"])
        sleep.assert_called_with(0.25)

        with patch("jarvis_windows._is_windows", return_value=True), patch(
            "jarvis_windows.observe_windows", return_value=[]
        ), patch("jarvis_windows.time.monotonic", side_effect=[0, 1]), patch(
            "jarvis_windows.time.sleep"
        ), patch("jarvis_windows.audit_action"):
            jarvis_windows.wait_for_window("missing", 0)

    def test_tools_are_low_risk_and_execute_without_approval(self):
        self.assertEqual(jarvis_policy.classify_risk("list_windows"), jarvis_policy.RiskLevel.LOW)
        self.assertEqual(jarvis_policy.classify_risk("wait_for_window"), jarvis_policy.RiskLevel.LOW)
        with patch("jarvis_windows.list_windows", return_value=jarvis_windows.ToolResult(True, "ok")), patch(
            "jarvis_windows.wait_for_window", return_value=jarvis_windows.ToolResult(True, "ok")
        ), patch("jarvis_windows.audit_action"):
            self.assertTrue(jarvis_tools.execute_tool("list_windows", {}).ok)
            self.assertTrue(
                jarvis_tools.execute_tool(
                    "wait_for_window", {"process_name": "demo", "timeout_seconds": 2}
                ).ok
            )

    def test_observation_tool_arguments_are_exact(self):
        self.assertFalse(jarvis_tools.execute_tool("list_windows", {"extra": True}).ok)
        self.assertFalse(
            jarvis_tools.execute_tool("wait_for_window", {"process_name": "demo"}).ok
        )


if __name__ == "__main__":
    unittest.main()
