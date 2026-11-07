"""Tests for scheduled routines in jarvis_routines.py."""

import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import jarvis_policy
import jarvis_routines
import jarvis_windows


class RoutineScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = jarvis_routines.RoutineStore(Path(self.temp.name) / "routines.sqlite3")
        self.store_patcher = patch("jarvis_routines._get_store", return_value=self.store)
        self.store_patcher.start()
        self.addCleanup(self.store_patcher.stop)

    @staticmethod
    def _step(tool="launch_application", name="notepad"):
        return {"tool": tool, "arguments": {"name": name}}

    def _save(self, name="setup"):
        return jarvis_routines.save_routine(name, [self._step()])

    # 1. set_daily_success: fields stored, enabled, last_run_on None, message, data
    def test_set_daily_success(self):
        self._save("morning")
        result = jarvis_routines.set_routine_schedule("morning", "daily", "07:30", "")
        self.assertTrue(result.ok, result.message)
        self.assertIn("'morning' will run every day at 07:30.", result.message)
        self.assertEqual(
            result.data,
            {"name": "morning", "kind": "daily", "time": "07:30", "date": None},
        )

        stored = self.store.get_schedule("morning")
        self.assertIsNotNone(stored)
        self.assertEqual(stored["routine_name"], "morning")
        self.assertEqual(stored["kind"], "daily")
        self.assertEqual(stored["time"], "07:30")
        self.assertIsNone(stored["date"])
        self.assertTrue(stored["enabled"])
        self.assertIsNone(stored["last_run_on"])

    # 2. set_replaces_existing: daily then once -> one row, kind once
    def test_set_replaces_existing(self):
        self._save("work")
        jarvis_routines.set_routine_schedule("work", "daily", "09:00", "")
        future_date = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        result = jarvis_routines.set_routine_schedule("work", "once", "10:00", future_date)
        self.assertTrue(result.ok)

        schedules = self.store.list_schedules()
        self.assertEqual(len(schedules), 1)
        self.assertEqual(schedules[0]["kind"], "once")
        self.assertEqual(schedules[0]["time"], "10:00")
        self.assertEqual(schedules[0]["date"], future_date)
        self.assertTrue(schedules[0]["enabled"])
        self.assertIsNone(schedules[0]["last_run_on"])

    # 3. Validation failures
    def test_set_unknown_routine(self):
        result = jarvis_routines.set_routine_schedule("ghost", "daily", "08:00", "")
        self.assertFalse(result.ok)
        self.assertIn("I don't have a routine called 'ghost'.", result.message)

    def test_set_bad_kind(self):
        self._save("alarm")
        result = jarvis_routines.set_routine_schedule("alarm", "weekly", "08:00", "")
        self.assertFalse(result.ok)
        self.assertIn("A schedule is either 'daily' or 'once'.", result.message)

    def test_set_bad_time(self):
        self._save("alarm")
        for bad in ["9pm", "25:00", "07:60", "7:30", "invalid"]:
            with self.subTest(bad=bad):
                result = jarvis_routines.set_routine_schedule("alarm", "daily", bad, "")
                self.assertFalse(result.ok)
                self.assertIn("Tell me the time as HH:MM in 24-hour format", result.message)

    def test_set_once_bad_date(self):
        self._save("alarm")
        for bad in ["2026-02-30", "tomorrow", "2026/11/09", ""]:
            with self.subTest(bad=bad):
                result = jarvis_routines.set_routine_schedule("alarm", "once", "08:00", bad)
                self.assertFalse(result.ok)
                self.assertIn("A one-time schedule needs a date like 2026-11-09.", result.message)

    def test_set_once_in_past(self):
        self._save("alarm")
        past_date = "2000-01-01"
        result = jarvis_routines.set_routine_schedule("alarm", "once", "08:00", past_date)
        self.assertFalse(result.ok)
        self.assertIn("That time is already past.", result.message)

    # 4. clear: set -> clear ok + row gone; clear again -> not ok
    def test_clear(self):
        self._save("alarm")
        jarvis_routines.set_routine_schedule("alarm", "daily", "08:00", "")
        self.assertIsNotNone(self.store.get_schedule("alarm"))

        res_clear = jarvis_routines.clear_routine_schedule("alarm")
        self.assertTrue(res_clear.ok)
        self.assertIn("'alarm' will no longer run on a schedule.", res_clear.message)
        self.assertIsNone(self.store.get_schedule("alarm"))

        res_clear_again = jarvis_routines.clear_routine_schedule("alarm")
        self.assertFalse(res_clear_again.ok)
        self.assertIn("'alarm' doesn't have a schedule.", res_clear_again.message)

    # 5. due_daily: before time False; at time True; after time True; last_run_on today False; enabled False False
    def test_due_daily(self):
        today = datetime.date(2026, 11, 7)
        schedule = {
            "routine_name": "r",
            "kind": "daily",
            "time": "08:00",
            "date": None,
            "enabled": True,
            "last_run_on": None,
        }

        cases = [
            ("before time", datetime.datetime(2026, 11, 7, 7, 59), schedule, False),
            ("at time", datetime.datetime(2026, 11, 7, 8, 0), schedule, True),
            ("after time", datetime.datetime(2026, 11, 7, 8, 30), schedule, True),
            (
                "already run today",
                datetime.datetime(2026, 11, 7, 8, 30),
                {**schedule, "last_run_on": today.isoformat()},
                False,
            ),
            ("disabled", datetime.datetime(2026, 11, 7, 8, 30), {**schedule, "enabled": False}, False),
        ]
        for desc, now_dt, s, expected in cases:
            with self.subTest(desc=desc):
                self.assertEqual(jarvis_routines._is_due(s, now_dt), expected)

    # 6. due_once: before False; at moment True; +23h True; +25h False; last_run_on set False
    def test_due_once(self):
        schedule = {
            "routine_name": "r",
            "kind": "once",
            "time": "10:00",
            "date": "2026-11-10",
            "enabled": True,
            "last_run_on": None,
        }

        cases = [
            ("before moment", datetime.datetime(2026, 11, 10, 9, 59), schedule, False),
            ("at moment", datetime.datetime(2026, 11, 10, 10, 0), schedule, True),
            ("+23h grace", datetime.datetime(2026, 11, 11, 9, 0), schedule, True),
            ("+25h expired", datetime.datetime(2026, 11, 11, 11, 0), schedule, False),
            (
                "last_run_on set",
                datetime.datetime(2026, 11, 10, 10, 30),
                {**schedule, "last_run_on": "2026-11-10"},
                False,
            ),
        ]
        for desc, now_dt, s, expected in cases:
            with self.subTest(desc=desc):
                self.assertEqual(jarvis_routines._is_due(s, now_dt), expected)

    # 7. delete_routine_cascades: schedule row gone after delete
    def test_delete_routine_cascades(self):
        self._save("cleanup")
        jarvis_routines.set_routine_schedule("cleanup", "daily", "08:00", "")
        self.assertIsNotNone(self.store.get_schedule("cleanup"))

        jarvis_routines.delete_routine("cleanup")
        self.assertIsNone(self.store.get("cleanup"))
        self.assertIsNone(self.store.get_schedule("cleanup"))

    # 8. fire_daily_stamps_and_announces
    def test_fire_daily_stamps_and_announces(self):
        self._save("daily_job")
        jarvis_routines.set_routine_schedule("daily_job", "daily", "08:00", "")

        announcements = []
        runner = MagicMock(return_value=jarvis_windows.ToolResult(True, "ran successfully"))
        now = datetime.datetime(2026, 11, 7, 8, 15)

        fired = jarvis_routines._run_due_once(now=now, runner=runner, announcer=announcements.append)
        self.assertEqual(fired, ["daily_job"])
        runner.assert_called_once_with("daily_job")

        sched = self.store.get_schedule("daily_job")
        self.assertEqual(sched["last_run_on"], now.date().isoformat())
        self.assertTrue(sched["enabled"])
        self.assertEqual(len(announcements), 1)
        self.assertIn("daily_job", announcements[0])
        self.assertIn("ran successfully", announcements[0])

        # Immediate second _run_due_once -> fired []
        fired_again = jarvis_routines._run_due_once(now=now, runner=runner, announcer=announcements.append)
        self.assertEqual(fired_again, [])

    # 9. fire_once_disables: after firing, enabled is False
    def test_fire_once_disables(self):
        self._save("once_job")
        future_date = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        jarvis_routines.set_routine_schedule("once_job", "once", "10:00", future_date)

        moment = datetime.datetime.strptime(future_date, "%Y-%m-%d").replace(hour=10, minute=5)
        runner = MagicMock(return_value=jarvis_windows.ToolResult(True, "done"))

        fired = jarvis_routines._run_due_once(now=moment, runner=runner)
        self.assertEqual(fired, ["once_job"])

        sched = self.store.get_schedule("once_job")
        self.assertFalse(sched["enabled"])
        self.assertEqual(sched["last_run_on"], moment.date().isoformat())

    # 10. fire_skips_pending_approval
    def test_fire_skips_pending_approval(self):
        self._save("pending_job")
        jarvis_routines.set_routine_schedule("pending_job", "daily", "08:00", "")

        runner = MagicMock()
        now = datetime.datetime(2026, 11, 7, 8, 30)

        with patch("jarvis_policy.get_pending_approval", return_value=object()):
            fired = jarvis_routines._run_due_once(now=now, runner=runner)

        self.assertEqual(fired, [])
        runner.assert_not_called()

    # 11. fire_skips_when_lock_held
    def test_fire_skips_when_lock_held(self):
        self._save("locked_job")
        jarvis_routines.set_routine_schedule("locked_job", "daily", "08:00", "")

        runner = MagicMock()
        now = datetime.datetime(2026, 11, 7, 8, 30)

        with jarvis_routines._firing_lock:
            fired = jarvis_routines._run_due_once(now=now, runner=runner)

        self.assertEqual(fired, [])
        runner.assert_not_called()

    # 12. expire_once: past-grace once -> _run_due_once disables it without calling the runner
    def test_expire_once(self):
        self._save("expired_job")
        # Store past once schedule directly in DB
        self.store.set_schedule("expired_job", "once", "08:00", "2026-11-01")

        runner = MagicMock()
        now = datetime.datetime(2026, 11, 7, 8, 0)
        fired = jarvis_routines._run_due_once(now=now, runner=runner)

        self.assertEqual(fired, [])
        runner.assert_not_called()

        sched = self.store.get_schedule("expired_job")
        self.assertFalse(sched["enabled"])

    # 13. policy_low: both tools classify LOW
    def test_policy_low(self):
        self.assertEqual(
            jarvis_policy.classify_risk("set_routine_schedule"),
            jarvis_policy.RiskLevel.LOW,
        )
        self.assertEqual(
            jarvis_policy.classify_risk("clear_routine_schedule"),
            jarvis_policy.RiskLevel.LOW,
        )

    # 14. list_shows_schedule: list_routines message contains "(every day at 07:30)"
    def test_list_shows_schedule(self):
        self._save("morning")
        self._save("evening")
        jarvis_routines.set_routine_schedule("morning", "daily", "07:30", "")

        result = jarvis_routines.list_routines()
        self.assertTrue(result.ok)
        self.assertIn("morning (every day at 07:30)", result.message)
        self.assertIn("evening", result.message)

        routines_by_name = {r["name"]: r for r in result.data["routines"]}
        self.assertEqual(
            routines_by_name["morning"]["schedule"],
            {"kind": "daily", "time": "07:30", "date": None},
        )
        self.assertIsNone(routines_by_name["evening"]["schedule"])


if __name__ == "__main__":
    unittest.main()
