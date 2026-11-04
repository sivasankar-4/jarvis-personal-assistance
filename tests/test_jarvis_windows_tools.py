import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import jarvis_planner
import jarvis_tools
import jarvis_windows


class WindowsControlTests(unittest.TestCase):
    def test_application_discovery_result_launches_without_a_shell(self):
        app = jarvis_windows.AppInfo("Visual Studio Code", r"C:\Apps\Code.exe", "registry")
        process = MagicMock(pid=4321)
        with patch("jarvis_windows.discover_applications", return_value=[app]), patch(
            "jarvis_windows.subprocess.Popen", return_value=process
        ) as popen, patch("jarvis_windows.wait_for_window") as wait, patch("jarvis_windows.audit_action"):
            result = jarvis_windows.launch_application("VS Code")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["pid"], 4321)
        self.assertFalse(result.data["verified"])
        wait.assert_not_called()
        popen.assert_called_once_with([r"C:\Apps\Code.exe"], close_fds=True)

    def test_unknown_application_is_reported_honestly(self):
        with patch("jarvis_windows.discover_applications", return_value=[]), patch("jarvis_windows.audit_action"):
            result = jarvis_windows.launch_application("made up app")
        self.assertFalse(result.ok)
        self.assertIn("couldn't find", result.message)

    def test_vs_code_is_discovered_from_the_per_user_installation_location(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict("os.environ", {"LOCALAPPDATA": temp}, clear=False):
            executable = Path(temp) / "Programs" / "Microsoft VS Code" / "Code.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"test")
            with patch("jarvis_windows._app_paths_from_registry", return_value=[]), patch(
                "jarvis_windows.shutil.which", return_value=None
            ):
                app = jarvis_windows.find_application("VS Code")
        self.assertIsNotNone(app)
        self.assertEqual(app.executable, str(executable))
        self.assertEqual(app.source, "installed location")

    def test_missing_vs_code_executable_is_not_reported_as_discovered(self):
        with patch("jarvis_windows._app_paths_from_registry", return_value=[]), patch(
            "jarvis_windows._vs_code_locations", return_value=[Path(r"C:\Missing\Code.exe")]
        ), patch("jarvis_windows.shutil.which", return_value=None):
            self.assertIsNone(jarvis_windows.find_application("VS Code"))

    def test_launch_failure_is_distinct_from_app_not_found(self):
        app = jarvis_windows.AppInfo("Visual Studio Code", r"C:\Program Files\Microsoft VS Code\Code.exe", "installed location")
        with patch("jarvis_windows.find_application", return_value=app), patch(
            "jarvis_windows.subprocess.Popen", side_effect=OSError("blocked")
        ), patch("jarvis_windows.audit_action"), patch("builtins.print"):
            result = jarvis_windows.launch_application("VS Code")
        self.assertFalse(result.ok)
        self.assertIn("could not start", result.message)
        self.assertEqual(result.data["error_type"], "OSError")

    def test_launch_verification_observed_uses_real_window_pid(self):
        app = jarvis_windows.AppInfo("Notepad", r"C:\Windows\Notepad.exe", "registry")
        observed = jarvis_windows.ToolResult(
            True, "Found.", {"found": True, "process": "Notepad.exe", "pid": 9876, "foreground": True}
        )
        with patch("jarvis_windows.find_application", return_value=app), patch(
            "jarvis_windows.subprocess.Popen", return_value=MagicMock(pid=4321)
        ), patch("jarvis_windows.wait_for_window", return_value=observed) as wait, patch(
            "jarvis_windows.audit_action"
        ):
            result = jarvis_windows.launch_application("notepad")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Opened Notepad; window observed.")
        self.assertEqual(result.data["pid"], 9876)
        self.assertEqual(result.data["process"], "Notepad.exe")
        self.assertTrue(result.data["verified"])
        wait.assert_called_once_with("Notepad.exe", 5)

    def test_launch_verification_timeout_is_reported_without_claiming_confirmation(self):
        app = jarvis_windows.AppInfo("Chrome", r"C:\Apps\Chrome.exe", "registry")
        with patch("jarvis_windows.find_application", return_value=app), patch(
            "jarvis_windows.subprocess.Popen", return_value=MagicMock(pid=4321)
        ), patch(
            "jarvis_windows.wait_for_window",
            return_value=jarvis_windows.ToolResult(False, "Timed out.", {"found": False}),
        ), patch("jarvis_windows.audit_action"):
            result = jarvis_windows.launch_application("chrome")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Launched Chrome but could not confirm a window.")
        self.assertEqual(result.data, {"verified": False})

    def test_launch_verification_exception_is_safe(self):
        app = jarvis_windows.AppInfo("Paint", r"C:\Windows\mspaint.exe", "registry")
        with patch("jarvis_windows.find_application", return_value=app), patch(
            "jarvis_windows.subprocess.Popen", return_value=MagicMock(pid=4321)
        ), patch("jarvis_windows.wait_for_window", side_effect=RuntimeError("observer failed")), patch(
            "jarvis_windows.audit_action"
        ):
            result = jarvis_windows.launch_application("paint")
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"verified": False})

    def test_relative_path_cannot_escape_selected_base_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / "project"
            base.mkdir()
            (Path(temp) / "outside.txt").write_text("x", encoding="utf-8")
            with self.assertRaises(ValueError):
                jarvis_windows.validate_path("../outside.txt", base)

    def test_type_text_refuses_unexpected_focused_window(self):
        with patch("jarvis_windows.foreground_window_title", return_value="Untitled - Notepad"), patch(
            "jarvis_windows.audit_action"
        ):
            result = jarvis_windows.type_text("hello", "Chrome")
        self.assertFalse(result.ok)
        self.assertIn("did not type", result.message)

    def test_tool_registry_rejects_unknown_or_extra_arguments(self):
        self.assertFalse(jarvis_tools.execute_tool("arbitrary_shell", {}).ok)
        self.assertFalse(jarvis_tools.execute_tool("launch_application", {"name": "Notepad", "extra": "no"}).ok)

    def test_planner_uses_validated_folder_tool_and_cancel_is_safe(self):
        with patch("jarvis_planner.execute_tool", return_value=jarvis_windows.ToolResult(True, "Opened.")) as execute:
            result = jarvis_planner.plan_direct_desktop_request('open folder "C:\\Work"')
        self.assertTrue(result.ok)
        execute.assert_called_once_with("open_path", {"path": "C:\\Work"})
        self.assertEqual(jarvis_planner.plan_direct_desktop_request("cancel task"), "Cancelled the current desktop task.")

    def test_planner_treats_an_absolute_path_as_a_path_not_an_app(self):
        with patch("jarvis_planner.execute_tool", return_value=jarvis_windows.ToolResult(True, "Opened.")) as execute:
            jarvis_planner.plan_direct_desktop_request(r"open C:\Projects\Jarvis")
        execute.assert_called_once_with("open_path", {"path": r"C:\Projects\Jarvis"})


if __name__ == "__main__":
    unittest.main()
