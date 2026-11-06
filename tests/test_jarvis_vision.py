"""Tests for in-memory cloud vision screen questions."""

import base64
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

import jarvis_policy
import jarvis_tools
import jarvis_vision


class _FakeClient:
    def __init__(self, answer="Notepad is open.", error=None):
        self.answer = answer
        self.error = error
        self.create = MagicMock(side_effect=self._create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def _create(self, **kwargs):
        if self.error:
            raise self.error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.answer))])


class VisionTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _ask(self, image, client=None, question="What is open?", target="active_window"):
        client = client or _FakeClient()
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_image", return_value=image), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.ask_screen(question, target)
        return result, client

    @staticmethod
    def _request_image(client):
        content = client.create.call_args.kwargs["messages"][1]["content"]
        return next(part["image_url"]["url"] for part in content if part["type"] == "image_url")

    def test_happy_path(self):
        client = _FakeClient("Notepad is open.")
        result, client = self._ask(Image.new("RGB", (800, 600)), client)
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Notepad is open.")
        self.assertEqual(result.data, {"target": "active_window", "model": jarvis_vision._vision_model()})
        self.assertEqual(client.create.call_args.kwargs["model"], jarvis_vision._vision_model())
        self.assertTrue(self._request_image(client).startswith("data:image/jpeg;base64,"))

    def test_image_downscaled(self):
        result, client = self._ask(Image.new("RGB", (2560, 1440)))
        self.assertTrue(result.ok)
        payload = self._request_image(client).split(",", 1)[1]
        with Image.open(io.BytesIO(base64.b64decode(payload))) as sent:
            self.assertEqual(max(sent.size), 1280)

    def test_model_env_override(self):
        with patch.dict(os.environ, {"JARVIS_VISION_MODEL": "vision-override"}):
            result, client = self._ask(Image.new("RGB", (100, 100)))
            self.assertTrue(result.ok)
            self.assertEqual(jarvis_vision._vision_model(), "vision-override")
            self.assertEqual(client.create.call_args.kwargs["model"], "vision-override")

    def test_bad_target_and_empty_question(self):
        client = MagicMock()
        with patch("jarvis_vision._get_client", return_value=client):
            bad_target = jarvis_vision.ask_screen("What?", "desktop")
            empty_question = jarvis_vision.ask_screen("  ", "screen")
        self.assertFalse(bad_target.ok)
        self.assertFalse(empty_question.ok)
        client.assert_not_called()

    def test_no_api_key(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}), \
             patch("jarvis_vision.KEYFILE", "missing-key-file"), \
             patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_image", return_value=Image.new("RGB", (10, 10))):
            result = jarvis_vision.ask_screen("What?", "screen")
        self.assertFalse(result.ok)
        self.assertIn("Groq API key", result.message)

    def test_key_file_is_used(self):
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as key_file:
            key_file.write("file-key\n")
            key_path = key_file.name
        self.addCleanup(os.unlink, key_path)
        jarvis_vision._client = None
        self.addCleanup(setattr, jarvis_vision, "_client", None)
        client = MagicMock()
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}), \
             patch("jarvis_vision.KEYFILE", key_path), \
             patch("jarvis_vision.OpenAI", return_value=client) as openai_class:
            self.assertIs(jarvis_vision._get_client(), client)
        openai_class.assert_called_once_with(api_key="file-key", base_url="https://api.groq.com/openai/v1")

    def test_client_error_is_honest_failure(self):
        result, _ = self._ask(Image.new("RGB", (10, 10)), _FakeClient("secret answer", RuntimeError("offline")))
        self.assertFalse(result.ok)
        self.assertIn("RuntimeError", result.message)
        self.assertNotIn("secret answer", result.message)

    def test_empty_answer_is_failure(self):
        result, _ = self._ask(Image.new("RGB", (10, 10)), _FakeClient("  "))
        self.assertFalse(result.ok)
        self.assertIn("no answer", result.message)

    def test_capture_failure(self):
        client = MagicMock()
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_image", return_value=None), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.ask_screen("What?", "active_window")
        self.assertFalse(result.ok)
        client.assert_not_called()

    def test_non_windows_refused(self):
        with patch("jarvis_vision.os.name", "posix"):
            result = jarvis_vision.ask_screen("What?", "screen")
        self.assertFalse(result.ok)
        self.assertIn("Windows", result.message)

    def test_policy_moderate(self):
        self.assertEqual(jarvis_policy.classify_risk("ask_screen"), jarvis_policy.RiskLevel.MODERATE)

    def test_execute_tool_wiring(self):
        client = _FakeClient()
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_image", return_value=Image.new("RGB", (10, 10))), \
             patch("jarvis_vision._get_client", return_value=client), \
             patch("jarvis_windows.audit_action"):
            result = jarvis_tools.execute_tool("ask_screen", {"question": "What is open?", "target": "active_window"})
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
