"""
Unit tests for voice recognition reliability and concise action responses.
Verifies all 9 requirements from Jarvis Problem A and Problem B.
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


class VoiceReliabilityAndConciseResponseTests(unittest.TestCase):
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

    # 1. Only one active microphone capture consumer
    def test_only_one_active_microphone_capture_consumer(self):
        """Microphone capture is owned exclusively; interrupt mic is distinct and only active during TTS."""
        main_mic = MagicMock()
        main_mic.__enter__.return_value = main_mic
        main_mic.__exit__.return_value = False

        recog = MagicMock()
        recog.listen.return_value = object()
        recog.recognize_google.return_value = "hello"

        # While capture_command is executing, TTS is not active
        self.assertFalse(jarvis_voice.is_tts_active())

        status, text = jarvis_voice.capture_command(recog, main_mic, max_attempts=1)
        self.assertEqual(status, jarvis_voice.CAPTURE_HEARD)
        self.assertEqual(text, "hello")
        main_mic.__enter__.assert_called_once()

    # 2. Normal recognition after TTS completion
    def test_normal_recognition_after_tts_completion(self):
        """After TTS completes normally, microphone capture runs cleanly without blocked locks."""
        engine = MagicMock()
        with patch("pyttsx3.init", return_value=engine), patch("builtins.print"):
            jarvis_voice.speak("Task completed.")

        self.assertFalse(jarvis_voice.is_tts_active())
        self.assertFalse(jarvis_voice._tts_lock)

        # Subsequent microphone capture succeeds immediately
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        recog = MagicMock()
        recog.listen.return_value = object()
        recog.recognize_google.return_value = "open notepad"

        status, text = jarvis_voice.capture_command(recog, mic, max_attempts=1)
        self.assertEqual(status, jarvis_voice.CAPTURE_HEARD)
        self.assertEqual(text, "open notepad")

    # 3. Normal recognition after TTS interruption
    def test_normal_recognition_after_tts_interruption(self):
        """Interrupted TTS halts cleanly and subsequent listening loop captures the next command."""
        engine = MagicMock()
        can_finish = threading.Event()

        def blocking_run():
            can_finish.wait(timeout=3.0)

        engine.runAndWait.side_effect = blocking_run

        inter_mic = MagicMock()
        inter_mic.__enter__.return_value = inter_mic
        inter_mic.__exit__.return_value = False

        poll_mock = MagicMock()
        poll_mock.listen.return_value = object()
        poll_mock.recognize_google.return_value = "stop"

        with patch("pyttsx3.init", return_value=engine), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll_mock):
            def trigger_unblock():
                jarvis_voice._stop_event.wait(timeout=3.0)
                can_finish.set()

            unblock = threading.Thread(target=trigger_unblock, daemon=True)
            unblock.start()

            res = jarvis_voice.speak_interruptible("Long speech...", inter_mic)

        self.assertIsNone(res)
        self.assertFalse(jarvis_voice.is_tts_active())
        self.assertFalse(jarvis_voice._tts_lock)

        # Now regular capture proceeds normally
        main_mic = MagicMock()
        main_mic.__enter__.return_value = main_mic
        main_mic.__exit__.return_value = False

        main_recog = MagicMock()
        main_recog.listen.return_value = object()
        main_recog.recognize_google.return_value = "open calculator"

        status, text = jarvis_voice.capture_command(main_recog, main_mic, max_attempts=1)
        self.assertEqual(status, jarvis_voice.CAPTURE_HEARD)
        self.assertEqual(text, "open calculator")

    # 4. Recognition timeout recovery
    def test_recognition_timeout_recovery(self):
        """WaitTimeoutError cleanly returns CAPTURE_IDLE without crashing or leaving open streams."""
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        recog = MagicMock()
        recog.listen.side_effect = jarvis_voice.sr.WaitTimeoutError()

        status, text = jarvis_voice.capture_command(recog, mic, max_attempts=1)
        self.assertEqual(status, jarvis_voice.CAPTURE_IDLE)
        self.assertIsNone(text)

    # 5. No duplicate listeners after repeated retries
    def test_no_duplicate_listeners_after_repeated_retries(self):
        """When speech is unclear, retries occur sequentially without creating concurrent listeners."""
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        recog = MagicMock()
        # Fail attempt 1 with unclear audio, succeed attempt 2
        recog.listen.return_value = object()
        recog.recognize_google.side_effect = [jarvis_voice.sr.UnknownValueError(), "hello jarvis"]

        status, text = jarvis_voice.capture_command(recog, mic, max_attempts=2)
        self.assertEqual(status, jarvis_voice.CAPTURE_HEARD)
        self.assertEqual(text, "hello jarvis")
        self.assertEqual(recog.listen.call_count, 2)

    # 6. Concise application-launch responses
    def test_concise_application_launch_responses(self):
        """Application launches return deterministic concise responses like 'Opened Notepad.'."""
        app = jarvis_windows.AppInfo("Notepad", r"C:\Windows\System32\notepad.exe", "PATH")
        mock_proc = MagicMock(pid=1001)
        with patch("jarvis_windows.discover_applications", return_value=[app]), \
             patch("jarvis_windows.subprocess.Popen", return_value=mock_proc), \
             patch("jarvis_windows.wait_for_window", return_value=jarvis_windows.ToolResult(
                 True, "Found.", {"pid": 1002, "process": "Notepad.exe"}
             )):
            reply = jarvis_brain.think("open notepad")
        self.assertEqual(reply, "Opened Notepad; window observed.")

        calc = jarvis_windows.AppInfo("Calculator", r"C:\Windows\System32\calc.exe", "PATH")
        with patch("jarvis_windows.discover_applications", return_value=[calc]), \
             patch("jarvis_windows.subprocess.Popen", return_value=mock_proc), \
             patch("jarvis_windows.wait_for_window", return_value=jarvis_windows.ToolResult(
                 True, "Found.", {"pid": 1003, "process": "CalculatorApp.exe"}
             )):
            reply_calc = jarvis_brain.think("open calculator")
        self.assertEqual(reply_calc, "Opened Calculator; window observed.")

        # Website launch concise response
        with patch("ctypes.windll.shell32.ShellExecuteW", return_value=42):
            reply_yt = jarvis_brain.think("open youtube")
        self.assertEqual(reply_yt, "Opened YouTube.")

    # 7. Concise application-close responses
    def test_concise_application_close_responses(self):
        """Approved application close returns deterministic concise response 'Closed Notepad.'."""
        app = jarvis_windows.AppInfo("Notepad", r"C:\Windows\System32\notepad.exe", "PATH")
        mock_kill = MagicMock(returncode=0, stderr="")
        with patch("jarvis_windows.discover_applications", return_value=[app]), \
             patch("subprocess.run", return_value=mock_kill):
            # 1. Initial request triggers high-risk approval prompt
            initial = jarvis_brain.think("close notepad")
            self.assertIn("close_application", initial)
            self.assertTrue(jarvis_policy.has_pending_approval())

            # 2. Confirming 'yes' executes and returns concise response
            confirmed = jarvis_brain.think("yes")
            self.assertEqual(confirmed, "Closed Notepad.")
            self.assertFalse(jarvis_policy.has_pending_approval())

    # 8. Informational answers remain conversational
    def test_informational_answers_remain_conversational(self):
        """Informational queries retain full conversational answers from AI brain."""
        class _FakeCompletions:
            def create(self, **kwargs):
                content = "Quantum computing uses quantum bits or qubits to perform calculations."
                msg = type("Msg", (), {"content": content, "tool_calls": None})()
                choice = type("Choice", (), {"message": msg})()
                return type("Resp", (), {"choices": [choice]})()

        jarvis_brain._client = type("Client", (), {"chat": type("Chat", (), {"completions": _FakeCompletions()})()})()
        with patch("jarvis_brain._get_api_key", return_value="test-key"), \
             patch("jarvis_brain._relevant_memory_context", return_value=""):
            reply = jarvis_brain.think("Explain quantum computing in one sentence.")
        self.assertEqual(reply, "Quantum computing uses quantum bits or qubits to perform calculations.")

    # 9. Approval prompts remain intact
    def test_approval_prompts_remain_intact(self):
        """High-risk action prompts require explicit confirmation before execution."""
        app = jarvis_windows.AppInfo("Notepad", r"C:\Windows\System32\notepad.exe", "PATH")
        with patch("jarvis_windows.discover_applications", return_value=[app]):
            prompt = jarvis_brain.think("close notepad")
        self.assertEqual(prompt, "Action 'close_application' requires your confirmation before execution.")
        self.assertTrue(jarvis_policy.has_pending_approval())

    # 10. No phantom microphone stream release log
    def test_no_phantom_mic_release_log_in_speak_interruptible(self):
        """speak_interruptible() cleanup does not emit phantom 'Microphone stream released' logs."""
        engine = MagicMock()
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        printed_messages = []
        def _capture_print(*args, **kwargs):
            printed_messages.append(" ".join(str(a) for a in args))

        with patch("pyttsx3.init", return_value=engine), \
             patch("builtins.print", side_effect=_capture_print), \
             patch("jarvis_voice._listen_for_interrupt", return_value=None):
            jarvis_voice.speak_interruptible("Testing no phantom release", mic)

        # Ensure no "[Session ...]: Microphone stream released" is printed by speak_interruptible()
        session_release_logs = [
            m for m in printed_messages
            if "Microphone stream released" in m and "Session" in m
        ]
        self.assertEqual(session_release_logs, [])

    # 11. TTS worker completes synchronously with audible playback
    def test_tts_worker_completes_synchronously_with_audible_playback(self):
        """_run_tts_background finishes immediately when playback returns, without artificial 400ms sleep."""
        engine = MagicMock()
        def _fast_say_and_wait():
            pass
        engine.runAndWait.side_effect = _fast_say_and_wait

        with patch("pyttsx3.init", return_value=engine), patch("builtins.print"):
            t0 = time.monotonic()
            jarvis_voice._run_tts_background("Testing immediate completion")
            elapsed = time.monotonic() - t0

        # With 0.0 post-speak settle delay, execution must be immediate (< 0.25s)
        self.assertLess(elapsed, 0.25)
        self.assertFalse(jarvis_voice._tts_lock)

    # 12. Quiet by default console output and debug preservation
    def test_quiet_by_default_console_output_and_debug_preservation(self):
        """Detailed diagnostics are suppressed from console by default, but preserved via logger / JARVIS_DEBUG."""
        engine = MagicMock()
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        printed_messages = []
        def _capture_print(*args, **kwargs):
            printed_messages.append(" ".join(str(a) for a in args))

        # Default mode (quiet)
        with patch.object(jarvis_voice, "JARVIS_DEBUG", False), \
             patch("pyttsx3.init", return_value=engine), \
             patch("builtins.print", side_effect=_capture_print), \
             patch("jarvis_voice._listen_for_interrupt", return_value=None):
            jarvis_voice.speak_interruptible("Testing quiet console output", mic)

        # In quiet mode, console shows only Jarvis response, no session/worker/mic diagnostics
        noisy_terms = ["[Session", "TTS worker", "Processing", "Microphone stream", "Interrupt listener"]
        for msg in printed_messages:
            for term in noisy_terms:
                self.assertNotIn(term, msg)

        # Debug mode (JARVIS_DEBUG=True)
        debug_printed = []
        with patch.object(jarvis_voice, "JARVIS_DEBUG", True), \
             patch("pyttsx3.init", return_value=engine), \
             patch("builtins.print", side_effect=lambda *a, **k: debug_printed.append(" ".join(str(x) for x in a))), \
             patch("jarvis_voice._listen_for_interrupt", return_value=None):
            jarvis_voice.speak_interruptible("Testing debug console output", mic)

        # In debug mode, session lifecycle diagnostics are present
        self.assertTrue(any("[Session" in m for m in debug_printed))


if __name__ == "__main__":
    unittest.main()
