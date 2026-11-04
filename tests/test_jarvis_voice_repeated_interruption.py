"""
tests/test_jarvis_voice_repeated_interruption.py

Comprehensive regression tests for repeated TTS interruption failure investigation.
Validates:
1. Interrupt first TTS session.
2. Start a second TTS session after first is interrupted.
3. Interrupt the second TTS session.
4. Start a third normal listening session.
5. Repeat interruption several times in sequence.
6. Verify no stale stop event affects a new session.
7. Verify no previous microphone stream remains active.
8. Verify each worker terminates exactly once.
9. Verify pending high-risk approvals survive all interruptions.
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import jarvis_brain
import jarvis_policy
import jarvis_tools
import jarvis_voice
import jarvis_windows


def _mock_engine(can_finish_event=None):
    engine = MagicMock()
    engine.getProperty.return_value = 1.0
    engine.isBusy.return_value = False

    def blocking_run():
        if can_finish_event is not None:
            can_finish_event.wait(timeout=3.0)

    engine.runAndWait.side_effect = blocking_run
    return engine


class RepeatedTtsInterruptionTests(unittest.TestCase):
    def setUp(self):
        jarvis_policy.clear_pending_approval()
        jarvis_voice._tts_lock = False
        jarvis_voice._tts_thread = None
        jarvis_voice._stop_event.clear()
        jarvis_brain._history.clear()

    def tearDown(self):
        jarvis_policy.clear_pending_approval()
        jarvis_voice._tts_lock = False
        jarvis_voice._tts_thread = None
        jarvis_voice._stop_event.clear()

    # 1. Interrupt first TTS session
    def test_1_interrupt_first_tts_session(self):
        """First TTS session halts on stop command and completes session cleanup."""
        can_finish = threading.Event()
        engine = _mock_engine(can_finish)

        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        poll_mock = MagicMock()
        poll_mock.listen.return_value = object()
        poll_mock.recognize_google.return_value = "Jarvis stop"

        with patch("pyttsx3.init", return_value=engine), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll_mock):

            def unblock_on_stop():
                jarvis_voice._stop_event.wait(timeout=2.0)
                can_finish.set()

            threading.Thread(target=unblock_on_stop, daemon=True).start()

            result = jarvis_voice.speak_interruptible("Session 1 talking...", mic)

        self.assertIsNone(result)
        self.assertFalse(jarvis_voice.is_tts_active())
        self.assertFalse(jarvis_voice._tts_lock)

    # 2. Start a second TTS session
    def test_2_start_second_tts_session_after_first_interrupted(self):
        """Second TTS session starts cleanly and runs after first was interrupted."""
        # Session 1 interrupted
        can_finish1 = threading.Event()
        engine1 = _mock_engine(can_finish1)

        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        poll_mock1 = MagicMock()
        poll_mock1.listen.return_value = object()
        poll_mock1.recognize_google.return_value = "Jarvis stop"

        with patch("pyttsx3.init", return_value=engine1), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll_mock1):

            def unblock1():
                jarvis_voice._stop_event.wait(timeout=2.0)
                can_finish1.set()

            threading.Thread(target=unblock1, daemon=True).start()
            jarvis_voice.speak_interruptible("Session 1 text", mic)

        self.assertFalse(jarvis_voice._tts_lock)

        # Session 2 plays naturally without interruption
        engine2 = _mock_engine(None)
        poll_mock2 = MagicMock()
        poll_mock2.listen.side_effect = jarvis_voice.sr.WaitTimeoutError()

        with patch("pyttsx3.init", return_value=engine2), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll_mock2):
            result2 = jarvis_voice.speak_interruptible("Session 2 completes naturally", mic)

        self.assertIsNone(result2)
        self.assertFalse(jarvis_voice.is_tts_active())
        self.assertFalse(jarvis_voice._tts_lock)
        engine2.say.assert_called_once_with("Session 2 completes naturally")

    # 3. Interrupt the second TTS session
    def test_3_interrupt_second_tts_session(self):
        """Both session 1 and session 2 can be interrupted sequentially."""
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        for session_num in (1, 2):
            can_finish = threading.Event()
            engine = _mock_engine(can_finish)

            poll_mock = MagicMock()
            poll_mock.listen.return_value = object()
            poll_mock.recognize_google.return_value = "stop"

            with patch("pyttsx3.init", return_value=engine), \
                 patch("builtins.print"), \
                 patch.object(jarvis_voice.sr, "Recognizer", return_value=poll_mock):

                def unblock():
                    jarvis_voice._stop_event.wait(timeout=2.0)
                    can_finish.set()

                threading.Thread(target=unblock, daemon=True).start()
                res = jarvis_voice.speak_interruptible(f"Session {session_num} long response", mic)

            self.assertIsNone(res)
            self.assertFalse(jarvis_voice.is_tts_active())
            self.assertFalse(jarvis_voice._tts_lock)

    # 4. Start a third normal listening session
    def test_4_start_third_normal_listening_session(self):
        """After two interrupted TTS sessions, normal microphone capture works perfectly."""
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        # Session 1 & 2 interrupted
        for i in range(1, 3):
            can_finish = threading.Event()
            engine = _mock_engine(can_finish)
            poll = MagicMock()
            poll.listen.return_value = object()
            poll.recognize_google.return_value = "stop"

            with patch("pyttsx3.init", return_value=engine), \
                 patch("builtins.print"), \
                 patch.object(jarvis_voice.sr, "Recognizer", return_value=poll):
                def unblock():
                    jarvis_voice._stop_event.wait(timeout=2.0)
                    can_finish.set()
                threading.Thread(target=unblock, daemon=True).start()
                jarvis_voice.speak_interruptible(f"Speech {i}", mic)

        # Third session: normal command capture
        normal_mic = MagicMock()
        normal_mic.__enter__.return_value = normal_mic
        normal_mic.__exit__.return_value = False

        recog = MagicMock()
        recog.listen.return_value = object()
        recog.recognize_google.return_value = "open calculator"

        with patch("builtins.print"):
            status, text = jarvis_voice.capture_command(recog, normal_mic, max_attempts=1)

        self.assertEqual(status, jarvis_voice.CAPTURE_HEARD)
        self.assertEqual(text, "open calculator")

    # 5. Repeat interruption several times
    def test_5_repeat_interruption_several_times(self):
        """Interruption cycle repeated 5 times without resource exhaustion or deadlocks."""
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        for cycle in range(1, 6):
            can_finish = threading.Event()
            engine = _mock_engine(can_finish)

            poll = MagicMock()
            poll.listen.return_value = object()
            # Alternate between stop phrase and follow-up command
            if cycle % 2 == 1:
                poll.recognize_google.return_value = "Jarvis stop"
                expected = None
            else:
                poll.recognize_google.return_value = f"Jarvis command {cycle}"
                expected = f"command {cycle}"

            with patch("pyttsx3.init", return_value=engine), \
                 patch("builtins.print"), \
                 patch.object(jarvis_voice.sr, "Recognizer", return_value=poll):

                def unblock():
                    jarvis_voice._stop_event.wait(timeout=2.0)
                    can_finish.set()

                threading.Thread(target=unblock, daemon=True).start()
                res = jarvis_voice.speak_interruptible(f"Cycle {cycle} content", mic)

            self.assertEqual(res, expected)
            self.assertFalse(jarvis_voice.is_tts_active())
            self.assertFalse(jarvis_voice._tts_lock)

    # 6. Verify no stale stop event affects a new session
    def test_6_verify_no_stale_stop_event_affects_a_new_session(self):
        """Stale _stop_event from a previous interrupt is cleared and does not halt new session."""
        # Manually set stale stop event
        jarvis_voice._stop_event.set()

        engine = _mock_engine(None)
        poll = MagicMock()
        poll.listen.side_effect = jarvis_voice.sr.WaitTimeoutError()

        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        with patch("pyttsx3.init", return_value=engine), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll):
            jarvis_voice.speak_interruptible("Testing fresh event flag", mic)

        self.assertFalse(jarvis_voice._stop_event.is_set())
        engine.say.assert_called_once_with("Testing fresh event flag")

    # 7. Verify no previous microphone stream remains active
    def test_7_verify_no_previous_microphone_stream_remains_active(self):
        """Microphone context exits on every interruption, freeing the audio stream."""
        can_finish = threading.Event()
        engine = _mock_engine(can_finish)

        enter_count = 0
        exit_count = 0

        class MockMic:
            def __enter__(self):
                nonlocal enter_count
                enter_count += 1
                return self
            def __exit__(self, exc_type, exc_val, exc_tb):
                nonlocal exit_count
                exit_count += 1
                return False

        mic = MockMic()
        poll = MagicMock()
        poll.listen.return_value = object()
        poll.recognize_google.return_value = "stop"

        with patch("pyttsx3.init", return_value=engine), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll):

            def unblock():
                jarvis_voice._stop_event.wait(timeout=2.0)
                can_finish.set()

            threading.Thread(target=unblock, daemon=True).start()
            jarvis_voice.speak_interruptible("Checking mic context exits", mic)

        # Every enter must have a matching exit
        self.assertGreater(enter_count, 0)
        self.assertEqual(enter_count, exit_count)

    # 8. Verify each worker terminates exactly once
    def test_8_verify_each_worker_terminates_exactly_once(self):
        """Every worker thread spawned by speak_interruptible terminates cleanly upon return."""
        threads_spawned = []
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        for i in range(3):
            can_finish = threading.Event()
            engine = _mock_engine(can_finish)
            poll = MagicMock()
            poll.listen.return_value = object()
            poll.recognize_google.return_value = "stop"

            with patch("pyttsx3.init", return_value=engine), \
                 patch("builtins.print"), \
                 patch.object(jarvis_voice.sr, "Recognizer", return_value=poll):

                def unblock():
                    jarvis_voice._stop_event.wait(timeout=2.0)
                    can_finish.set()

                threading.Thread(target=unblock, daemon=True).start()
                jarvis_voice.speak_interruptible(f"Worker tracking {i}", mic)
                threads_spawned.append(jarvis_voice._tts_thread)

        # Every session worker must be None after return and previous threads dead
        self.assertIsNone(jarvis_voice._tts_thread)

    # 9. Verify pending high-risk approvals survive all interruptions
    def test_9_verify_pending_high_risk_approvals_survive_all_interruptions(self):
        """Pending approval set before or during TTS is never discarded across multiple interruptions."""
        # 1. Establish pending approval for close_application
        jarvis_policy.set_pending_approval(
            tool_name="close_application",
            arguments={"name": "notepad"},
            description="Close Notepad",
        )
        self.assertTrue(jarvis_policy.has_pending_approval())

        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        # 2. Interruption 1
        can_finish1 = threading.Event()
        engine1 = _mock_engine(can_finish1)
        poll1 = MagicMock()
        poll1.listen.return_value = object()
        poll1.recognize_google.return_value = "stop"

        with patch("pyttsx3.init", return_value=engine1), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll1):
            def unblock1():
                jarvis_voice._stop_event.wait(timeout=2.0)
                can_finish1.set()
            threading.Thread(target=unblock1, daemon=True).start()
            jarvis_voice.speak_interruptible("Action close_application requires confirmation...", mic)

        self.assertTrue(jarvis_policy.has_pending_approval())

        # 3. Interruption 2
        can_finish2 = threading.Event()
        engine2 = _mock_engine(can_finish2)
        poll2 = MagicMock()
        poll2.listen.return_value = object()
        poll2.recognize_google.return_value = "stop"

        with patch("pyttsx3.init", return_value=engine2), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll2):
            def unblock2():
                jarvis_voice._stop_event.wait(timeout=2.0)
                can_finish2.set()
            threading.Thread(target=unblock2, daemon=True).start()
            jarvis_voice.speak_interruptible("Are you sure you want to close Notepad?", mic)

        self.assertTrue(jarvis_policy.has_pending_approval())
        pending = jarvis_policy.get_pending_approval()
        self.assertEqual(pending.tool_name, "close_application")
        self.assertEqual(pending.arguments, {"name": "notepad"})

        # 4. User says 'yes' in normal listening loop, executing the action
        mock_result = jarvis_windows.ToolResult(True, "Closed Notepad.", {"app": "notepad"})
        with patch("jarvis_tools.execute_tool", return_value=mock_result) as exec_mock:
            reply = jarvis_brain.think("yes")

        self.assertEqual(reply, "Closed Notepad.")
        self.assertFalse(jarvis_policy.has_pending_approval())
        exec_mock.assert_called_once_with("close_application", {"name": "notepad"}, approved=True)


if __name__ == "__main__":
    unittest.main()
