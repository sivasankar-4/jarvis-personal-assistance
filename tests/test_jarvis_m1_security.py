"""Comprehensive security and regression tests for Jarvis M1 milestone.

Covers:
- Live Failure 1: Complete pending approval lifecycle, exact argument enforcement,
  tampering rejection, repeat action guidance, single consumption.
- Live Failure 2: Notepad typing target validation, title variations, missing/wrong window
  blocks, 64-bit SendInput struct accuracy, and diagnostic reporting.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import jarvis_actions
import jarvis_brain
import jarvis_planner
import jarvis_policy
import jarvis_tools
import jarvis_windows


class JarvisM1SecurityTests(unittest.TestCase):
    def setUp(self):
        jarvis_brain._history = []
        jarvis_brain._client = None
        jarvis_brain._cooldown_until = 0.0
        jarvis_policy.clear_pending_approval()

    def tearDown(self):
        jarvis_brain._history = []
        jarvis_brain._client = None
        jarvis_policy.clear_pending_approval()

    # ---------------------------------------------------------
    # Baseline Action Routing and Policy Tests
    # ---------------------------------------------------------

    def test_1_legacy_action_router_cannot_bypass_registry(self):
        """Verify that legacy action router delegates side effects to the registry."""
        with patch("jarvis_tools.execute_tool", return_value=jarvis_windows.ToolResult(True, "Started Notepad.")) as mock_exec:
            reply = jarvis_actions.try_action("open notepad")
        self.assertEqual(reply, "Started Notepad.")
        mock_exec.assert_called_once_with("launch_application", {"name": "notepad"})

        # Verify web site open routes through registry
        with patch("jarvis_tools.execute_tool", return_value=jarvis_windows.ToolResult(True, "Accepted.")) as mock_exec:
            reply = jarvis_actions.try_action("open github")
        self.assertIn("Opened GitHub", reply)
        mock_exec.assert_called_once_with("open_website", {"url": "https://github.com"})

    def test_2_low_risk_action_executes_without_approval(self):
        """Low-risk action executes autonomously without requiring approval."""
        with patch("jarvis_windows.launch_application", return_value=jarvis_windows.ToolResult(True, "Started Notepad.", {"pid": 1234})):
            result = jarvis_tools.execute_tool("launch_application", {"name": "Notepad"})
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Started Notepad.")
        self.assertFalse(jarvis_policy.has_pending_approval())

    def test_3_high_risk_action_cannot_execute_without_approval(self):
        """High-risk action cannot execute without explicit prior approval."""
        result = jarvis_tools.execute_tool("close_application", {"name": "Notepad"}, approved=False)
        self.assertFalse(result.ok)
        self.assertIn("requires your confirmation", result.message)
        self.assertTrue(result.data.get("requires_approval"))
        self.assertEqual(result.data.get("risk_level"), "HIGH")
        self.assertTrue(jarvis_policy.has_pending_approval())

        # Test high-risk shortcut
        result_sc = jarvis_tools.execute_tool("send_shortcut", {"shortcut": "alt+f4"}, approved=False)
        self.assertFalse(result_sc.ok)
        self.assertTrue(result_sc.data.get("requires_approval"))
        self.assertEqual(result_sc.data.get("risk_level"), "HIGH")

    def test_4_rejected_approval_prevents_execution(self):
        """When approval is rejected by the user, action does not execute and is cancelled."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"}, "close Notepad")
        with patch("jarvis_tools.execute_tool") as mock_exec:
            reply = jarvis_brain.think("no, cancel that")
        self.assertEqual(reply, "Okay, I cancelled that action.")
        self.assertFalse(jarvis_policy.has_pending_approval())
        mock_exec.assert_not_called()

    def test_5_approval_for_one_set_of_arguments_cannot_authorize_modified_arguments(self):
        """Approval granted for one set of arguments fails if arguments are altered."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"})
        consumed = jarvis_policy.consume_approval("close_application", {"name": "Calculator"})
        self.assertFalse(consumed)
        self.assertFalse(jarvis_policy.has_pending_approval())

    def test_6_unknown_tools_are_rejected(self):
        """Registry strictly rejects unknown tool requests."""
        result = jarvis_tools.execute_tool("arbitrary_system_command", {"command": "dir"})
        self.assertFalse(result.ok)
        self.assertIn("not available", result.message)

    def test_7_malformed_arguments_are_rejected(self):
        """Registry rejects malformed JSON, wrong types, missing args, and extra args."""
        r1 = jarvis_tools.execute_tool("launch_application", "{malformed json")
        self.assertFalse(r1.ok)
        self.assertIn("malformed", r1.message)

        r2 = jarvis_tools.execute_tool("launch_application", {"name": 12345})
        self.assertFalse(r2.ok)
        self.assertIn("did not pass validation", r2.message)

        r3 = jarvis_tools.execute_tool("launch_application", {"name": "Notepad", "inject_command": "rm -rf"})
        self.assertFalse(r3.ok)
        self.assertIn("did not pass validation", r3.message)

        r4 = jarvis_tools.execute_tool("type_text", {"text": "hello"})
        self.assertFalse(r4.ok)
        self.assertIn("did not pass validation", r4.message)

    def test_8_tool_execution_failure_is_not_reported_as_success(self):
        """Failures in underlying primitives are faithfully reported with ok=False."""
        with patch("jarvis_windows.launch_application", return_value=jarvis_windows.ToolResult(False, "Windows could not start Notepad.")):
            result = jarvis_tools.execute_tool("launch_application", {"name": "Notepad"})
        self.assertFalse(result.ok)
        self.assertIn("could not start", result.message)

    def test_9_fabricated_email_results_are_eliminated(self):
        """Mail briefing never fabricates fake unread emails."""
        with patch("os.path.exists", return_value=False):
            brief = jarvis_actions.get_mail_brief()
        self.assertNotIn("3 unread priority emails", brief)
        self.assertIn("not supported yet", brief)

    def test_10_unsupported_whatsapp_and_windows_volume_actions_reported_honestly(self):
        """Unsupported WhatsApp sending and volume control return explicit honest messages."""
        wa_reply = jarvis_actions.try_action("send whatsapp to Alice saying hello")
        self.assertNotIn("Opening WhatsApp to message Alice", wa_reply)
        self.assertIn("not supported", wa_reply)

        self.assertEqual(jarvis_actions.try_action("volume up"), "Windows volume control is not supported yet.")
        self.assertEqual(jarvis_actions.try_action("volume down"), "Windows volume control is not supported yet.")
        self.assertEqual(jarvis_actions.try_action("mute"), "Windows volume control is not supported yet.")

    def test_11_existing_safe_application_launching_still_works(self):
        """Normal safe application launching through brain works via validated tools."""
        app = jarvis_windows.AppInfo("Notepad", r"C:\Windows\System32\notepad.exe", "PATH")
        mock_proc = MagicMock(pid=9999)
        with patch("jarvis_windows.discover_applications", return_value=[app]), patch(
            "jarvis_windows.subprocess.Popen", return_value=mock_proc
        ), patch(
            "jarvis_windows.wait_for_window",
            return_value=jarvis_windows.ToolResult(True, "Found.", {"pid": 9998, "process": "Notepad.exe"}),
        ):
            response = jarvis_brain.think("open notepad")
        self.assertEqual(response, "Opened Notepad; window observed.")

    def test_12_normal_conversational_responses_still_work(self):
        """General conversational requests bypass tools and route to AI brain response."""
        class _FakeCompletions:
            def create(self, **kwargs):
                message = type("Msg", (), {"content": "An algorithm is a step-by-step procedure.", "tool_calls": None})()
                choice = type("Choice", (), {"message": message})()
                return type("Resp", (), {"choices": [choice]})()

        jarvis_brain._client = type("Client", (), {"chat": type("Chat", (), {"completions": _FakeCompletions()})()})()
        with patch("jarvis_brain._get_api_key", return_value="test-key"), patch(
            "jarvis_brain._relevant_memory_context", return_value=""
        ):
            reply = jarvis_brain.think("What is an algorithm?")
        self.assertEqual(reply, "An algorithm is a step-by-step procedure.")

    def test_13_audit_logging_structure_and_sanitization(self):
        """Audit logging records all required fields and omits sensitive private data."""
        with tempfile.TemporaryDirectory() as temp_dir:
            log_file = os.path.join(temp_dir, "test_audit.jsonl")
            with patch.dict("os.environ", {"JARVIS_AUDIT_LOG": log_file}):
                res = jarvis_windows.ToolResult(
                    True,
                    "Launched app",
                    {"pid": 1234, "password": "secret_password", "token": "xyz", "safe_param": "ok_value"},
                )
                jarvis_windows.audit_action("launch_application", res, risk_level="LOW", approval_status="AUTONOMOUS")

            with open(log_file, "r", encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertIn("timestamp", entry)
            self.assertEqual(entry["tool"], "launch_application")
            self.assertEqual(entry["risk_classification"], "LOW")
            self.assertEqual(entry["approval_status"], "AUTONOMOUS")
            self.assertEqual(entry["execution_status"], "SUCCESS")
            self.assertNotIn("password", entry["metadata"])
            self.assertNotIn("token", entry["metadata"])
            self.assertEqual(entry["metadata"]["pid"], 1234)
            self.assertEqual(entry["metadata"]["safe_param"], "ok_value")

    # ---------------------------------------------------------
    # Regression Tests: Failure 1 (Approval Lifecycle)
    # ---------------------------------------------------------

    def test_approval_exact_yes_executes_pending_action(self):
        """Exact 'yes' executes the pending action."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"}, "close Notepad")
        with patch("jarvis_windows.close_application", return_value=jarvis_windows.ToolResult(True, "Closed Notepad.")):
            reply = jarvis_brain.think("yes")
        self.assertEqual(reply, "Closed Notepad.")
        self.assertFalse(jarvis_policy.has_pending_approval())

    def test_approval_yes_close_the_notepad_application_executes_exact_action(self):
        """'Yes, close the Notepad application' executes the exact pending action."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"}, "close Notepad")
        with patch("jarvis_windows.close_application", return_value=jarvis_windows.ToolResult(True, "Closed Notepad.")):
            reply = jarvis_brain.think("yes close the Notepad application")
        self.assertEqual(reply, "Closed Notepad.")
        self.assertFalse(jarvis_policy.has_pending_approval())

    def test_approval_no_cancels(self):
        """'No' cancels the pending action."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"}, "close Notepad")
        reply = jarvis_brain.think("no")
        self.assertEqual(reply, "Okay, I cancelled that action.")
        self.assertFalse(jarvis_policy.has_pending_approval())

    def test_approval_repeating_close_application_does_not_authorize_execution(self):
        """Repeating 'close application' does not authorize execution; prompts user for yes/no."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"}, "close Notepad")
        with patch("jarvis_windows.close_application") as mock_close:
            reply = jarvis_brain.think("close application")
        mock_close.assert_not_called()
        self.assertIn("waiting for your confirmation to close Notepad", reply)
        self.assertIn("say 'yes' to proceed or 'no' to cancel", reply)
        self.assertTrue(jarvis_policy.has_pending_approval())

    def test_approval_unrelated_conversation_does_not_authorize_execution(self):
        """Unrelated conversation does not authorize execution while approval is pending."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"}, "close Notepad")
        class _FakeCompletions:
            def create(self, **kwargs):
                self.tools_provided = kwargs.get("tools")
                message = type("Msg", (), {"content": "Python is a programming language.", "tool_calls": None})()
                choice = type("Choice", (), {"message": message})()
                return type("Resp", (), {"choices": [choice]})()

        fake_comp = _FakeCompletions()
        jarvis_brain._client = type("Client", (), {"chat": type("Chat", (), {"completions": fake_comp})()})()
        with patch("jarvis_windows.close_application") as mock_close, patch("jarvis_brain._get_api_key", return_value="k"), patch("jarvis_brain._relevant_memory_context", return_value=""):
            reply = jarvis_brain.think("Explain Python")
        mock_close.assert_not_called()
        self.assertEqual(reply, "Python is a programming language.")
        # Model was NOT offered tools while approval was pending
        self.assertIsNone(fake_comp.tools_provided)
        # Pending approval is preserved for subsequent resolution
        self.assertTrue(jarvis_policy.has_pending_approval())

    def test_approval_modified_arguments_cannot_consume_approval(self):
        """Approval granted for one set of arguments fails if arguments are altered."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"}, "close Notepad")
        self.assertFalse(jarvis_policy.consume_approval("close_application", {"name": "Calculator"}))
        self.assertFalse(jarvis_policy.has_pending_approval())

    def test_approval_pending_action_executes_at_most_once(self):
        """Pending approval is consumed and executes at most once."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"}, "close Notepad")
        with patch("jarvis_windows.close_application", return_value=jarvis_windows.ToolResult(True, "Closed Notepad.")) as mock_close:
            r1 = jarvis_brain.think("yes")
            self.assertEqual(r1, "Closed Notepad.")
            self.assertEqual(mock_close.call_count, 1)

            # Second 'yes' has no pending approval to consume
            r2 = jarvis_brain.think("yes")
            self.assertNotEqual(r2, "Closed Notepad.")
            self.assertEqual(mock_close.call_count, 1)

    def test_approval_new_request_cannot_silently_replace_existing_pending_approval(self):
        """A new request cannot silently replace an existing pending approval."""
        jarvis_policy.set_pending_approval("close_application", {"name": "Notepad"}, "close Notepad")
        result = jarvis_tools.execute_tool("close_application", {"name": "Calculator"})
        self.assertFalse(result.ok)
        self.assertIn("another action (close Notepad) is already awaiting confirmation", result.message)
        # Verify original pending action was not overwritten
        pending = jarvis_policy.get_pending_approval()
        self.assertIsNotNone(pending)
        self.assertEqual(pending.arguments["name"], "Notepad")

    # ---------------------------------------------------------
    # Regression Tests: Failure 2 (Notepad Target & Typing)
    # ---------------------------------------------------------

    def test_notepad_valid_title_variation_passes_target_validation(self):
        """Normal Notepad title variations accurately pass target validation."""
        self.assertTrue(jarvis_windows.verify_window_target("Notepad", "Untitled - Notepad"))
        self.assertTrue(jarvis_windows.verify_window_target("Notepad", "*Untitled - Notepad"))
        self.assertTrue(jarvis_windows.verify_window_target("Notepad", "notes.txt - Notepad"))
        self.assertTrue(jarvis_windows.verify_window_target("Notepad", "Notepad"))
        self.assertTrue(jarvis_windows.verify_window_target("VS Code", "main.py - Visual Studio Code"))

    def test_notepad_wrong_foreground_window_blocks_typing(self):
        """Wrong foreground window strictly blocks typing without injecting keys."""
        with patch("jarvis_windows.foreground_window_title", return_value="Google Chrome"), patch("jarvis_windows.audit_action"):
            result = jarvis_windows.type_text("hello", "Notepad")
        self.assertFalse(result.ok)
        self.assertIn("is not the focused window", result.message)
        self.assertIn("Google Chrome", result.message)

    def test_notepad_missing_foreground_window_blocks_typing(self):
        """Missing foreground window strictly blocks typing."""
        with patch("jarvis_windows.foreground_window_title", return_value=""), patch("jarvis_windows.audit_action"):
            result = jarvis_windows.type_text("hello", "Notepad")
        self.assertFalse(result.ok)
        self.assertIn("no focused window", result.message)

    def test_notepad_keyboard_injection_failure_returns_failure(self):
        """Keyboard SendInput failure is reported honestly with diagnostics."""
        with patch("jarvis_windows.foreground_window_title", return_value="Untitled - Notepad"), patch(
            "ctypes.windll.user32.SendInput", return_value=0
        ), patch("ctypes.GetLastError", return_value=5), patch("jarvis_windows.audit_action"):
            result = jarvis_windows.type_text("hello", "Notepad")
        self.assertFalse(result.ok)
        self.assertIn("Windows could not type into Untitled - Notepad", result.message)
        self.assertIn("error code 5", result.message)

    def test_notepad_successful_injection_is_reported_accurately(self):
        """Successful SendInput injection is reported accurately with character counts."""
        with patch("jarvis_windows.foreground_window_title", return_value="Untitled - Notepad"), patch(
            "ctypes.windll.user32.SendInput", return_value=1
        ), patch("jarvis_windows.audit_action"):
            result = jarvis_windows.type_text("hello", "Notepad")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Typed 5 characters into Untitled - Notepad.")
        self.assertEqual(result.data["characters"], 5)
        self.assertEqual(result.data["window"], "Untitled - Notepad")

    def test_notepad_planner_supplies_correct_target_and_text_arguments(self):
        """Planner strips trailing punctuation from voice transcripts and supplies clean args."""
        with patch("jarvis_planner.execute_tool") as mock_exec:
            jarvis_planner.plan_direct_desktop_request("Jarvis, type hello in Notepad.")
        mock_exec.assert_called_once_with("type_text", {"text": "hello", "expected_window": "Notepad"})


if __name__ == "__main__":
    unittest.main()
