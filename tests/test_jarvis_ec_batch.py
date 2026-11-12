"""Tests for EC-2/EC-3 YouTube routing, play_youtube composite tool, and loop non-interception."""

import unittest
from unittest.mock import MagicMock, call, patch

import jarvis_brain
import jarvis_loop
import jarvis_planner
import jarvis_policy
import jarvis_tasks
import jarvis_tools
import jarvis_vision
import jarvis_windows
from jarvis_windows import ToolResult


class TestJarvisECBatch(unittest.TestCase):
    # Tasks parsing
    def test_01_parse_open_youtube_and_search_english_songs(self):
        task = jarvis_tasks.create_task_from_objective("open YouTube and search english songs")
        self.assertNotEqual(task.status, jarvis_tasks.TaskStatus.FAILED)
        self.assertEqual(
            [(s.tool_name, s.arguments) for s in task.steps],
            [("search_youtube", {"query": "english songs"})],
        )

    def test_02_parse_open_youtube_and_play_lofi(self):
        task = jarvis_tasks.create_task_from_objective("open YouTube and play lofi")
        self.assertEqual(
            [(s.tool_name, s.arguments) for s in task.steps],
            [("play_youtube", {"query": "lofi"})],
        )

    def test_03_parse_open_youtube(self):
        task = jarvis_tasks.create_task_from_objective("open YouTube")
        self.assertEqual(
            [(s.tool_name, s.arguments) for s in task.steps],
            [("open_website", {"url": "https://www.youtube.com"})],
        )

    def test_04_parse_open_google_and_search_english_songs(self):
        task = jarvis_tasks.create_task_from_objective("open google and search english songs")
        self.assertEqual(
            [(s.tool_name, s.arguments) for s in task.steps],
            [
                ("open_website", {"url": "https://www.google.com"}),
                ("search_web", {"query": "english songs"}),
            ],
        )

    def test_05_parse_open_notepad_and_play_lofi(self):
        task = jarvis_tasks.create_task_from_objective("open notepad and play lofi")
        self.assertEqual(task.status, jarvis_tasks.TaskStatus.FAILED)
        self.assertIn("play lofi", task.error)

    def test_06_parse_open_youtube_and_play_lofi_on_youtube(self):
        task = jarvis_tasks.create_task_from_objective("open YouTube and play lofi on youtube")
        self.assertEqual(
            [(s.tool_name, s.arguments) for s in task.steps],
            [("search_youtube", {"query": "lofi"})],
        )

    def test_07_parse_open_notepad_and_type_hello(self):
        task = jarvis_tasks.create_task_from_objective("open notepad and type hello")
        self.assertEqual(
            [(s.tool_name, s.arguments) for s in task.steps],
            [
                ("launch_application", {"name": "notepad"}),
                ("type_text", {"text": "hello", "expected_window": "notepad"}),
            ],
        )

    # Planner
    @patch("jarvis_planner.execute_tool")
    def test_08_planner_open_youtube_and_play_lofi(self, mock_exec):
        jarvis_planner.plan_direct_desktop_request("open youtube and play lofi")
        self.assertEqual(mock_exec.call_args_list, [call("play_youtube", {"query": "lofi"})])

    @patch("jarvis_planner.execute_tool")
    def test_09_planner_search_and_open(self, mock_exec):
        jarvis_planner.plan_direct_desktop_request("open youtube and search lofi")
        mock_exec.assert_called_with("search_youtube", {"query": "lofi"})

        jarvis_planner.plan_direct_desktop_request("open youtube")
        mock_exec.assert_called_with("open_website", {"url": "https://www.youtube.com"})

    # play_youtube
    @patch("time.sleep")
    @patch("jarvis_vision.ask_screen")
    @patch("jarvis_planner.grounded_click")
    @patch("jarvis_tools.search_youtube")
    def test_14_play_youtube_search_failure(self, mock_search, mock_click, mock_ask, mock_sleep):
        mock_search.return_value = ToolResult(False, "search failed", {})

        res = jarvis_tools.play_youtube("lofi")
        self.assertFalse(res.ok)
        mock_click.assert_not_called()

    # Policy + registry
    def test_15_policy_and_registry(self):
        self.assertEqual(
            jarvis_policy.classify_risk("play_youtube", {"query": "x"}),
            jarvis_policy.RiskLevel.MODERATE,
        )
        eval_res = jarvis_policy.evaluate("play_youtube", {"query": "x"})
        self.assertTrue(eval_res.allowed)
        self.assertFalse(eval_res.requires_approval)

        target_func, allowed_types = jarvis_tools._TOOLS["play_youtube"]
        self.assertEqual(allowed_types, {"query": str})

    # Loop non-interception (D5)
    @patch("jarvis_loop.run_gui_loop")
    @patch("jarvis_tasks.execute_task")
    def test_16_think_open_youtube_and_play_lofi(self, mock_execute_task, mock_run_gui_loop):
        mock_execute_task.return_value = "Done"
        try:
            jarvis_brain.think("open YouTube and play lofi")
            self.assertTrue(mock_execute_task.called)
            passed_task = mock_execute_task.call_args[0][0]
            self.assertEqual(
                [(s.tool_name, s.arguments) for s in passed_task.steps],
                [("play_youtube", {"query": "lofi"})],
            )
            mock_run_gui_loop.assert_not_called()
        finally:
            jarvis_tasks.task_manager.clear_active_task()

    @patch("jarvis_loop.run_gui_loop")
    @patch("jarvis_tasks.execute_task")
    def test_17_think_open_youtube_and_search_english_songs(self, mock_execute_task, mock_run_gui_loop):
        mock_execute_task.return_value = "Done"
        try:
            jarvis_brain.think("open YouTube and search english songs")
            self.assertTrue(mock_execute_task.called)
            passed_task = mock_execute_task.call_args[0][0]
            self.assertEqual(
                [(s.tool_name, s.arguments) for s in passed_task.steps],
                [("search_youtube", {"query": "english songs"})],
            )
            mock_run_gui_loop.assert_not_called()
        finally:
            jarvis_tasks.task_manager.clear_active_task()


if __name__ == "__main__":
    unittest.main()
