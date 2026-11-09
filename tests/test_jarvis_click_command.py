"""Tests for spoken click commands and EC-5 schema closure."""

import unittest
from unittest.mock import patch

import jarvis_planner
import jarvis_tools
from jarvis_windows import ToolResult


class CallRecorder:
    def __init__(self, responses: list[ToolResult] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self._responses = list(responses or [])

    def __call__(self, name: str, arguments: dict, **kwargs):
        self.calls.append((name, arguments))
        if self._responses:
            return self._responses.pop(0)
        return ToolResult(True, "OK", {})


class SpokenClickCommandTests(unittest.TestCase):
    def test_click_on_the_clock_success_with_visual_change(self):
        locate_res = ToolResult(
            True,
            "Found 'the clock' at (1851, 1079).",
            {"x": 1851, "y": 1079, "target": "screen", "model": "test-vision", "confirmed": True},
        )
        click_res = ToolResult(
            True,
            "Visible change detected (2.5% of screen altered).",
            {
                "changed": True,
                "changed_percent": 2.5,
                "change_region": (1800, 1050, 1900, 1080),
                "foreground_before": "Desktop",
                "foreground_after": "Desktop",
            },
        )
        recorder = CallRecorder([locate_res, click_res])
        with patch("jarvis_planner.execute_tool", side_effect=recorder):
            reply = jarvis_planner.plan_direct_desktop_request("click on the clock")

        self.assertEqual(
            recorder.calls,
            [
                ("locate_element", {"description": "the clock", "target": "screen"}),
                ("click_and_verify", {"x": 1851, "y": 1079, "button": "left", "clicks": 1}),
            ],
        )
        self.assertEqual(
            reply,
            "I clicked where I found the clock, and the screen changed a little, but I can't confirm that's what you asked for.",
        )

    def test_wake_word_and_politeness_stripped_case_preserved(self):
        locate_res = ToolResult(
            True,
            "Found 'Start button' at (20, 1050).",
            {"x": 20, "y": 1050, "target": "screen", "model": "test-vision", "confirmed": True},
        )
        click_res = ToolResult(
            True,
            "Foreground changed to Start menu.",
            {
                "changed": False,
                "foreground_before": "Desktop",
                "foreground_after": "Start menu",
            },
        )
        recorder = CallRecorder([locate_res, click_res])
        with patch("jarvis_planner.execute_tool", side_effect=recorder):
            reply = jarvis_planner.plan_direct_desktop_request("Jarvis, please click Start button")

        self.assertEqual(
            recorder.calls,
            [
                ("locate_element", {"description": "Start button", "target": "screen"}),
                ("click_and_verify", {"x": 20, "y": 1050, "button": "left", "clicks": 1}),
            ],
        )
        self.assertTrue(isinstance(reply, str) and reply.startswith("Clicked on Start button."))

    def test_locate_fails_honestly_no_click_attempted(self):
        locate_fail = ToolResult(False, "I couldn't find 'the clock' on the screen.")
        recorder = CallRecorder([locate_fail])
        with patch("jarvis_planner.execute_tool", side_effect=recorder):
            reply = jarvis_planner.plan_direct_desktop_request("click on the clock")

        self.assertEqual(
            recorder.calls,
            [("locate_element", {"description": "the clock", "target": "screen"})],
        )
        self.assertFalse(reply.ok)
        self.assertEqual(reply.message, "I couldn't find 'the clock' on the screen.")

    def test_locate_unconfirmed_refuses_to_click(self):
        locate_unconfirmed = ToolResult(
            True,
            "Found candidate.",
            {"x": 100, "y": 200, "target": "screen", "model": "test-vision"},  # confirmed missing
        )
        recorder = CallRecorder([locate_unconfirmed])
        with patch("jarvis_planner.execute_tool", side_effect=recorder):
            reply = jarvis_planner.plan_direct_desktop_request("click on the clock")

        self.assertEqual(
            recorder.calls,
            [("locate_element", {"description": "the clock", "target": "screen"})],
        )
        self.assertEqual(
            reply,
            "I found something that might be the clock, but I couldn't confirm it, so I won't click.",
        )

    def test_click_and_verify_no_change_reported(self):
        locate_res = ToolResult(
            True,
            "Found 'the clock' at (1851, 1079).",
            {"x": 1851, "y": 1079, "target": "screen", "model": "test-vision", "confirmed": True},
        )
        click_no_change = ToolResult(
            True,
            "The mouse clicked at (1851, 1079), but the screen did not visibly change.",
            {
                "changed": False,
                "changed_percent": 0.0,
                "change_region": None,
                "foreground_before": "Desktop",
                "foreground_after": "Desktop",
            },
        )
        recorder = CallRecorder([locate_res, click_no_change])
        with patch("jarvis_planner.execute_tool", side_effect=recorder):
            reply = jarvis_planner.plan_direct_desktop_request("click on the clock")

        self.assertEqual(
            recorder.calls,
            [
                ("locate_element", {"description": "the clock", "target": "screen"}),
                ("click_and_verify", {"x": 1851, "y": 1079, "button": "left", "clicks": 1}),
            ],
        )
        self.assertEqual(reply, "I clicked on the clock, but no visible change was detected.")

    def test_click_and_verify_foreground_changed_reports_success(self):
        locate_res = ToolResult(
            True,
            "Found 'the clock' at (1851, 1079).",
            {"x": 1851, "y": 1079, "target": "screen", "model": "test-vision", "confirmed": True},
        )
        click_fg_change = ToolResult(
            True,
            "Active window changed to Notification Center.",
            {
                "changed": False,
                "changed_percent": 0.0,
                "change_region": None,
                "foreground_before": "Desktop",
                "foreground_after": "Notification Center",
            },
        )
        recorder = CallRecorder([locate_res, click_fg_change])
        with patch("jarvis_planner.execute_tool", side_effect=recorder):
            reply = jarvis_planner.plan_direct_desktop_request("click on the clock")

        self.assertEqual(
            recorder.calls,
            [
                ("locate_element", {"description": "the clock", "target": "screen"}),
                ("click_and_verify", {"x": 1851, "y": 1079, "button": "left", "clicks": 1}),
            ],
        )
        self.assertTrue(isinstance(reply, str) and reply.startswith("Clicked on the clock."))

    def test_click_and_verify_fails_returns_result(self):
        locate_res = ToolResult(
            True,
            "Found 'the clock' at (1851, 1079).",
            {"x": 1851, "y": 1079, "target": "screen", "model": "test-vision", "confirmed": True},
        )
        click_fail = ToolResult(False, "Coordinates outside screen bounds.")
        recorder = CallRecorder([locate_res, click_fail])
        with patch("jarvis_planner.execute_tool", side_effect=recorder):
            reply = jarvis_planner.plan_direct_desktop_request("click on the clock")

        self.assertEqual(
            recorder.calls,
            [
                ("locate_element", {"description": "the clock", "target": "screen"}),
                ("click_and_verify", {"x": 1851, "y": 1079, "button": "left", "clicks": 1}),
            ],
        )
        self.assertFalse(reply.ok)
        self.assertEqual(reply.message, "Coordinates outside screen bounds.")

    def test_multi_intent_compound_command_refused(self):
        recorder = CallRecorder()
        with patch("jarvis_planner.execute_tool", side_effect=recorder):
            reply = jarvis_planner.plan_direct_desktop_request("click on the clock and open chrome")

        self.assertIsNone(reply)
        self.assertEqual(recorder.calls, [])

    def test_bare_click_prompts_clarification(self):
        for utterance in ("click", "click on", "click the", "click on the"):
            recorder = CallRecorder()
            with patch("jarvis_planner.execute_tool", side_effect=recorder):
                reply = jarvis_planner.plan_direct_desktop_request(utterance)

            self.assertEqual(reply, "What would you like me to click on?")
            self.assertEqual(recorder.calls, [])

    def test_schema_closure_and_registry_pins(self):
        schema_names = {entry["function"]["name"] for entry in jarvis_tools.TOOL_SCHEMAS}
        self.assertNotIn("click_at", schema_names)
        self.assertNotIn("click_and_verify", schema_names)
        self.assertIn("locate_element", schema_names)

        self.assertIn("click_at", jarvis_tools._TOOLS)
        self.assertIn("click_and_verify", jarvis_tools._TOOLS)


if __name__ == "__main__":
    unittest.main()
