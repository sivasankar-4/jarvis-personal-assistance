import os
import unittest
from unittest.mock import MagicMock, patch

import speech_recognition as sr

import jarvis_voice

SENTINEL_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00"


class StubAudio:
    def get_wav_data(self):
        return SENTINEL_WAV


class FakeTranscriptions:
    def __init__(self, return_val=None, side_effect=None):
        self.return_val = return_val
        self.side_effect = side_effect
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self.side_effect is not None:
            raise self.side_effect
        return self.return_val


class FakeAudioAPI:
    def __init__(self, transcriptions):
        self.transcriptions = transcriptions


class FakeClient:
    def __init__(self, return_val=None, side_effect=None):
        self.transcriptions = FakeTranscriptions(return_val=return_val, side_effect=side_effect)
        self.audio = FakeAudioAPI(self.transcriptions)


class JarvisSTTWhisperTests(unittest.TestCase):
    def setUp(self):
        self._orig_client = jarvis_voice._groq_stt_client
        jarvis_voice._groq_stt_client = None

    def tearDown(self):
        jarvis_voice._groq_stt_client = self._orig_client

    def test_whisper_transcription_success(self):
        fake_client = FakeClient(return_val=" open chrome ")
        jarvis_voice._groq_stt_client = fake_client
        recognizer = MagicMock()
        audio = StubAudio()

        with patch("jarvis_voice._tts_api_key", return_value="fake-key"):
            result = jarvis_voice.transcribe_audio(recognizer, audio)

        self.assertEqual(result, "open chrome")
        self.assertEqual(
            fake_client.transcriptions.last_kwargs,
            {
                "model": "whisper-large-v3-turbo",
                "file": ("command.wav", SENTINEL_WAV),
                "language": "en",
                "response_format": "text",
            },
        )
        recognizer.recognize_google.assert_not_called()

    def test_whisper_transcription_success_object_with_text(self):
        class ResultObj:
            text = " open chrome "

        fake_client = FakeClient(return_val=ResultObj())
        jarvis_voice._groq_stt_client = fake_client
        recognizer = MagicMock()
        audio = StubAudio()

        with patch("jarvis_voice._tts_api_key", return_value="fake-key"):
            result = jarvis_voice.transcribe_audio(recognizer, audio)

        self.assertEqual(result, "open chrome")
        recognizer.recognize_google.assert_not_called()

    def test_whisper_model_override(self):
        fake_client = FakeClient(return_val="result")
        jarvis_voice._groq_stt_client = fake_client
        recognizer = MagicMock()
        audio = StubAudio()

        with patch("jarvis_voice._tts_api_key", return_value="fake-key"), \
             patch.dict(os.environ, {"JARVIS_STT_MODEL": "whisper-large-v3"}):
            jarvis_voice.transcribe_audio(recognizer, audio)

        self.assertEqual(fake_client.transcriptions.last_kwargs["model"], "whisper-large-v3")

    def test_whisper_empty_result(self):
        fake_client = FakeClient(return_val=" ")
        jarvis_voice._groq_stt_client = fake_client
        recognizer = MagicMock()
        audio = StubAudio()

        with patch("jarvis_voice._tts_api_key", return_value="fake-key"):
            result = jarvis_voice.transcribe_audio(recognizer, audio)

        self.assertEqual(result, "")
        recognizer.recognize_google.assert_not_called()

    def test_whisper_fallback_on_runtime_error(self):
        fake_client = FakeClient(side_effect=RuntimeError("Whisper service down"))
        jarvis_voice._groq_stt_client = fake_client
        recognizer = MagicMock()
        recognizer.recognize_google.return_value = "from google"
        audio = StubAudio()

        with patch("jarvis_voice._tts_api_key", return_value="fake-key"):
            result = jarvis_voice.transcribe_audio(recognizer, audio)

        self.assertEqual(result, "from google")
        recognizer.recognize_google.assert_called_once_with(audio)

    def test_whisper_fallback_error_mapping_request_error(self):
        fake_client = FakeClient(side_effect=RuntimeError("Whisper failed"))
        jarvis_voice._groq_stt_client = fake_client
        recognizer = MagicMock()
        recognizer.recognize_google.side_effect = sr.RequestError("boom")
        audio = StubAudio()

        with patch("jarvis_voice._tts_api_key", return_value="fake-key"):
            with self.assertRaises(sr.RequestError) as cm:
                jarvis_voice.transcribe_audio(recognizer, audio)
        self.assertIn("boom", str(cm.exception))

    def test_whisper_fallback_error_mapping_unknown_value_error(self):
        fake_client = FakeClient(side_effect=RuntimeError("Whisper failed"))
        jarvis_voice._groq_stt_client = fake_client
        recognizer = MagicMock()
        recognizer.recognize_google.side_effect = sr.UnknownValueError()
        audio = StubAudio()

        with patch("jarvis_voice._tts_api_key", return_value="fake-key"):
            with self.assertRaises(sr.UnknownValueError):
                jarvis_voice.transcribe_audio(recognizer, audio)

    def test_whisper_fallback_error_mapping_os_error(self):
        fake_client = FakeClient(side_effect=RuntimeError("Whisper failed"))
        jarvis_voice._groq_stt_client = fake_client
        recognizer = MagicMock()
        recognizer.recognize_google.side_effect = OSError("connection lost")
        audio = StubAudio()

        with patch("jarvis_voice._tts_api_key", return_value="fake-key"):
            with self.assertRaises(OSError) as cm:
                jarvis_voice.transcribe_audio(recognizer, audio)
        self.assertIn("connection lost", str(cm.exception))

    def test_whisper_no_key_fallback(self):
        jarvis_voice._groq_stt_client = None
        recognizer = MagicMock()
        recognizer.recognize_google.return_value = "from google"
        audio = StubAudio()

        with patch("jarvis_voice._tts_api_key", return_value=None):
            result = jarvis_voice.transcribe_audio(recognizer, audio)

        self.assertEqual(result, "from google")
        self.assertIsNone(jarvis_voice._groq_stt_client)
        recognizer.recognize_google.assert_called_once_with(audio)

    def test_pause_default(self):
        env_copy = os.environ.copy()
        env_copy.pop("JARVIS_PAUSE_THRESHOLD", None)
        with patch.dict(os.environ, env_copy, clear=True):
            self.assertEqual(jarvis_voice.PAUSE_THRESHOLD, 0.8)


if __name__ == "__main__":
    unittest.main()
