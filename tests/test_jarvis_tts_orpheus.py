"""Tests for Orpheus TTS integration and fallback in jarvis_voice.py."""

import io
import os
import sys
import time
import unittest
import wave
from unittest.mock import MagicMock, patch

import jarvis_voice


class JarvisTTSOrpheusTests(unittest.TestCase):
    def setUp(self):
        jarvis_voice._stop_event.clear()
        jarvis_voice._orpheus_cooldown_until = 0.0

    def tearDown(self):
        jarvis_voice._stop_event.clear()
        jarvis_voice._orpheus_cooldown_until = 0.0

    # 1. Short text fits in a single chunk
    def test_split_short_single_chunk(self):
        text = "One. Two! Three?"
        chunks = jarvis_voice._split_for_tts(text)
        self.assertEqual(chunks, ["One. Two! Three?"])

    # 2. Packing sentences into chunks <= 200 without splitting sentences
    def test_split_sentence_packing(self):
        s1 = "The first sentence explains the general architecture in detail and satisfies all tests with high confidence and precision."
        s2 = "The second sentence explains the playback system and audio synchronization in full while maintaining complete safety!"
        s3 = "The third sentence asks whether any further verification is needed before we conclude the entire procedure today?"
        text = f"{s1} {s2} {s3}"
        self.assertGreater(len(text), 350)
        chunks = jarvis_voice._split_for_tts(text)
        self.assertEqual(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(len(c), 200)
        self.assertEqual(" ".join(chunks), text)
        self.assertEqual(chunks[0], s1)
        self.assertEqual(chunks[1], s2)
        self.assertEqual(chunks[2], s3)

    # 3. Long sentence split at whole word boundary
    def test_split_long_sentence_word_boundary(self):
        words = (
            "The quick brown fox jumps over the lazy dog and runs across the open green "
            "field towards the distant blue mountains under the bright warm sun feeling "
            "happy and peaceful while watching the birds fly high in the clear blue sky "
            "above the quiet valley below today."
        )
        self.assertEqual(len(words), 260)
        chunks = jarvis_voice._split_for_tts(words)
        self.assertEqual(len(chunks), 2)
        for c in chunks:
            self.assertLessEqual(len(c), 200)
        self.assertEqual(chunks[0].split()[-1], "fly")
        self.assertEqual(" ".join(chunks), words)

    # 4. Long token without space split at exactly 200 chars
    def test_split_no_space_hard_cut(self):
        token = "x" * 250
        chunks = jarvis_voice._split_for_tts(token)
        self.assertEqual(chunks, ["x" * 200, "x" * 50])

    # 5. Synthesize payloads match model, voice, response_format, input
    def test_synth_payloads(self):
        calls = []

        class FakeSpeech:
            def create(self, **kwargs):
                calls.append(kwargs)
                class FakeResponse:
                    def read(self):
                        return b"RIFFfake"
                return FakeResponse()

        class FakeAudio:
            speech = FakeSpeech()

        class FakeClient:
            audio = FakeAudio()

        s1 = "This is the first sentence for testing multi-chunk payload processing."
        s2 = "This is the second sentence that definitely pushes the total character count well over two hundred characters."
        s3 = "And this third sentence guarantees that we have two distinct chunks of speech."
        text = f"{s1} {s2} {s3}"
        expected_chunks = jarvis_voice._split_for_tts(text)
        self.assertEqual(len(expected_chunks), 2)

        with patch("jarvis_voice._tts_client", return_value=FakeClient()), \
             patch("jarvis_voice._play_wav"):
            ok = jarvis_voice._speak_with_orpheus(text)

        self.assertTrue(ok)
        self.assertEqual(len(calls), 2)
        for i, call in enumerate(calls):
            self.assertEqual(call["model"], "canopylabs/orpheus-v1-english")
            self.assertEqual(call["voice"], "troy")
            self.assertEqual(call["response_format"], "wav")
            self.assertEqual(call["input"], expected_chunks[i])

    # 6. Audio playback receives byte strings in chunk order
    def test_playback_order(self):
        responses = [b"WAV_DATA_1", b"WAV_DATA_2"]
        resp_iter = iter(responses)

        class FakeSpeech:
            def create(self, **kwargs):
                data = next(resp_iter)
                class FakeResponse:
                    def read(self):
                        return data
                return FakeResponse()

        class FakeAudio:
            speech = FakeSpeech()

        class FakeClient:
            audio = FakeAudio()

        played = []
        s1 = "This is the first sentence for testing multi-chunk payload processing."
        s2 = "This is the second sentence that definitely pushes the total character count well over two hundred characters."
        s3 = "And this third sentence guarantees that we have two distinct chunks of speech."
        text = f"{s1} {s2} {s3}"
        with patch("jarvis_voice._tts_client", return_value=FakeClient()), \
             patch("jarvis_voice._play_wav", side_effect=played.append):
            ok = jarvis_voice._speak_with_orpheus(text)

        self.assertTrue(ok)
        self.assertEqual(played, responses)

    # 7. Orpheus success skips pyttsx3 entirely
    def test_dispatcher_orpheus_success(self):
        with patch("jarvis_voice._speak_with_orpheus", return_value=True) as mock_orpheus, \
             patch("jarvis_voice._speak_with_pyttsx3") as mock_pyttsx3:
            jarvis_voice._run_tts_background("Hello test")

        mock_orpheus.assert_called_once_with("Hello test")
        mock_pyttsx3.assert_not_called()

    # 8. Orpheus failure triggers pyttsx3 fallback and activates cooldown latch
    def test_dispatcher_fallback_and_latch(self):
        with patch("jarvis_voice._speak_with_orpheus", return_value=False) as mock_orpheus, \
             patch("jarvis_voice._speak_with_pyttsx3") as mock_pyttsx3, \
             patch("builtins.print") as mock_print:
            now = time.time()
            jarvis_voice._run_tts_background("First message")

            mock_orpheus.assert_called_once_with("First message")
            mock_pyttsx3.assert_called_once_with("First message")
            self.assertGreaterEqual(jarvis_voice._orpheus_cooldown_until, now + 290)
            mock_print.assert_any_call("[TTS]: natural voice unavailable; using the local voice.")

            # Second call while latch is active: Orpheus must NOT be invoked again
            mock_orpheus.reset_mock()
            mock_pyttsx3.reset_mock()
            jarvis_voice._run_tts_background("Second message")

            mock_orpheus.assert_not_called()
            mock_pyttsx3.assert_called_once_with("Second message")

    # 9. JARVIS_TTS_ENGINE=local bypasses Orpheus completely
    def test_engine_local(self):
        with patch.dict(os.environ, {"JARVIS_TTS_ENGINE": "local"}), \
             patch("jarvis_voice._speak_with_orpheus") as mock_orpheus, \
             patch("jarvis_voice._speak_with_pyttsx3") as mock_pyttsx3:
            jarvis_voice._run_tts_background("Local engine test")

        mock_orpheus.assert_not_called()
        mock_pyttsx3.assert_called_once_with("Local engine test")

    # 10. Voice selection from environment
    def test_voice_env(self):
        calls = []

        class FakeSpeech:
            def create(self, **kwargs):
                calls.append(kwargs)
                class FakeResponse:
                    content = b"WAV"
                return FakeResponse()

        class FakeAudio:
            speech = FakeSpeech()

        class FakeClient:
            audio = FakeAudio()

        with patch.dict(os.environ, {"JARVIS_TTS_VOICE": "hannah"}), \
             patch("jarvis_voice._tts_client", return_value=FakeClient()), \
             patch("jarvis_voice._play_wav"):
            jarvis_voice._speak_with_orpheus("Hello voice")
        self.assertEqual(calls[-1]["voice"], "hannah")

        with patch.dict(os.environ, {"JARVIS_TTS_VOICE": "bob"}), \
             patch("jarvis_voice._tts_client", return_value=FakeClient()), \
             patch("jarvis_voice._play_wav"):
            jarvis_voice._speak_with_orpheus("Hello voice default")
        self.assertEqual(calls[-1]["voice"], "troy")

    # 11. Stopping between chunks halts synthesis immediately
    def test_stop_between_chunks(self):
        synth_inputs = []

        class FakeSpeech:
            def create(self, **kwargs):
                synth_inputs.append(kwargs["input"])
                class FakeResponse:
                    content = b"WAV"
                return FakeResponse()

        class FakeAudio:
            speech = FakeSpeech()

        class FakeClient:
            audio = FakeAudio()

        s1 = "This is the first sentence for testing multi-chunk payload processing."
        s2 = "This is the second sentence that definitely pushes the total character count well over two hundred characters."
        s3 = "And this third sentence guarantees that we have two distinct chunks of speech."
        text = f"{s1} {s2} {s3}"
        chunks = jarvis_voice._split_for_tts(text)
        self.assertEqual(len(chunks), 2)

        def fake_play(data):
            jarvis_voice._stop_event.set()

        with patch("jarvis_voice._tts_client", return_value=FakeClient()), \
             patch("jarvis_voice._play_wav", side_effect=fake_play), \
             patch("jarvis_voice._speak_with_pyttsx3") as mock_pyttsx3:
            ok = jarvis_voice._speak_with_orpheus(text)

        self.assertTrue(ok)
        self.assertEqual(len(synth_inputs), 1)
        mock_pyttsx3.assert_not_called()

    # 12. request_tts_stop calls _purge_playback and sets _stop_event
    def test_request_tts_stop_purges(self):
        with patch("jarvis_voice._purge_playback") as mock_purge:
            jarvis_voice.request_tts_stop()

        mock_purge.assert_called_once()
        self.assertTrue(jarvis_voice._stop_event.is_set())

    # 13. Real winsound playback seam smoke test
    @unittest.skipUnless(sys.platform == "win32", "winsound is Windows-only")
    def test_play_wav_real_winsound(self):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)      # mono
            w.setsampwidth(2)      # 16-bit
            w.setframerate(8000)   # 8000 Hz
            w.writeframes(b"\x00\x00" * int(8000 * 0.05))  # 0.05 s of silence
        wav_bytes = buf.getvalue()

        # Real unpatched _play_wav call against winsound.PlaySound
        try:
            jarvis_voice._play_wav(wav_bytes)
        except Exception as exc:
            self.fail(f"_play_wav raised an unexpected exception: {exc}")

    # 14. Temporary file lifecycle during playback
    def test_play_wav_uses_temp_file(self):
        recorded = []
        file_content_during_call = None
        file_existed_during_call = False

        FAKE_SND_FILENAME = 0x00020000

        class FakeWinsound:
            SND_FILENAME = FAKE_SND_FILENAME

            @staticmethod
            def PlaySound(sound, flags):
                nonlocal file_content_during_call, file_existed_during_call
                recorded.append((sound, flags))
                file_existed_during_call = os.path.exists(sound)
                if file_existed_during_call:
                    with open(sound, "rb") as f:
                        file_content_during_call = f.read()

        fake_module = FakeWinsound()
        with patch.dict(sys.modules, {"winsound": fake_module}):
            jarvis_voice._play_wav(b"FAKE-WAV-BYTES")

        self.assertEqual(len(recorded), 1)
        path, flags = recorded[0]
        self.assertEqual(flags, FAKE_SND_FILENAME)
        self.assertTrue(file_existed_during_call)
        self.assertEqual(file_content_during_call, b"FAKE-WAV-BYTES")
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
