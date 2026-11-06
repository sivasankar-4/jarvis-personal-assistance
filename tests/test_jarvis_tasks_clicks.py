"""Task-executor click verification and plan-gating tests."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_routines
import jarvis_tasks
import jarvis_windows


class TaskClickTests(unittest.TestCase):
    @staticmethod
    def _click_step(step_id="click", dependencies=None):
        return jarvis_tasks.TaskStep(
            step_id=step_id, description="Verified click", tool_name="click_and_verify",
            arguments={"x": 10, "y": 20, "button": "left", "clicks": 1},
            dependencies=dependencies or [],
        )

    @staticmethod
    def _click_result(changed=False, before="A", after="A"):
        return jarvis_windows.ToolResult(
            True, "Clicked.",
            {"changed": changed, "changed_percent": 2.5, "change_region": (0, 0, 10, 10),
             "foreground_before": before, "foreground_after": after},
        )

    def test_click_plan_step_passes_validation(self):
        self.assertEqual(jarvis_tasks.validate_plan([self._click_step()]), (True, "Plan is valid."))

    def test_click_verified_on_pixel_change(self):
        task = jarvis_tasks.Task("pixel", "click", steps=[self._click_step()])
        with patch("jarvis_tools.execute_tool", return_value=self._click_result(changed=True)):
            jarvis_tasks.execute_task(task)
        self.assertEqual(task.status, jarvis_tasks.TaskStatus.COMPLETED)
        self.assertEqual(task.steps[0].verification_status, "VERIFIED")

    def test_click_verified_on_foreground_change(self):
        task = jarvis_tasks.Task("window", "click", steps=[self._click_step()])
        with patch("jarvis_tools.execute_tool", return_value=self._click_result(after="B")):
            jarvis_tasks.execute_task(task)
        self.assertEqual(task.status, jarvis_tasks.TaskStatus.COMPLETED)
        self.assertEqual(task.steps[0].verification_status, "VERIFIED")

    def test_unverified_click_halts_plan(self):
        steps = [
            jarvis_tasks.TaskStep("launch", "Launch", "launch_application", {"name": "notepad"}),
            self._click_step("click", ["launch"]),
            jarvis_tasks.TaskStep("open", "Open", "open_path", {"path": "C:\\Jarvis"}, ["click"]),
        ]
        task = jarvis_tasks.Task("halt", "halt after no-op click", steps=steps)
        results = [
            jarvis_windows.ToolResult(True, "Launched.", {"pid": 1}),
            self._click_result(),
        ]
        with patch("jarvis_tools.execute_tool", side_effect=results) as execute_tool:
            jarvis_tasks.execute_task(task)
        self.assertEqual(task.steps[1].status, jarvis_tasks.StepStatus.FAILED)
        self.assertEqual(task.steps[1].verification_status, "FAILED")
        self.assertEqual(task.steps[2].status, jarvis_tasks.StepStatus.SKIPPED)
        self.assertEqual(execute_tool.call_count, 2)
        self.assertEqual(task.status, jarvis_tasks.TaskStatus.PARTIALLY_COMPLETED)
        self.assertIn("verification", task.error)

    def test_click_at_step_rejected(self):
        step = jarvis_tasks.TaskStep("click", "Click", "click_at", {"x": 1, "y": 2, "button": "left", "clicks": 1})
        valid, message = jarvis_tasks.validate_plan([step])
        self.assertFalse(valid)
        self.assertIn("click_and_verify", message)

    def test_observation_steps_rejected(self):
        steps = [
            jarvis_tasks.TaskStep("vision", "Observe", "ask_screen", {"question": "What?", "target": "screen"}),
            jarvis_tasks.TaskStep("screen", "Capture", "take_screenshot", {"target": "screen"}),
        ]
        for step in steps:
            with self.subTest(tool=step.tool_name):
                valid, message = jarvis_tasks.validate_plan([step])
                self.assertFalse(valid)
                self.assertIn("observation tool", message)

    def test_non_click_plan_regression(self):
        steps = [
            jarvis_tasks.TaskStep("launch", "Launch", "launch_application", {"name": "notepad"}),
            jarvis_tasks.TaskStep("open", "Open", "open_path", {"path": "C:\\Jarvis"}, ["launch"]),
        ]
        task = jarvis_tasks.Task("normal", "normal tools", steps=steps)
        results = [
            jarvis_windows.ToolResult(True, "Launched.", {"pid": 1}),
            jarvis_windows.ToolResult(True, "Opened.", {"kind": "folder"}),
        ]
        with patch("jarvis_tools.execute_tool", side_effect=results):
            jarvis_tasks.execute_task(task)
        self.assertEqual(task.status, jarvis_tasks.TaskStatus.COMPLETED)
        self.assertTrue(all(step.verification_status == "VERIFIED" for step in task.steps))

    def test_routine_click_step_still_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            database = str(Path(temp) / "routines.sqlite3")
            with patch.dict(os.environ, {"JARVIS_ROUTINES_DB": database}):
                result = jarvis_routines.save_routine("click routine", [{
                    "tool": "click_and_verify",
                    "arguments": {"x": 1, "y": 2, "button": "left", "clicks": 1},
                }])
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
