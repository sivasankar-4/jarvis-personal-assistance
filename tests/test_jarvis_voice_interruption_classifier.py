"""
tests/test_jarvis_voice_interruption_classifier.py

Comprehensive tests for interruption intent classification and false-positive reduction.
Validates all 10 requirements:
1. Explicit stop phrases.
2. Wake word without stop intent.
3. Incomplete transcripts.
4. Unrelated conversational speech.
5. Background/noise recognition.
6. Recognition variations ("Jarvis top").
7. A real actionable command during TTS.
8. Repeated interruptions across consecutive sessions.
9. Pending high-risk approval preservation.
10. Lifecycle cleanup after ignored speech.
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


class InterruptionClassifierTests(unittest.TestCase):
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

    # 1. Explicit stop phrases
    def test_1_explicit_stop_phrases(self):
        """Explicit stop phrases classify as STOP."""
        stop_phrases = [
            "Jarvis stop",
            "Stop",
            "stop",
            "Be quiet",
            "be quiet",
            "shut up",
            "quiet please",
            "please stop",
            "stop talking",
            "stop speaking",
            "cancel",
            "that's enough",
            "hey jarvis stop",
        ]
        for phrase in stop_phrases:
            with self.subTest(phrase=phrase):
                decision, cmd = jarvis_voice.classify_interruption(phrase)
                self.assertEqual(decision, jarvis_voice.INTERRUPT_STOP, f"Failed for '{phrase}'")
                self.assertIsNone(cmd)

    def test_1b_messy_stop_tokens(self):
        """Messy endpointing still detects whole-word stop intent."""
        stop_phrases = [
            "make a stop stop",
            "stop stop stop stop stop stop stop stop stopstop stop hey",
            "make it stop",
            "cancel cancel",
        ]
        for phrase in stop_phrases:
            with self.subTest(phrase=phrase):
                decision, cmd = jarvis_voice.classify_interruption(phrase)
                self.assertEqual(decision, jarvis_voice.INTERRUPT_STOP, f"Failed for '{phrase}'")
                self.assertIsNone(cmd)

        unrelated = [
            "stopwatch is a useful app",
            "i was talking about the bus stop yesterday",
            "the stopwatch stopped working yesterday at the bus stop",
            "how are you",
        ]
        for phrase in unrelated:
            with self.subTest(phrase=phrase):
                decision, cmd = jarvis_voice.classify_interruption(phrase)
                self.assertEqual(decision, jarvis_voice.INTERRUPT_IGNORE, f"Failed for '{phrase}'")
                self.assertIsNone(cmd)

    # 2. Wake word without stop intent
    def test_2_wake_word_without_stop_intent(self):
        """Wake word alone without a command payload is ignored."""
        wake_words = [
            "Jarvis",
            "jarvis",
            "Hey Jarvis",
            "hey jarvis",
            "ok jarvis",
            "OK Jarvis",
            "Jarvis, ",
        ]
        for phrase in wake_words:
            with self.subTest(phrase=phrase):
                decision, cmd = jarvis_voice.classify_interruption(phrase)
                self.assertEqual(decision, jarvis_voice.INTERRUPT_IGNORE, f"Failed for '{phrase}'")
                self.assertIsNone(cmd)

    # 3. Incomplete transcripts
    def test_3_incomplete_transcripts(self):
        """Incomplete or single-word fragments are ignored."""
        fragments = [
            "how",
            "How",
            "what",
            "why",
            "who",
            "when",
            "bright",
            "",
            "   ",
        ]
        for frag in fragments:
            with self.subTest(fragment=frag):
                decision, cmd = jarvis_voice.classify_interruption(frag)
                self.assertEqual(decision, jarvis_voice.INTERRUPT_IGNORE, f"Failed for '{frag}'")
                self.assertIsNone(cmd)

    # 4. Unrelated conversational speech
    def test_4_unrelated_conversational_speech(self):
        """Conversational pleasantries and ambient chatter are ignored."""
        unrelated = [
            "bright how are you",
            "how are you",
            "good morning",
            "good evening",
            "hello",
            "hi there",
            "yeah",
            "well",
            "you know",
        ]
        for phrase in unrelated:
            with self.subTest(phrase=phrase):
                decision, cmd = jarvis_voice.classify_interruption(phrase)
                self.assertEqual(decision, jarvis_voice.INTERRUPT_IGNORE, f"Failed for '{phrase}'")
                self.assertIsNone(cmd)

    # 5. Background/noise recognition
    def test_5_background_noise_recognition(self):
        """Unrelated words and noise transcriptions are ignored."""
        noise = [
            "the",
            "laptop",
            "top 10",
            "uh",
            "um",
            "acoustic",
            "microphone",
        ]
        for word in noise:
            with self.subTest(word=word):
                decision, cmd = jarvis_voice.classify_interruption(word)
                self.assertEqual(decision, jarvis_voice.INTERRUPT_IGNORE, f"Failed for '{word}'")
                self.assertIsNone(cmd)

    # 6. Recognition variations ("Jarvis top")
    def test_6_recognition_variations(self):
        """Phonetic variations like 'Jarvis top' classify as STOP only when addressed to Jarvis."""
        variations = [
            "Jarvis top",
            "jarvis top",
            "hey jarvis top",
            "ok jarvis top",
            "Jarvis, top please",
        ]
        for var in variations:
            with self.subTest(variation=var):
                decision, cmd = jarvis_voice.classify_interruption(var)
                self.assertEqual(decision, jarvis_voice.INTERRUPT_STOP, f"Failed for '{var}'")
                self.assertIsNone(cmd)

        # But 'top' alone without Jarvis must be ignored
        decision, _ = jarvis_voice.classify_interruption("top")
        self.assertEqual(decision, jarvis_voice.INTERRUPT_IGNORE)

        # 'top 10' must be ignored
        decision, _ = jarvis_voice.classify_interruption("top 10")
        self.assertEqual(decision, jarvis_voice.INTERRUPT_IGNORE)

    # 7. A real actionable command during TTS
    def test_7_real_actionable_command_during_tts(self):
        """Actionable commands classify as COMMAND and return cleaned command transcript."""
        commands = [
            ("Open Notepad", "Open Notepad"),
            ("open notepad", "open notepad"),
            ("Jarvis, open notepad", "open notepad"),
            ("close notepad", "close notepad"),
            ("what time is it", "what time is it"),
            ("search for python dataclasses", "search for python dataclasses"),
            ("play lofi on youtube", "play lofi on youtube"),
            ("cancel task", "cancel task"),
        ]
        for raw, expected in commands:
            with self.subTest(cmd=raw):
                decision, cmd = jarvis_voice.classify_interruption(raw)
                self.assertEqual(decision, jarvis_voice.INTERRUPT_COMMAND, f"Failed for '{raw}'")
                self.assertEqual(cmd.lower(), expected.lower())

        # Test end-to-end through speak_interruptible
        can_finish = threading.Event()
        engine = _mock_engine(can_finish)

        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        poll_mock = MagicMock()
        poll_mock.listen.return_value = object()
        poll_mock.recognize_google.return_value = "Open Notepad"

        with patch("pyttsx3.init", return_value=engine), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll_mock):

            def unblock():
                jarvis_voice._stop_event.wait(timeout=2.0)
                can_finish.set()

            threading.Thread(target=unblock, daemon=True).start()
            res = jarvis_voice.speak_interruptible("Speaking long text...", mic)

        self.assertEqual(res, "Open Notepad")
        self.assertFalse(jarvis_voice.is_tts_active())

    # 8. Repeated interruptions across consecutive sessions
    def test_8_repeated_interruptions_across_consecutive_sessions(self):
        """Classifier correctly distinguishes STOP vs COMMAND across consecutive sessions."""
        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        # Session 1: STOP
        can_finish1 = threading.Event()
        engine1 = _mock_engine(can_finish1)
        poll1 = MagicMock()
        poll1.listen.return_value = object()
        poll1.recognize_google.return_value = "Jarvis stop"

        with patch("pyttsx3.init", return_value=engine1), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll1):
            def unblock1():
                jarvis_voice._stop_event.wait(timeout=2.0)
                can_finish1.set()
            threading.Thread(target=unblock1, daemon=True).start()
            res1 = jarvis_voice.speak_interruptible("Session 1...", mic)
        self.assertIsNone(res1)

        # Session 2: COMMAND
        can_finish2 = threading.Event()
        engine2 = _mock_engine(can_finish2)
        poll2 = MagicMock()
        poll2.listen.return_value = object()
        poll2.recognize_google.return_value = "open calculator"

        with patch("pyttsx3.init", return_value=engine2), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll2):
            def unblock2():
                jarvis_voice._stop_event.wait(timeout=2.0)
                can_finish2.set()
            threading.Thread(target=unblock2, daemon=True).start()
            res2 = jarvis_voice.speak_interruptible("Session 2...", mic)
        self.assertEqual(res2, "open calculator")

        # Session 3: STOP VARIATION
        can_finish3 = threading.Event()
        engine3 = _mock_engine(can_finish3)
        poll3 = MagicMock()
        poll3.listen.return_value = object()
        poll3.recognize_google.return_value = "Jarvis top"

        with patch("pyttsx3.init", return_value=engine3), \
             patch("builtins.print"), \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll3):
            def unblock3():
                jarvis_voice._stop_event.wait(timeout=2.0)
                can_finish3.set()
            threading.Thread(target=unblock3, daemon=True).start()
            res3 = jarvis_voice.speak_interruptible("Session 3...", mic)
        self.assertIsNone(res3)

    # 9. Pending high-risk approval preservation
    def test_9_pending_high_risk_approval_preservation(self):
        """Pending high-risk approval survives ignored speech and deliberate stop interruptions."""
        jarvis_policy.set_pending_approval(
            tool_name="close_application",
            arguments={"name": "notepad"},
            description="Close Notepad",
        )
        self.assertTrue(jarvis_policy.has_pending_approval())

        # Ignored noise during prompt
        decision, _ = jarvis_voice.classify_interruption("bright how are you")
        self.assertEqual(decision, jarvis_voice.INTERRUPT_IGNORE)
        self.assertTrue(jarvis_policy.has_pending_approval())

        # Deliberate stop
        decision, _ = jarvis_voice.classify_interruption("Jarvis stop")
        self.assertEqual(decision, jarvis_voice.INTERRUPT_STOP)
        self.assertTrue(jarvis_policy.has_pending_approval())

        # Confirming 'yes' executes the pending action
        mock_res = jarvis_windows.ToolResult(True, "Closed Notepad.", {"app": "notepad"})
        with patch("jarvis_tools.execute_tool", return_value=mock_res):
            reply = jarvis_brain.think("yes")
        self.assertEqual(reply, "Closed Notepad.")
        self.assertFalse(jarvis_policy.has_pending_approval())

    # 10. Lifecycle cleanup after ignored speech
    def test_10_lifecycle_cleanup_after_ignored_speech(self):
        """When noise/filler occurs, TTS continues uninterrupted, finishes naturally, and cleans up."""
        engine = _mock_engine(None)

        mic = MagicMock()
        mic.__enter__.return_value = mic
        mic.__exit__.return_value = False

        poll_mock = MagicMock()
        # First iteration returns ignored filler "how", second iteration raises WaitTimeoutError
        poll_mock.listen.side_effect = [object(), jarvis_voice.sr.WaitTimeoutError()]
        poll_mock.recognize_google.return_value = "how"

        with patch("pyttsx3.init", return_value=engine), \
             patch("builtins.print") as mock_print, \
             patch.object(jarvis_voice.sr, "Recognizer", return_value=poll_mock):
            res = jarvis_voice.speak_interruptible("Full utterance finishes naturally", mic)

        self.assertIsNone(res)
        self.assertFalse(jarvis_voice.is_tts_active())
        self.assertFalse(jarvis_voice._tts_lock)
        # Engine should have completed naturally without stop requested
        engine.say.assert_called_once_with("Full utterance finishes naturally")


if __name__ == "__main__":
    unittest.main()
