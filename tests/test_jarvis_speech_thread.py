"""Tests for dedicated speech worker thread seams (TASK-020B recovery)."""

import os
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

import jarvis_voice


class SpeechThreadSeamTests(unittest.TestCase):
    def setUp(self):
        jarvis_voice._stop_speech_worker()
        jarvis_voice._tts_engine = None
        jarvis_voice._stop_event.clear()
        jarvis_voice._tts_lock = False

    def tearDown(self):
        jarvis_voice._stop_speech_worker()
        jarvis_voice._tts_engine = None
        jarvis_voice._stop_event.clear()
        jarvis_voice._tts_lock = False

    def test_worker_does_not_exist_before_first_dispatch(self):
        """Worker thread must NOT be spawned at import time."""
        # 1. Assert in-process state when worker has not been started
        self.assertIsNone(jarvis_voice._speech_thread)
        active_thread_names = [t.name for t in threading.enumerate()]
        self.assertNotIn("jarvis-speech-worker", active_thread_names)

        # 2. Subprocess check: fresh module import spawns no speech worker thread
        cmd = [
            sys.executable,
            "-c",
            "import threading, jarvis_voice; "
            "names = [t.name for t in threading.enumerate()]; "
            "assert 'jarvis-speech-worker' not in names, names",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"Worker thread spawned on import: {proc.stderr}")

    def test_request_tts_stop_executes_on_speech_worker_thread(self):
        """stop() via request_tts_stop() executes on the jarvis-speech-worker thread."""
        stop_threads = []

        class RecordingEngine:
            def stop(self):
                stop_threads.append(threading.current_thread().name)

            def getProperty(self, name):
                return 1.0

            def isBusy(self):
                return False

        engine = RecordingEngine()
        jarvis_voice._ensure_speech_thread()
        jarvis_voice._tts_engine = engine

        caller_thread = threading.current_thread().name
        jarvis_voice.request_tts_stop()

        self.assertTrue(len(stop_threads) >= 1, "engine.stop() was not called")
        self.assertEqual(stop_threads[0], "jarvis-speech-worker")
        self.assertNotEqual(stop_threads[0], caller_thread)

    def test_discard_executes_on_speech_worker_thread(self):
        """_discard_tts_engine() executes engine.stop on the jarvis-speech-worker thread."""
        discard_stop_threads = []

        class RecordingEngine:
            def stop(self):
                discard_stop_threads.append(threading.current_thread().name)

            def getProperty(self, name):
                return 1.0

            def isBusy(self):
                return False

        engine = RecordingEngine()
        jarvis_voice._ensure_speech_thread()
        jarvis_voice._tts_engine = engine

        caller_thread = threading.current_thread().name
        jarvis_voice._discard_tts_engine()

        self.assertEqual(len(discard_stop_threads), 1, "engine.stop() was not called exactly once on discard")
        self.assertEqual(discard_stop_threads[0], "jarvis-speech-worker")
        self.assertNotEqual(discard_stop_threads[0], caller_thread)
        self.assertIsNone(jarvis_voice._tts_engine)

    def test_dispatch_timeout_raises_within_bounded_time(self):
        """_dispatch_pyttsx3 raises TimeoutError within timeout + slack if engine blocks."""
        unblock_event = threading.Event()

        class BlockingEngine:
            def getProperty(self, name):
                return 1.0

            def say(self, text):
                pass

            def runAndWait(self):
                unblock_event.wait(timeout=5.0)

            def stop(self):
                unblock_event.set()

        test_timeout = 0.3
        slack = 0.5

        try:
            with patch.object(jarvis_voice, "_TTS_DISPATCH_TIMEOUT", test_timeout), \
                 patch("pyttsx3.init", return_value=BlockingEngine()):
                start = time.time()
                with self.assertRaises(TimeoutError):
                    jarvis_voice._dispatch_pyttsx3("This should time out")
                elapsed = time.time() - start

                self.assertGreaterEqual(elapsed, test_timeout * 0.8)
                self.assertLess(elapsed, test_timeout + slack)
        finally:
            unblock_event.set()
            jarvis_voice._stop_speech_worker()


    def test_dispatch_timeout_constant_exceeds_minimum_safe_floor(self):
        """_TTS_DISPATCH_TIMEOUT must be large enough to cover a full long reply.

        At ~130 wpm (SAPI5 default) a 500-word reply takes ~230 s, so the constant
        must be at least 120 s.  Any edit that silently drops it back to ~10 will
        fail here before the broken constant can cause retry loops in production.
        """
        self.assertGreaterEqual(
            jarvis_voice._TTS_DISPATCH_TIMEOUT,
            120.0,
            "_TTS_DISPATCH_TIMEOUT is too small to cover a full long reply "
            "(see comment in jarvis_voice.py for the stated reasoning)",
        )

    def test_long_utterance_completes_through_dispatch_without_raising(self):
        """A slow (but not hung) engine completes through _dispatch_pyttsx3 without TimeoutError.

        Simulates a reply that takes ~0.4 s of "playback" — longer than the old
        10-second limit would have easily passed but representative of the shape
        of the problem.  The timeout is 300 s so this must succeed silently.
        """
        SIMULATED_SPEAK_DURATION = 0.4  # seconds

        class SlowEngine:
            def getProperty(self, name):
                return 1.0

            def say(self, text):
                pass

            def runAndWait(self):
                time.sleep(SIMULATED_SPEAK_DURATION)

            def stop(self):
                pass

            def connect(self, event, callback):
                pass

        with patch("pyttsx3.init", return_value=SlowEngine()), \
             patch("pyttsx3._activeEngines", {}, create=True):
            # Must complete without raising — the real test is simply no exception.
            start = time.time()
            jarvis_voice._dispatch_pyttsx3("This is a long reply that takes a while to say aloud.")
            elapsed = time.time() - start

        # Should have waited at least for the simulated duration
        self.assertGreaterEqual(elapsed, SIMULATED_SPEAK_DURATION * 0.5)
        # And should not have timed out (timeout is 300s; this should finish fast)
        self.assertLess(elapsed, 5.0, "dispatch took unreasonably long")

        jarvis_voice._stop_speech_worker()


if __name__ == "__main__":
    unittest.main()
