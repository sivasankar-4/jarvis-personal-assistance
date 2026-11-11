"""Tests for EC-22 Store-app discovery and AppsFolder launch."""

import json
import unittest
from unittest.mock import MagicMock, call, patch

import jarvis_planner
import jarvis_windows
from jarvis_windows import AppInfo, ToolResult


class TestJarvisEC22(unittest.TestCase):
    # Parsing (pure)
    def test_01_parse_start_apps_array(self):
        raw_json = (
            "\ufeff"
            + json.dumps([
                {"Name": "WhatsApp", "AppID": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"},
                {"Name": "NoAppID"},
                {"Name": "ClassicApp", "AppID": r"C:\Program Files\Classic\app.exe"},
                "not-a-dict",
            ])
        )
        apps = jarvis_windows._parse_start_apps(raw_json)
        self.assertEqual(
            apps,
            [AppInfo("WhatsApp", r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App", "store")],
        )

    def test_02_parse_start_apps_single_object(self):
        raw_json = json.dumps({"Name": "WhatsApp", "AppID": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"})
        apps = jarvis_windows._parse_start_apps(raw_json)
        self.assertEqual(
            apps,
            [AppInfo("WhatsApp", r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App", "store")],
        )

    def test_03_parse_start_apps_garbage_and_empty(self):
        self.assertEqual(jarvis_windows._parse_start_apps("not json"), [])
        self.assertEqual(jarvis_windows._parse_start_apps(""), [])
        self.assertEqual(jarvis_windows._parse_start_apps("   "), [])

    # Enumeration
    @patch("subprocess.run")
    @patch("jarvis_windows._is_windows", return_value=False)
    def test_04_enumeration_non_windows(self, mock_is_windows, mock_run):
        apps = jarvis_windows._store_apps_from_start_apps()
        self.assertEqual(apps, [])
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch("jarvis_windows._is_windows", return_value=True)
    def test_05_enumeration_errors(self, mock_is_windows, mock_run):
        mock_run.side_effect = OSError("command failed")
        self.assertEqual(jarvis_windows._store_apps_from_start_apps(), [])

        mock_run.side_effect = None
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        self.assertEqual(jarvis_windows._store_apps_from_start_apps(), [])

    # Discovery + find
    @patch("shutil.which", return_value=None)
    @patch("jarvis_windows._vs_code_locations", return_value=[])
    @patch("jarvis_windows._app_paths_from_registry", return_value=[])
    @patch("jarvis_windows._store_apps_from_start_apps")
    def test_06_discovery_and_find(self, mock_store, mock_reg, mock_vscode, mock_which):
        whatsapp_info = AppInfo("WhatsApp", r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App", "store")
        mock_store.return_value = [whatsapp_info]

        app1 = jarvis_windows.find_application("WhatsApp")
        app2 = jarvis_windows.find_application("whatsapp")
        self.assertEqual(app1, whatsapp_info)
        self.assertEqual(app2, whatsapp_info)

    # Launch
    @patch("subprocess.Popen")
    @patch("jarvis_windows._shell_execute_open", return_value=42)
    @patch("jarvis_windows.audit_action")
    @patch("jarvis_windows.discover_applications")
    def test_07_launch_store_app_success(self, mock_discover, mock_audit, mock_shell_open, mock_popen):
        whatsapp_info = AppInfo("WhatsApp", r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App", "store")
        mock_discover.return_value = [whatsapp_info]

        res = jarvis_windows.launch_application("WhatsApp")
        self.assertTrue(res.ok)
        self.assertEqual(res.message, "Launched WhatsApp but could not confirm a window.")
        self.assertFalse(res.data.get("verified"))
        self.assertEqual(res.data.get("source"), "store")
        mock_shell_open.assert_called_once_with(r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App")
        mock_popen.assert_not_called()

    @patch("subprocess.Popen")
    @patch("jarvis_windows._shell_execute_open", return_value=2)
    @patch("jarvis_windows.audit_action")
    @patch("jarvis_windows.discover_applications")
    def test_08_launch_store_app_failure(self, mock_discover, mock_audit, mock_shell_open, mock_popen):
        whatsapp_info = AppInfo("WhatsApp", r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App", "store")
        mock_discover.return_value = [whatsapp_info]

        res = jarvis_windows.launch_application("WhatsApp")
        self.assertFalse(res.ok)
        self.assertEqual(res.message, "Windows could not start WhatsApp.")
        mock_popen.assert_not_called()

    # Classic guard
    @patch("jarvis_windows._shell_execute_open")
    @patch("subprocess.Popen")
    @patch("jarvis_windows.audit_action")
    @patch("jarvis_windows.discover_applications")
    def test_09_classic_guard(self, mock_discover, mock_audit, mock_popen, mock_shell_open):
        classic_info = AppInfo("Visual Studio Code", r"C:\Apps\Code.exe", "registry")
        mock_discover.return_value = [classic_info]
        proc_mock = MagicMock()
        proc_mock.pid = 4321
        mock_popen.return_value = proc_mock

        res = jarvis_windows.launch_application("Visual Studio Code")
        self.assertTrue(res.ok)
        self.assertEqual(res.data.get("pid"), 4321)
        mock_shell_open.assert_not_called()

    # Planner
    @patch("jarvis_planner.execute_tool")
    def test_10_planner_whatsapp_and_gmail(self, mock_exec):
        jarvis_planner.plan_direct_desktop_request("open whatsapp")
        mock_exec.assert_called_once_with("launch_application", {"name": "whatsapp"})

        mock_exec.reset_mock()
        res_gmail = jarvis_planner.plan_direct_desktop_request("open gmail")
        self.assertIsNone(res_gmail)
        mock_exec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
