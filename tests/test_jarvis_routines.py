"""Tests for validated, persistent Jarvis routines."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_policy
import jarvis_routines
import jarvis_tools


class RoutineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = jarvis_routines.RoutineStore(Path(self.temp.name) / "routines.sqlite3")
        self.store_patcher = patch("jarvis_routines._get_store", return_value=self.store)
        self.store_patcher.start()
        self.addCleanup(self.store_patcher.stop)

    @staticmethod
    def _launch(name="notepad"):
        return {"tool": "launch_application", "arguments": {"name": name}}

    @staticmethod
    def _volume(action="up"):
        return {"tool": "adjust_volume", "arguments": {"action": action}}

    def test_save_and_list_roundtrip(self):
        self.assertTrue(jarvis_routines.save_routine("Zulu", [self._launch()]).ok)
        self.assertTrue(jarvis_routines.save_routine("Alpha", [self._launch(), self._volume()]).ok)
        result = jarvis_routines.list_routines()
        self.assertTrue(result.ok)
        self.assertEqual(result.data["routines"], [{"name": "Alpha", "steps": 2}, {"name": "Zulu", "steps": 1}])

    def test_save_replaces_existing(self):
        jarvis_routines.save_routine("setup", [self._launch()])
        result = jarvis_routines.save_routine("setup", [self._volume()])
        self.assertTrue(result.ok)
        self.assertTrue(result.data["replaced"])
        self.assertIn("replaced", result.message)
        self.assertEqual(self.store.get("setup")["steps"], [self._volume()])

    def test_name_normalization(self):
        result = jarvis_routines.save_routine("  Study   Setup ", [self._launch()])
        self.assertEqual(result.data["name"], "Study Setup")
        self.assertEqual(self.store.get("  study  setup  ")["name"], "Study Setup")
        self.assertEqual(jarvis_routines.list_routines().data["routines"][0]["name"], "Study Setup")

    def test_rejects_non_allowlisted_step(self):
        result = jarvis_routines.save_routine("bad", [{"tool": "click_at", "arguments": {}}])
        self.assertFalse(result.ok)
        self.assertIn("can't be used in a routine", result.message)
        self.assertEqual(self.store.list_all(), [])

    def test_rejects_bad_steps(self):
        bad_steps = [
            [],
            [self._launch()] * 21,
            [{"tool": "launch_application"}],
            [{"tool": "launch_application", "arguments": {"name": "x"}, "extra": True}],
            [{"tool": "launch_application", "arguments": {"name": 42}}],
            [{"tool": "launch_application", "arguments": {"name": "x", "extra": True}}],
        ]
        for steps in bad_steps:
            with self.subTest(steps=steps):
                self.assertFalse(jarvis_routines.save_routine("bad", steps).ok)
        self.assertEqual(self.store.list_all(), [])

    def test_run_executes_steps_in_order(self):
        steps = [self._launch(), self._volume()]
        jarvis_routines.save_routine("setup", steps)
        import jarvis_tasks

        captured = {}
        def execute(task):
            captured["task"] = task
            for step in task.steps:
                step.status = jarvis_tasks.StepStatus.COMPLETED
            task.status = jarvis_tasks.TaskStatus.COMPLETED
            return "done"

        with patch("jarvis_tasks.execute_task", side_effect=execute):
            result = jarvis_routines.run_routine("setup")
        self.assertTrue(result.ok)
        self.assertEqual([step.tool_name for step in captured["task"].steps], ["launch_application", "adjust_volume"])
        self.assertEqual([step.arguments for step in captured["task"].steps], [step["arguments"] for step in steps])
        self.assertEqual(result.data["steps_completed"], 2)
        self.assertEqual(result.data["steps_total"], 2)

    def test_run_partial_failure_reported(self):
        jarvis_routines.save_routine("setup", [self._launch(), self._volume()])
        import jarvis_tasks

        def execute(task):
            task.steps[0].status = jarvis_tasks.StepStatus.COMPLETED
            task.status = jarvis_tasks.TaskStatus.PARTIALLY_COMPLETED
            return "partial"

        with patch("jarvis_tasks.execute_task", side_effect=execute):
            result = jarvis_routines.run_routine("setup")
        self.assertFalse(result.ok)
        self.assertEqual(result.data["status"], "PARTIALLY_COMPLETED")
        self.assertEqual(result.data["steps_completed"], 1)

    def test_run_missing_routine(self):
        jarvis_routines.save_routine("saved", [self._launch()])
        result = jarvis_routines.run_routine("missing")
        self.assertFalse(result.ok)
        self.assertIn("missing", result.message)
        self.assertIn("saved", result.message)

    def test_delete_routine(self):
        jarvis_routines.save_routine("setup", [self._launch()])
        self.assertTrue(jarvis_routines.delete_routine("setup").ok)
        self.assertEqual(self.store.list_all(), [])
        self.assertFalse(jarvis_routines.delete_routine("setup").ok)

    def test_store_persists(self):
        path = Path(self.temp.name) / "persistent.sqlite3"
        first = jarvis_routines.RoutineStore(path)
        first.save("setup", [self._launch()])
        second = jarvis_routines.RoutineStore(path)
        self.assertEqual(second.get("setup")["steps"], [self._launch()])

    def test_step_arg_types_match_registry(self):
        for tool in jarvis_routines.ROUTINE_STEP_TOOLS:
            self.assertEqual(jarvis_routines._STEP_ARG_TYPES[tool], jarvis_tools._TOOLS[tool][1])

    def test_allowlist_has_no_high_risk_tools(self):
        for tool in jarvis_routines.ROUTINE_STEP_TOOLS:
            self.assertIn(jarvis_policy.classify_risk(tool), (jarvis_policy.RiskLevel.LOW, jarvis_policy.RiskLevel.MODERATE))

    def test_policy_levels_for_routine_tools(self):
        self.assertEqual(jarvis_policy.classify_risk("save_routine"), jarvis_policy.RiskLevel.LOW)
        self.assertEqual(jarvis_policy.classify_risk("list_routines"), jarvis_policy.RiskLevel.LOW)
        self.assertEqual(jarvis_policy.classify_risk("run_routine"), jarvis_policy.RiskLevel.MODERATE)
        self.assertEqual(jarvis_policy.classify_risk("delete_routine"), jarvis_policy.RiskLevel.MODERATE)

    def test_dispatch_through_execute_tool(self):
        result = jarvis_tools.execute_tool("list_routines", {})
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {"routines": []})


if __name__ == "__main__":
    unittest.main()
