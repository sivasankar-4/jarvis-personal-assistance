"""Tests for TASK-024 bounded GUI observe-act-verify loop."""

import json
import unittest
from unittest.mock import MagicMock, call, patch

import jarvis_brain
import jarvis_loop
import jarvis_policy
import jarvis_tasks
import jarvis_windows
from jarvis_windows import ToolResult


class JarvisLoopTests(unittest.TestCase):
    def test_trigger_set(self):
        """Trigger set: 'play lofi' True, 'search the web' False, 'display' False."""
        self.assertTrue(jarvis_loop.should_attempt_loop("play lofi"))
        self.assertFalse(jarvis_loop.should_attempt_loop("search the web"))
        self.assertFalse(jarvis_loop.should_attempt_loop("display"))
        # Additional verb checks
        self.assertTrue(jarvis_loop.should_attempt_loop("click the button"))
        self.assertTrue(jarvis_loop.should_attempt_loop("type password"))
        self.assertTrue(jarvis_loop.should_attempt_loop("press enter"))
        self.assertFalse(jarvis_loop.should_attempt_loop(""))
        self.assertFalse(jarvis_loop.should_attempt_loop(None))

    def test_argument_mapping(self):
        """type/open argument mapping (expected_window!)."""
        # type -> type_text, text, expected_window
        choice_type = {"action": "type", "text": "my note", "window": "Notepad"}
        tool, args = jarvis_loop._tool_for(choice_type)
        self.assertEqual(tool, "type_text")
        self.assertEqual(args, {"text": "my note", "expected_window": "Notepad"})

        # open app -> launch_application, name
        choice_app = {"action": "open", "kind": "app", "value": "spotify"}
        tool, args = jarvis_loop._tool_for(choice_app)
        self.assertEqual(tool, "launch_application")
        self.assertEqual(args, {"name": "spotify"})

        # open website -> open_website, url
        choice_web = {"action": "open", "kind": "website", "value": "https://example.com"}
        tool, args = jarvis_loop._tool_for(choice_web)
        self.assertEqual(tool, "open_website")
        self.assertEqual(args, {"url": "https://example.com"})

        # key -> send_shortcut, shortcut
        choice_key = {"action": "key", "shortcut": "ctrl+s"}
        tool, args = jarvis_loop._tool_for(choice_key)
        self.assertEqual(tool, "send_shortcut")
        self.assertEqual(args, {"shortcut": "ctrl+s"})

        # click -> click_and_verify, {}
        choice_click = {"action": "click", "target": "Save button"}
        tool, args = jarvis_loop._tool_for(choice_click)
        self.assertEqual(tool, "click_and_verify")
        self.assertEqual(args, {})

    def test_contract_rejects_parse_choice(self):
        """contract rejects: malformed/unknown/extra-key/click-with-coordinates/type-missing-window/None."""
        self.assertIsNone(jarvis_loop._parse_choice(None))
        self.assertIsNone(jarvis_loop._parse_choice(""))
        self.assertIsNone(jarvis_loop._parse_choice("{invalid json"))
        self.assertIsNone(jarvis_loop._parse_choice("123"))
        self.assertIsNone(jarvis_loop._parse_choice(json.dumps(["not a dict"])))

        # unknown action
        self.assertIsNone(jarvis_loop._parse_choice(json.dumps({"action": "jump"})))

        # extra-key
        self.assertIsNone(
            jarvis_loop._parse_choice(
                json.dumps({"action": "click", "target": "button", "unexpected": "value"})
            )
        )
        self.assertIsNone(
            jarvis_loop._parse_choice(
                json.dumps({"done": True, "evidence": "done", "extra": 1})
            )
        )
        self.assertIsNone(
            jarvis_loop._parse_choice(
                json.dumps({"stuck": "blocked", "extra": 2})
            )
        )

        # click-with-coordinates
        self.assertIsNone(
            jarvis_loop._parse_choice(json.dumps({"action": "click", "x": 100, "y": 200}))
        )

        # type-missing-window
        self.assertIsNone(
            jarvis_loop._parse_choice(json.dumps({"action": "type", "text": "hello"}))
        )
        # type non-string window or text
        self.assertIsNone(
            jarvis_loop._parse_choice(json.dumps({"action": "type", "text": 123, "window": "app"}))
        )

        # open invalid kind or non-string
        self.assertIsNone(
            jarvis_loop._parse_choice(json.dumps({"action": "open", "kind": "file", "value": "x"}))
        )

    def test_contract_rejects_halts_with_zero_executions(self):
        """Malformed or rejected chooser replies halt the loop with zero executions."""
        bad_choices = [
            None,
            "",
            "{invalid json",
            json.dumps({"action": "jump"}),
            json.dumps({"action": "click", "target": "button", "extra": 123}),
            json.dumps({"action": "click", "x": 100, "y": 200}),
            json.dumps({"action": "type", "text": "hello"}),
        ]

        for bad in bad_choices:
            with self.subTest(bad=bad):
                with patch("jarvis_loop.ask_screen", return_value=ToolResult(True, "NO, see app.")) as mock_screen, \
                     patch("jarvis_brain.choose_loop_action", return_value=bad) as mock_choose, \
                     patch("jarvis_loop.execute_tool") as mock_exec, \
                     patch("jarvis_loop.grounded_click") as mock_click:
                    report = jarvis_loop.run_gui_loop("play music")
                    self.assertIn("Outcome: PARTIAL", report)
                    self.assertIn("I couldn't decide the next step, so I stopped.", report)
                    mock_exec.assert_not_called()
                    mock_click.assert_not_called()

    def test_happy_path_to_completed_on_observation_yes(self):
        """Happy path to COMPLETED when observation starts with YES."""
        with patch("jarvis_loop.ask_screen", return_value=ToolResult(True, "YES, the music is playing now.")) as mock_screen, \
             patch("jarvis_brain.choose_loop_action") as mock_choose, \
             patch("jarvis_loop.execute_tool") as mock_exec:
            report = jarvis_loop.run_gui_loop("play lofi")
            self.assertIn("Outcome: COMPLETED — The latest observation confirms the goal is done.", report)
            mock_choose.assert_not_called()
            mock_exec.assert_not_called()

    def test_unconfirmed_click_failed_halt(self):
        """unconfirmed click → FAILED halt."""
        observe_result = ToolResult(True, "NO, browser is open on youtube.")
        click_choice = json.dumps({"action": "click", "target": "play button"})
        unconfirmed_result = ToolResult(
            False,
            "I found something that might be play button, but I couldn't confirm it, so I won't click."
        )

        with patch("jarvis_loop.ask_screen", return_value=observe_result), \
             patch("jarvis_brain.choose_loop_action", return_value=click_choice), \
             patch("jarvis_loop.grounded_click", return_value=unconfirmed_result) as mock_click:
            report = jarvis_loop.run_gui_loop("play lofi")
            self.assertIn("Step 1: Click on play button — FAILED", report)
            self.assertIn("Outcome: PARTIAL — Step 1 did not verify", report)
            mock_click.assert_called_once_with("play button")

    def test_cap_exactly_five(self):
        """cap = exactly 5 iterations when goal is not confirmed."""
        observations = [
            ToolResult(True, f"NO, step {i} pending.") for i in range(1, 6)
        ]
        choice = json.dumps({"action": "key", "shortcut": "tab"})
        step_result = ToolResult(True, "Shortcut sent.", {"shortcut": "tab"})

        with patch("jarvis_loop.ask_screen", side_effect=observations), \
             patch("jarvis_brain.choose_loop_action", return_value=choice), \
             patch("jarvis_loop.execute_tool", return_value=step_result) as mock_exec:
            report = jarvis_loop.run_gui_loop("play lofi")
            self.assertEqual(mock_exec.call_count, 5)
            self.assertIn("Step 1:", report)
            self.assertIn("Step 5:", report)
            self.assertNotIn("Step 6:", report)
            self.assertIn(
                "Outcome: PARTIAL — I reached the 5-step limit without the goal being confirmed done.",
                report,
            )

    def test_identical_observations_halt_after_one_action(self):
        """identical observations → halt after 1 action."""
        obs = ToolResult(True, "NO, screen still has the login dialog.")
        choice = json.dumps({"action": "key", "shortcut": "enter"})
        step_result = ToolResult(True, "Shortcut sent.", {"shortcut": "enter"})

        # Observation repeated identically
        with patch("jarvis_loop.ask_screen", return_value=obs), \
             patch("jarvis_brain.choose_loop_action", return_value=choice), \
             patch("jarvis_loop.execute_tool", return_value=step_result) as mock_exec:
            report = jarvis_loop.run_gui_loop("press submit")
            self.assertEqual(mock_exec.call_count, 1)
            self.assertIn("Step 1:", report)
            self.assertNotIn("Step 2:", report)
            self.assertIn(
                "Outcome: PARTIAL — The screen stopped changing, so I stopped instead of repeating myself.",
                report,
            )

    def test_chooser_done_without_observation_yes_partial(self):
        """chooser done without observation YES → PARTIAL."""
        obs1 = ToolResult(True, "NO, player loading.")
        done_choice = json.dumps({"done": True, "evidence": "I see the video playing."})
        confirm_obs_no = ToolResult(True, "NO, the player is still buffering.")

        with patch("jarvis_loop.ask_screen", side_effect=[obs1, confirm_obs_no]), \
             patch("jarvis_brain.choose_loop_action", return_value=done_choice):
            report = jarvis_loop.run_gui_loop("play lofi")
            self.assertIn(
                "Outcome: PARTIAL — I thought the goal was done, but the screen does not confirm it.",
                report,
            )

    def test_chooser_done_with_observation_yes_completed(self):
        """chooser done with observation YES → COMPLETED."""
        obs1 = ToolResult(True, "NO, player loading.")
        done_choice = json.dumps({"done": True, "evidence": "I clicked play."})
        confirm_obs_yes = ToolResult(True, "YES, video is currently playing.")

        with patch("jarvis_loop.ask_screen", side_effect=[obs1, confirm_obs_yes]), \
             patch("jarvis_brain.choose_loop_action", return_value=done_choice):
            report = jarvis_loop.run_gui_loop("play lofi")
            self.assertIn(
                "Outcome: COMPLETED — The latest observation confirms the goal is done.",
                report,
            )

    def test_alt_f4_stopped_by_real_policy_execute_tool_never_called(self):
        """alt+f4 → real policy stops it, execute_tool never called."""
        obs = ToolResult(True, "NO, app window is open.")
        choice = json.dumps({"action": "key", "shortcut": "alt+f4"})

        # Use REAL policy evaluation (do NOT mock jarvis_policy.evaluate)
        with patch("jarvis_loop.ask_screen", return_value=obs), \
             patch("jarvis_brain.choose_loop_action", return_value=choice), \
             patch("jarvis_loop.execute_tool") as mock_exec:
            report = jarvis_loop.run_gui_loop("close app")
            self.assertIn("Outcome: PARTIAL", report)
            self.assertIn(
                "Step 1 needs 'send_shortcut', which needs your direct say-so. Please say it as a direct command instead.",
                report,
            )
            mock_exec.assert_not_called()

    def test_stuck_choice_halts_partial(self):
        """Chooser returning stuck halts the loop with PARTIAL."""
        obs = ToolResult(True, "NO, menu closed.")
        choice = json.dumps({"stuck": "The required button is missing."})

        with patch("jarvis_loop.ask_screen", return_value=obs), \
             patch("jarvis_brain.choose_loop_action", return_value=choice):
            report = jarvis_loop.run_gui_loop("play lofi")
            self.assertIn("Outcome: PARTIAL — I got stuck: The required button is missing.", report)

    def test_brain_think_fallback_triggers_gui_loop(self):
        """When multi-step objective deterministic plan fails, think falls back to gui loop if trigger matches."""
        # "play lofi and then click start" - planner fails because unsupported or unplannable,
        # but should_attempt_loop is True because it has 'play'/'click'.
        fake_report = "Goal: play lofi\nOutcome: COMPLETED — The latest observation confirms the goal is done."
        with patch("jarvis_tasks.create_task_from_objective") as mock_create, \
             patch("jarvis_loop.run_gui_loop", return_value=fake_report) as mock_loop:
            failed_task = jarvis_tasks.Task(
                task_id="t1",
                objective="play lofi",
                status=jarvis_tasks.TaskStatus.FAILED,
                error="Could not plan.",
            )
            mock_create.return_value = failed_task
            response = jarvis_brain.think("play lofi and then click start")
            self.assertEqual(response, "Done. The latest observation confirms the goal is done.")
            mock_loop.assert_called_once_with("play lofi and then click start")

    def test_spoken_summary_mappings(self):
        """Test exact spoken_summary mappings for COMPLETED, PARTIAL, FAILED, and empty/garbage."""
        # COMPLETED
        rep_completed = "Goal: test\nOutcome: COMPLETED — The latest observation confirms the goal is done."
        self.assertEqual(
            jarvis_loop.spoken_summary(rep_completed),
            "Done. The latest observation confirms the goal is done.",
        )

        # PARTIAL
        reason_partial = "The screen stopped changing, so I stopped instead of repeating myself."
        rep_partial = f"Goal: test\nOutcome: PARTIAL — {reason_partial}"
        self.assertEqual(
            jarvis_loop.spoken_summary(rep_partial),
            "I couldn't finish that. " + reason_partial,
        )

        # FAILED
        reason_failed = "I couldn't observe the screen: device disconnected"
        rep_failed = f"Goal: test\nOutcome: FAILED — {reason_failed}"
        self.assertEqual(
            jarvis_loop.spoken_summary(rep_failed),
            "That failed. " + reason_failed,
        )

        # garbage / empty
        self.assertEqual(jarvis_loop.spoken_summary(""), "I couldn't finish that.")
        self.assertEqual(jarvis_loop.spoken_summary(None), "I couldn't finish that.")
        self.assertEqual(jarvis_loop.spoken_summary("invalid report text"), "I couldn't finish that.")

    def test_happy_path_click_verified_then_yes_completed(self):
        """§5.2: NO obs -> click VERIFIED -> YES obs -> COMPLETED, step listed."""
        observations = [
            ToolResult(True, "NO - YouTube is open, a lofi video is listed"),
            ToolResult(True, "YES - the video is playing"),
        ]
        click_result = ToolResult(True, "Clicked.", {"changed": True, "changed_percent": 5.0, "change_region": None, "foreground_before": "a", "foreground_after": "b"})
        choice = json.dumps({"action": "click", "target": "lofi video"})
        with patch("jarvis_loop.ask_screen", side_effect=observations), \
             patch("jarvis_brain.choose_loop_action", return_value=choice), \
             patch("jarvis_loop.grounded_click", return_value=click_result) as mock_click:
            report = jarvis_loop.run_gui_loop("open YouTube and play lofi")
            self.assertIn("Step 1: Click on lofi video — VERIFIED", report)
            self.assertIn("Outcome: COMPLETED", report)
            mock_click.assert_called_once_with("lofi video")


if __name__ == "__main__":
    unittest.main()
