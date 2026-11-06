"""Tests for grounded visual UI element location."""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

import jarvis_policy
import jarvis_tasks
import jarvis_tools
import jarvis_vision


class _FakeClient:
    def __init__(self, content):
        self.create = MagicMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        ))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))


class LocateElementTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _locate(self, image, origin, content, description="the Save button", target="screen"):
        client = _FakeClient(content)
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", return_value=(image, origin)), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element(description, target)
        return result, client

    @staticmethod
    def _request_content(client):
        return client.create.call_args.kwargs["messages"][1]["content"]

    def test_screen_coordinates_corrected(self):
        result, client = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"x": 500, "y": 281}')
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (960, 540))
        self.assertIn("(960, 540)", result.message)
        content = self._request_content(client)
        self.assertIn("the Save button", content[0]["text"])
        self.assertTrue(any(part["type"] == "image_url" for part in content))

    def test_active_window_origin_added(self):
        result, _ = self._locate(Image.new("RGB", (800, 600)), (100, 50), '{"x": 500, "y": 250}', target="active_window")
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (500, 250))

    def test_not_found(self):
        result, _ = self._locate(Image.new("RGB", (100, 100)), (0, 0), '{"x": -1, "y": -1}')
        self.assertFalse(result.ok)
        self.assertIn("couldn't find", result.message)

    def test_prose_wrapped_json(self):
        result, _ = self._locate(
            Image.new("RGB", (1920, 1080)), (0, 0),
            'The element is at {"x": 500, "y": 281} in the image.',
        )
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (960, 540))

    def test_garbage_answer_fails(self):
        result, _ = self._locate(Image.new("RGB", (100, 100)), (0, 0), "It is near the top.")
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)

    def test_clamped_to_image(self):
        result, _ = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"x": 1000, "y": 620}')
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (1919, 1079))

    def test_bad_target_and_empty_description(self):
        client = MagicMock()
        with patch("jarvis_vision._get_client", return_value=client):
            bad_target = jarvis_vision.locate_element("Save", "desktop")
            empty_description = jarvis_vision.locate_element("  ", "screen")
        self.assertFalse(bad_target.ok)
        self.assertFalse(empty_description.ok)
        client.assert_not_called()

    def test_capture_failure(self):
        client = MagicMock()
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", return_value=None), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("Save", "screen")
        self.assertFalse(result.ok)
        client.assert_not_called()

    def test_missing_key(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}), \
             patch("jarvis_vision.KEYFILE", "missing-key-file"), \
             patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", return_value=(Image.new("RGB", (10, 10)), (0, 0))):
            result = jarvis_vision.locate_element("Save", "screen")
        self.assertFalse(result.ok)
        self.assertIn("key", result.message.lower())

    def test_policy_moderate(self):
        self.assertEqual(jarvis_policy.classify_risk("locate_element"), jarvis_policy.RiskLevel.MODERATE)

    def test_plan_step_refused(self):
        step = jarvis_tasks.TaskStep("find", "Find", "locate_element", {"description": "Save", "target": "screen"})
        valid, _ = jarvis_tasks.validate_plan([step])
        self.assertFalse(valid)

    def test_execute_tool_wiring(self):
        client = _FakeClient('{"x": 500, "y": 281}')
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", return_value=(Image.new("RGB", (1920, 1080)), (0, 0))), \
             patch("jarvis_vision._get_client", return_value=client), \
             patch("jarvis_windows.audit_action"):
            result = jarvis_tools.execute_tool("locate_element", {"description": "the SAVE button", "target": "screen"})
        self.assertTrue(result.ok)
        self.assertIn("x", result.data)
        self.assertIn("y", result.data)


if __name__ == "__main__":
    unittest.main()
