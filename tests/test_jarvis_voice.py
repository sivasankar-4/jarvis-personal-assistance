import unittest
from unittest.mock import MagicMock, patch

import speech_recognition as sr

import jarvis_voice


class VoiceTranscriptHandlingTests(unittest.TestCase):
    def test_short_command_is_passed_through_completely(self):
        raw = "Jarvis, what time is it?"
        command = jarvis_voice.command_from_transcript(raw)
        self.assertEqual(command, "what time is it?")

        think = MagicMock(return_value="It is noon.")
        speak = MagicMock()
        with patch("builtins.print"):
            reply = jarvis_voice.handle_recognized_text(raw, think_fn=think, speak_fn=speak)
        think.assert_called_once_with("what time is it?")
        speak.assert_called_once_with("It is noon.")
        self.assertEqual(reply, "It is noon.")

    def test_long_sentence_is_not_truncated_before_the_brain(self):
        raw = (
            "Remember that I prefer Java for backend development "
            "and explain the difference between an interface and an abstract class in Java."
        )
        self.assertEqual(jarvis_voice.command_from_transcript(raw), raw)

        think = MagicMock(return_value="Should I save this memory?")
        speak = MagicMock()
        with patch("builtins.print"):
            jarvis_voice.handle_recognized_text(raw, think_fn=think, speak_fn=speak)
        think.assert_called_once_with(raw)
        self.assertGreater(len(think.call_args[0][0]), 80)

    def test_wake_word_only_does_not_call_the_brain(self):
        think = MagicMock()
        speak = MagicMock()
        with patch("builtins.print"):
            result = jarvis_voice.handle_recognized_text("Jarvis", think_fn=think, speak_fn=speak)
        think.assert_not_called()
        speak.assert_called_once_with("Yes sir, I'm listening.")
        self.assertEqual(result, "wake")


class VoiceCaptureRetryTests(unittest.TestCase):
    def test_recoverable_recognition_failure_does_not_crash(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False
        recognizer.listen.return_value = object()
        recognizer.recognize_google.side_effect = [
            sr.UnknownValueError(),
            "Remember that I prefer Java for backend development",
        ]

        with patch("builtins.print"):
            status, text = jarvis_voice.capture_command(recognizer, microphone, max_attempts=3)

        self.assertEqual(status, jarvis_voice.CAPTURE_HEARD)
        self.assertEqual(text, "Remember that I prefer Java for backend development")
        self.assertEqual(recognizer.listen.call_count, 2)

    def test_unclear_audio_is_replaced_with_a_fresh_recording(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False
        first_audio, second_audio = object(), object()
        recognizer.listen.side_effect = [first_audio, second_audio]
        recognizer.recognize_google.side_effect = [sr.UnknownValueError(), "what time is it"]

        with patch("builtins.print"):
            status, text = jarvis_voice.capture_command(recognizer, microphone, max_attempts=2)

        self.assertEqual((status, text), (jarvis_voice.CAPTURE_HEARD, "what time is it"))
        self.assertEqual(recognizer.recognize_google.call_args_list[0].args[0], first_audio)
        self.assertEqual(recognizer.recognize_google.call_args_list[1].args[0], second_audio)

    def test_network_failure_is_not_reported_as_unclear_audio(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False
        recognizer.listen.return_value = object()
        recognizer.recognize_google.side_effect = sr.RequestError("connection timed out")
        printed = []

        with patch("time.sleep"), patch(
            "builtins.print", side_effect=lambda *args, **kwargs: printed.append(" ".join(map(str, args)))
        ):
            status, text = jarvis_voice.capture_command(
                recognizer, microphone, max_attempts=5, max_network_retries=0
            )

        self.assertEqual((status, text), (jarvis_voice.CAPTURE_SERVICE_ERROR, None))
        self.assertEqual(recognizer.listen.call_count, 1)
        self.assertIn("Google speech service error", "\n".join(printed))
        self.assertNotIn("unclear", "\n".join(printed))

    def test_network_retries_are_bounded_and_use_fresh_recordings(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False
        recognizer.listen.side_effect = [object(), object(), object(), object()]
        recognizer.recognize_google.side_effect = sr.RequestError("timed out")

        with patch("time.sleep") as sleep, patch("builtins.print"):
            status, text = jarvis_voice.capture_command(
                recognizer, microphone, max_attempts=10, max_network_retries=2
            )

        self.assertEqual((status, text), (jarvis_voice.CAPTURE_SERVICE_ERROR, None))
        self.assertEqual(recognizer.listen.call_count, 3)
        self.assertEqual(recognizer.recognize_google.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.5, 1.0])

    def test_command_succeeds_after_a_recoverable_service_failure(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False
        first_audio, second_audio = object(), object()
        recognizer.listen.side_effect = [first_audio, second_audio]
        recognizer.recognize_google.side_effect = [
            sr.RequestError("connection timed out"),
            "open YouTube and search for Java tutorials",
        ]

        with patch("time.sleep"), patch("builtins.print"):
            status, text = jarvis_voice.capture_command(
                recognizer, microphone, max_attempts=3, max_network_retries=2
            )

        self.assertEqual(status, jarvis_voice.CAPTURE_HEARD)
        self.assertEqual(text, "open YouTube and search for Java tutorials")
        self.assertEqual([call.args[0] for call in recognizer.recognize_google.call_args_list], [first_audio, second_audio])

    def test_microphone_error_is_reported_as_a_device_error(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.side_effect = OSError(10060, "device stream failed")
        printed = []

        with patch(
            "builtins.print", side_effect=lambda *args, **kwargs: printed.append(" ".join(map(str, args)))
        ):
            status, text = jarvis_voice.capture_command(recognizer, microphone, max_attempts=3)

        self.assertEqual((status, text), (jarvis_voice.CAPTURE_MIC_ERROR, None))
        self.assertEqual(recognizer.recognize_google.call_count, 0)
        self.assertIn("microphone/device error during opening microphone", "\n".join(printed))

    def test_transport_oserror_after_capture_is_not_called_a_microphone_error(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False
        recognizer.listen.return_value = object()
        recognizer.recognize_google.side_effect = OSError(10060, "connection timed out")
        printed = []

        with patch("time.sleep"), patch(
            "builtins.print", side_effect=lambda *args, **kwargs: printed.append(" ".join(map(str, args)))
        ):
            status, text = jarvis_voice.capture_command(
                recognizer, microphone, max_attempts=3, max_network_retries=0
            )

        self.assertEqual((status, text), (jarvis_voice.CAPTURE_SERVICE_ERROR, None))
        notice = "\n".join(printed)
        self.assertIn("Google speech transport error", notice)
        self.assertNotIn("microphone/device error", notice)

    def test_repeated_failures_stop_after_max_attempts(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False
        recognizer.listen.return_value = object()
        recognizer.recognize_google.side_effect = sr.UnknownValueError()

        with patch("builtins.print"):
            status, text = jarvis_voice.capture_command(recognizer, microphone, max_attempts=3)

        self.assertEqual(status, jarvis_voice.CAPTURE_FAILED)
        self.assertIsNone(text)
        self.assertEqual(recognizer.listen.call_count, 3)
        self.assertEqual(recognizer.recognize_google.call_count, 3)

    def test_silence_is_idle_and_does_not_retry(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False
        recognizer.listen.side_effect = sr.WaitTimeoutError()

        with patch("builtins.print"):
            status, text = jarvis_voice.capture_command(recognizer, microphone, max_attempts=5)

        self.assertEqual(status, jarvis_voice.CAPTURE_IDLE)
        self.assertIsNone(text)
        self.assertEqual(recognizer.listen.call_count, 1)

    def test_listen_uses_configured_timeout_and_phrase_limit(self):
        recognizer = MagicMock()
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False
        recognizer.listen.return_value = object()
        recognizer.recognize_google.return_value = "open notepad"

        with patch("builtins.print"):
            jarvis_voice.capture_command(recognizer, microphone, max_attempts=1)

        recognizer.listen.assert_called_once()
        kwargs = recognizer.listen.call_args.kwargs
        self.assertEqual(kwargs["timeout"], jarvis_voice.LISTEN_TIMEOUT)
        self.assertEqual(kwargs["phrase_time_limit"], jarvis_voice.PHRASE_TIME_LIMIT)
        self.assertGreaterEqual(kwargs["phrase_time_limit"], 20)

    def test_recognizer_allows_pauses_inside_a_sentence(self):
        recognizer = sr.Recognizer()
        jarvis_voice.apply_recognizer_settings(recognizer)
        self.assertEqual(recognizer.pause_threshold, 0.8)
        self.assertLessEqual(recognizer.non_speaking_duration, recognizer.pause_threshold)
        self.assertFalse(recognizer.dynamic_energy_threshold)
        self.assertEqual(recognizer.operation_timeout, jarvis_voice.RECOGNITION_NETWORK_TIMEOUT)


class SpeechPlaybackTests(unittest.TestCase):
    def setUp(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False

    def tearDown(self):
        jarvis_voice._tts_engine = None
        jarvis_voice._tts_lock = False

    def _engine(self):
        engine = MagicMock()
        engine.getProperty.return_value = 1.0
        return engine

    def test_two_consecutive_responses_are_both_sent_to_tts(self):
        first = self._engine()
        second = self._engine()
        with patch("pyttsx3.init", side_effect=[first, second]), patch("time.sleep"), patch("builtins.print"):
            jarvis_voice.speak("It is currently 10:00 AM")
            jarvis_voice.speak("An interface cannot contain implementation.")

        first.say.assert_called_once_with("It is currently 10:00 AM")
        first.runAndWait.assert_called_once()
        second.say.assert_called_once_with("An interface cannot contain implementation.")
        second.runAndWait.assert_called_once()

    def test_second_response_is_not_lost_after_the_first_finishes(self):
        think = MagicMock(side_effect=["First spoken reply.", "Second spoken reply."])
        spoken = MagicMock()
        with patch("builtins.print"):
            jarvis_voice.handle_recognized_text("what time is it", think_fn=think, speak_fn=spoken)
            jarvis_voice.handle_recognized_text("explain java interfaces", think_fn=think, speak_fn=spoken)
        self.assertEqual(
            [call.args[0] for call in spoken.call_args_list],
            ["First spoken reply.", "Second spoken reply."],
        )

    def test_tts_exception_is_reported_and_does_not_raise(self):
        broken = self._engine()
        broken.runAndWait.side_effect = RuntimeError("run loop already started")
        printed = []
        with patch("pyttsx3.init", return_value=broken), patch("time.sleep"), patch(
            "builtins.print", side_effect=lambda *args, **kwargs: printed.append(" ".join(str(a) for a in args))
        ):
            jarvis_voice.speak("It is currently 10:00 AM")
            jarvis_voice.speak("Still here after a TTS failure.")

        notice = "\n".join(printed)
        self.assertIn("playback failed", notice)
        self.assertIn("run loop already started", notice)
        self.assertTrue(any("It is currently 10:00 AM" in line for line in printed))
        self.assertTrue(any("Still here after a TTS failure." in line for line in printed))

    def test_playback_completes_without_artificial_settle_delay(self):
        engine = self._engine()
        order = []
        engine.say.side_effect = lambda text: order.append("say")
        engine.runAndWait.side_effect = lambda: order.append("runAndWait")
        with patch("pyttsx3.init", return_value=engine), patch(
            "time.sleep", side_effect=lambda seconds: order.append("settle")
        ), patch("builtins.print"):
            jarvis_voice.speak("Hello")
        self.assertEqual(order, ["say", "runAndWait"])

    def test_next_listen_starts_only_after_speech_returns(self):
        order = []
        pending = [
            (jarvis_voice.CAPTURE_HEARD, "what time is it"),
            (jarvis_voice.CAPTURE_HEARD, "explain interfaces"),
        ]
        microphone = MagicMock()
        microphone.__enter__.return_value = microphone
        microphone.__exit__.return_value = False

        def capture(_recognizer, _microphone, max_attempts=jarvis_voice.MAX_RECOGNITION_ATTEMPTS):
            order.append("listen")
            if pending:
                return pending.pop(0)
            raise KeyboardInterrupt

        def handle(transcript, think_fn=None, speak_fn=None):
            order.append(("speak", transcript))

        with patch.object(jarvis_voice, "open_microphone", return_value=microphone), patch.object(
            jarvis_voice, "calibrate_for_ambient_noise"
        ), patch.object(jarvis_voice, "capture_command", side_effect=capture), patch.object(
            jarvis_voice, "handle_recognized_text", side_effect=handle
        ), patch("builtins.print"):
            jarvis_voice.listen_loop()

        self.assertEqual(
            order,
            [
                "listen",
                ("speak", "what time is it"),
                "listen",
                ("speak", "explain interfaces"),
                "listen",
            ],
        )


if __name__ == "__main__":
    unittest.main()
