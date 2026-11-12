"""Tests for UIA branch in locate_element (TASK-027E)."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

import jarvis_vision


class _FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.create = MagicMock(side_effect=self._create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def _create(self, **kwargs):
        if self.replies:
            reply = self.replies.pop(0)
        else:
            messages = kwargs.get("messages", [])
            content = messages[-1]["content"] if messages else ""
            txt = ""
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        txt += part.get("text", "")
            elif isinstance(content, str):
                txt = content
            if "YES or NO" in txt:
                reply = "YES"
            elif "marker" in txt:
                reply = '{"marker": 1}'
            else:
                reply = '{"x": 500, "y": 500}'
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


class JarvisUiaBranchTests(unittest.TestCase):
    def test_07_uia_point_found_short_circuits_vision(self):
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_uia.find_element_point", return_value=(628, 1050)), \
             patch("jarvis_vision._capture_with_origin", side_effect=RuntimeError("capture called")), \
             patch("jarvis_vision._get_client", side_effect=RuntimeError("client called")):
            result = jarvis_vision.locate_element("the Start button", "screen")

        self.assertTrue(result.ok)
        self.assertEqual(result.data.get("model"), "uia")
        self.assertEqual(result.data.get("x"), 628)
        self.assertEqual(result.data.get("y"), 1050)
        self.assertEqual(result.data.get("target"), "screen")
        self.assertTrue(result.data.get("confirmed"))

    def test_08_uia_none_falls_back_to_vision(self):
        client = _FakeClient(['{"marker": 72}', 'YES', '{"marker": 15}', 'YES'])
        image = Image.new("RGB", (1920, 1080))

        with patch("jarvis_vision.os.name", "nt"), \
             patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False), \
             patch("jarvis_uia.find_element_point", return_value=None), \
             patch("jarvis_vision._capture_with_origin", return_value=(image, (0, 0))) as mock_capture, \
             patch("jarvis_vision._get_client", return_value=client) as mock_get_client:
            result = jarvis_vision.locate_element("the Start button", "screen")

        self.assertTrue(mock_capture.called)
        self.assertTrue(mock_get_client.called)
        self.assertTrue(result.ok)
        self.assertNotEqual(result.data.get("model"), "uia")
        self.assertEqual(result.data.get("model"), jarvis_vision._vision_model())


if __name__ == "__main__":
    unittest.main()
