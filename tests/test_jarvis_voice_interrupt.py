"""
tests/test_jarvis_voice_interrupt.py

Automated tests for the interruptible TTS architecture (Phase 4 of M2 improvement).

Tests:
1. TTS can be stopped before natural completion.
2. An interruption signal is handled exactly once.
3. A new request can be processed after interruption.
4. Repeated interruption signals do not create duplicate workers.
5. Audio resources are cleaned up after interruption.
6. Pending high-risk approval remains intact when speech is interrupted.
7. Existing voice and task tests continue passing (tested in full suite).
"""

import threading
import time
import unittest
from unittest.mock import MagicMock, call, patch

import jarvis_brain
import jarvis_policy
import jarvis_tasks
import jarvis_voice


def _engine():
    """Return a MagicMock pyttsx3 engine."""
    engine = MagicMock()
    engine.getProperty.return_value = 1.0
    engine.isBusy.return_value = False
    return engine


class TtsStopBeforeNaturalCompletionTest(unittest.TestCase):
    """Test 1: TTS can be stopped before natural completion."""

    def setUp(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()
        jarvis_voice._tts_thread = None

    def tearDown(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()

    def test_request_tts_stop_sets_event_and_calls_engine_stop(self):
        """request_tts_stop() sets _stop_event and calls engine.stop() once."""
        engine = _engine()
        jarvis_voice._tts_engine = engine

        jarvis_voice.request_tts_stop()

        self.assertTrue(jarvis_voice._stop_event.is_set())
        engine.stop.assert_called_once()

    def test_speak_exits_when_stop_event_is_set_during_playback(self):
        """TTS thread exits cleanly when engine.stop() is called concurrently."""
        stop_called = threading.Event()

        def fake_run_and_wait():
            # Simulate blocking TTS; wait until stop is requested.
            stop_called.wait(timeout=2.0)

        engine = _engine()
        engine.runAndWait.side_effect = fake_run_and_wait

        with patch("pyttsx3.init", return_value=engine), patch("time.sleep"), patch("builtins.print"):
            # Speak in a separate thread (as speak() itself does internally)
            tts_done = threading.Event()

            def do_speak():
                jarvis_voice.speak("This is a very long response that should be interrupted.")
                tts_done.set()

            t = threading.Thread(target=do_speak)
            t.start()

            # Wait briefly then trigger stop
            time.sleep(0.05)
            stop_called.set()  # Unblocks fake runAndWait
            jarvis_voice.request_tts_stop()

            tts_done.wait(timeout=3.0)
            t.join(timeout=3.0)

        self.assertTrue(tts_done.is_set(), "speak() must return after stop")
        self.assertFalse(jarvis_voice._tts_lock, "_tts_lock must be released")
        self.assertIsNone(jarvis_voice._tts_thread, "_tts_thread must be cleared")


class InterruptionSignalExactlyOnceTest(unittest.TestCase):
    """Test 2: An interruption signal is handled exactly once."""

    def setUp(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()
        jarvis_voice._tts_thread = None

    def tearDown(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()

    def test_stop_event_is_cleared_before_each_speak_call(self):
        """_stop_event is cleared at the beginning of every speak() call."""
        engine = _engine()
        jarvis_voice._stop_event.set()  # Pre-set from a previous interaction

        with patch("pyttsx3.init", return_value=engine), patch("time.sleep"), patch("builtins.print"):
            jarvis_voice.speak("Hello world")

        # The event should have been cleared at the start of speak()
        self.assertFalse(jarvis_voice._stop_event.is_set())
        engine.say.assert_called_once_with("Hello world")
        engine.runAndWait.assert_called_once()

    def test_second_speak_call_is_not_blocked_by_stale_tts_lock(self):
        """_tts_lock is released after each speak(), enabling the next call."""
        engine = _engine()

        with patch("pyttsx3.init", return_value=engine), patch("time.sleep"), patch("builtins.print"):
            jarvis_voice.speak("First response")
            jarvis_voice.speak("Second response")

        self.assertEqual(engine.say.call_count, 2)
        self.assertFalse(jarvis_voice._tts_lock)


class NewRequestAfterInterruptionTest(unittest.TestCase):
    """Test 3: A new request can be processed after interruption."""

    def setUp(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()
        jarvis_voice._tts_thread = None

    def tearDown(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()

    def test_speak_interruptible_returns_follow_up_command(self):
        """speak_interruptible returns a non-stop captured utterance for processing."""
        # Make runAndWait block until the stop event is set so TTS stays alive
        # long enough for _listen_for_interrupt to fire.
        can_finish = threading.Event()
        engine = _engine()
        def blocking_run_and_wait():
            can_finish.wait(timeout=3.0)
        engine.runAndWait.side_effect = blocking_run_and_wait

        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        main_recognizer = MagicMock()

        mock_poll = MagicMock()
        mock_poll.listen.return_value = object()
        mock_poll.recognize_google.return_value = "what time is it"
        mock_poll.energy_threshold = 300
        mock_poll.pause_threshold = 0.8
        mock_poll.non_speaking_duration = 0.4
        mock_poll.operation_timeout = 15
        mock_poll.phrase_threshold = 0.3
        mock_poll.dynamic_energy_threshold = False

        # Patch jarvis_voice.sr.Recognizer (the module-level alias used inside
        # _listen_for_interrupt) so the poll recognizer is our mock.
        with patch("pyttsx3.init", return_value=engine), \
             patch("time.sleep"), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=mock_poll):

            # Unblock runAndWait when the stop event fires (simulating engine.stop())
            def unblock_on_stop():
                jarvis_voice._stop_event.wait(timeout=3.0)
                can_finish.set()
            unblock = threading.Thread(target=unblock_on_stop, daemon=True)
            unblock.start()

            result = jarvis_voice.speak_interruptible(
                "A very long answer about Python and Java...",
                main_recognizer,
                mic,
            )

        self.assertEqual(result, "what time is it")
        self.assertFalse(jarvis_voice._tts_lock)

    def test_stop_phrase_returns_none_not_the_command(self):
        """speak_interruptible returns None when a stop-only phrase is heard."""
        can_finish = threading.Event()
        engine = _engine()
        def blocking_run_and_wait():
            can_finish.wait(timeout=3.0)
        engine.runAndWait.side_effect = blocking_run_and_wait

        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        mock_poll = MagicMock()
        mock_poll.listen.return_value = object()
        mock_poll.recognize_google.return_value = "Jarvis stop"
        mock_poll.energy_threshold = 300
        mock_poll.pause_threshold = 0.8
        mock_poll.non_speaking_duration = 0.4
        mock_poll.operation_timeout = 15
        mock_poll.phrase_threshold = 0.3
        mock_poll.dynamic_energy_threshold = False

        with patch("pyttsx3.init", return_value=engine), \
             patch("time.sleep"), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=mock_poll):

            def unblock_on_stop():
                jarvis_voice._stop_event.wait(timeout=3.0)
                can_finish.set()
            unblock = threading.Thread(target=unblock_on_stop, daemon=True)
            unblock.start()

            result = jarvis_voice.speak_interruptible(
                "This long response should be stopped.",
                MagicMock(),
                mic,
            )

        self.assertIsNone(result)
        self.assertFalse(jarvis_voice._tts_lock)


class NoDuplicateWorkersTest(unittest.TestCase):
    """Test 4: Repeated interruption signals do not create duplicate workers."""

    def setUp(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()
        jarvis_voice._tts_thread = None

    def tearDown(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()

    def test_overlapping_speak_calls_skip_second(self):
        """A second speak() call while TTS is active is skipped, not queued."""
        engine = _engine()
        started = threading.Event()
        can_finish = threading.Event()

        def slow_run_and_wait():
            started.set()
            can_finish.wait(timeout=2.0)

        engine.runAndWait.side_effect = slow_run_and_wait
        printed = []

        with patch("pyttsx3.init", return_value=engine), \
             patch("time.sleep"), \
             patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):

            t1 = threading.Thread(target=jarvis_voice.speak, args=("First response",))
            t1.start()
            started.wait(timeout=1.0)

            # Call speak() while TTS is already running
            jarvis_voice.speak("Second response — should be skipped")

            can_finish.set()
            t1.join(timeout=3.0)

        skipped_msgs = [m for m in printed if "skipped overlapping" in m]
        self.assertEqual(len(skipped_msgs), 1, "Second speak() should be skipped exactly once")
        # First response should complete normally
        engine.runAndWait.assert_called_once()

    def test_request_tts_stop_is_safe_with_no_active_tts(self):
        """request_tts_stop() is a no-op and raises nothing when TTS is not active."""
        jarvis_voice._tts_engine = None
        try:
            jarvis_voice.request_tts_stop()
        except Exception as exc:
            self.fail(f"request_tts_stop() raised {exc} with no active TTS")
        self.assertTrue(jarvis_voice._stop_event.is_set())

    def test_is_tts_active_false_when_no_thread(self):
        jarvis_voice._tts_thread = None
        self.assertFalse(jarvis_voice.is_tts_active())

    def test_is_tts_active_false_after_thread_finishes(self):
        engine = _engine()
        with patch("pyttsx3.init", return_value=engine), patch("time.sleep"), patch("builtins.print"):
            jarvis_voice.speak("Hello")
        self.assertFalse(jarvis_voice.is_tts_active())
        self.assertIsNone(jarvis_voice._tts_thread)


class AudioResourceCleanupTest(unittest.TestCase):
    """Test 5: Audio resources are cleaned up correctly."""

    def setUp(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()
        jarvis_voice._tts_thread = None

    def tearDown(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()

    def test_tts_lock_released_after_normal_completion(self):
        engine = _engine()
        with patch("pyttsx3.init", return_value=engine), patch("time.sleep"), patch("builtins.print"):
            jarvis_voice.speak("Normal response")
        self.assertFalse(jarvis_voice._tts_lock)

    def test_tts_lock_released_after_engine_exception(self):
        engine = _engine()
        engine.runAndWait.side_effect = RuntimeError("engine crashed")
        with patch("pyttsx3.init", return_value=engine), patch("time.sleep"), patch("builtins.print"):
            jarvis_voice.speak("Broken response")
        self.assertFalse(jarvis_voice._tts_lock)

    def test_tts_engine_discarded_after_completion(self):
        engine = _engine()
        with patch("pyttsx3.init", return_value=engine), patch("time.sleep"), patch("builtins.print"):
            jarvis_voice.speak("Response")
        self.assertIsNone(jarvis_voice._tts_engine)

    def test_tts_thread_reference_cleared_after_completion(self):
        engine = _engine()
        with patch("pyttsx3.init", return_value=engine), patch("time.sleep"), patch("builtins.print"):
            jarvis_voice.speak("Response")
        self.assertIsNone(jarvis_voice._tts_thread)


class PendingApprovalPreservedOnInterruptTest(unittest.TestCase):
    """Test 6: Pending high-risk approval remains intact when speech is interrupted."""

    def setUp(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()
        jarvis_voice._tts_thread = None
        jarvis_policy.clear_pending_approval()
        jarvis_tasks.task_manager.clear_active_task()

    def tearDown(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False
        jarvis_voice._stop_event.clear()
        jarvis_policy.clear_pending_approval()
        jarvis_tasks.task_manager.clear_active_task()

    def test_request_tts_stop_does_not_clear_pending_approval(self):
        """Calling request_tts_stop() must not touch pending approval state."""
        jarvis_policy.set_pending_approval(
            tool_name="close_application",
            arguments={"name": "notepad"},
            description="Close Notepad",
        )
        self.assertTrue(jarvis_policy.has_pending_approval())

        jarvis_voice.request_tts_stop()

        # Approval must still be pending
        self.assertTrue(jarvis_policy.has_pending_approval())
        pending = jarvis_policy.get_pending_approval()
        self.assertEqual(pending.tool_name, "close_application")

    def test_speak_interruptible_stop_phrase_does_not_clear_pending_approval(self):
        """
        Saying 'stop' while TTS plays its approval prompt must not clear
        the pending approval — the user must still be able to say 'yes' afterward.
        """
        jarvis_policy.set_pending_approval(
            tool_name="close_application",
            arguments={"name": "notepad"},
            description="Close Notepad",
        )
        engine = _engine()
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        with patch("pyttsx3.init", return_value=engine), \
             patch("time.sleep"), \
             patch("builtins.print"), \
             patch("speech_recognition.Recognizer") as mock_sr_recognizer:
            mock_poll = MagicMock()
            mock_poll.listen.return_value = object()
            mock_poll.recognize_google.return_value = "Jarvis stop"
            mock_poll.energy_threshold = 300
            mock_sr_recognizer.return_value = mock_poll

            result = jarvis_voice.speak_interruptible(
                "Action 'close_application' requires your confirmation before execution. "
                "Please say yes to proceed or no to cancel.",
                MagicMock(),
                mic,
            )

        self.assertIsNone(result)
        # Pending approval must be intact after speech interruption
        self.assertTrue(jarvis_policy.has_pending_approval())
        pending = jarvis_policy.get_pending_approval()
        self.assertEqual(pending.tool_name, "close_application")

    def test_yes_after_interrupted_approval_prompt_executes_action(self):
        """
        Saying 'yes' after the approval prompt was interrupted still executes
        the pending high-risk action correctly via jarvis_brain.think().
        """
        from unittest.mock import patch as up
        jarvis_policy.set_pending_approval(
            tool_name="close_application",
            arguments={"name": "notepad"},
            description="Close Notepad",
        )

        fake_result = MagicMock()
        fake_result.ok = True
        fake_result.message = "Closed notepad successfully."

        with up("jarvis_tools.execute_tool", return_value=fake_result):
            resp = jarvis_brain.think("yes")

        self.assertFalse(jarvis_policy.has_pending_approval())
        self.assertIn("Closed notepad", resp)


if __name__ == "__main__":
    unittest.main()
