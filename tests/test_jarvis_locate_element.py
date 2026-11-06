"""Tests for grounded visual UI element location and confirmation."""

import base64
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

import jarvis_policy
import jarvis_tasks
import jarvis_tools
import jarvis_vision


class _FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.create = MagicMock(side_effect=self._create)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def _create(self, **_kwargs):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


class LocateElementTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _locate(self, image, origin, locate_reply, description="the Save button", target="screen", confirm_reply="YES"):
        client = _FakeClient([locate_reply, confirm_reply])
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, origin)), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element(description, target)
        return result, client

    @staticmethod
    def _content(client, index):
        return client.create.call_args_list[index].kwargs["messages"][1]["content"]

    def test_screen_coordinates_corrected(self):
        result, client = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"x": 500, "y": 281}')
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (960, 540))
        self.assertTrue(result.data["confirmed"])
        self.assertIn("(960, 540)", result.message)
        self.assertIn("the Save button", self._content(client, 0)[0]["text"])

    def test_active_window_origin_added(self):
        result, _ = self._locate(Image.new("RGB", (800, 600)), (100, 50), '{"x": 500, "y": 250}', target="active_window")
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (500, 250))

    def test_not_found_skips_confirmation(self):
        result, client = self._locate(Image.new("RGB", (100, 100)), (0, 0), '{"x": -1, "y": -1}')
        self.assertFalse(result.ok)
        self.assertIn("couldn't find", result.message)
        self.assertEqual(client.create.call_count, 1)

    def test_prose_wrapped_json(self):
        result, _ = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), 'At {"x": 500, "y": 281}.')
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (960, 540))

    def test_garbage_answer_fails(self):
        result, _ = self._locate(Image.new("RGB", (100, 100)), (0, 0), "near the top")
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)

    def test_clamped_to_image(self):
        result, _ = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"x": 1000, "y": 620}')
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (1919, 1079))

    def test_confirm_rejects_hallucination(self):
        result, _ = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"x": 500, "y": 281}', description="the Print button", confirm_reply="NO")
        self.assertFalse(result.ok)
        self.assertIn("couldn't confirm", result.message)
        self.assertNotIn("x", result.data)

    def test_confirm_garbage_rejected(self):
        result, _ = self._locate(Image.new("RGB", (100, 100)), (0, 0), '{"x": 500, "y": 500}', confirm_reply="It looks like it.")
        self.assertFalse(result.ok)

    def test_confirm_call_failure_rejected(self):
        result, _ = self._locate(Image.new("RGB", (100, 100)), (0, 0), '{"x": 500, "y": 500}', confirm_reply=RuntimeError("offline"))
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)

    def test_confirmation_request_content(self):
        result, client = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"x": 500, "y": 281}')
        self.assertTrue(result.ok)
        first, second = self._content(client, 0), self._content(client, 1)
        self.assertIn("red circle", second[0]["text"])
        self.assertIn("the Save button", second[0]["text"])
        self.assertIn("YES", second[0]["text"])
        first_uri = next(p["image_url"]["url"] for p in first if p["type"] == "image_url")
        second_uri = next(p["image_url"]["url"] for p in second if p["type"] == "image_url")
        self.assertNotEqual(first_uri, second_uri)

    def test_marker_drawn(self):
        result, client = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"x": 500, "y": 281}')
        self.assertTrue(result.ok)
        content = self._content(client, 1)
        uri = next(part["image_url"]["url"] for part in content if part["type"] == "image_url")
        with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as marked:
            self.assertTrue(any(red > 180 and green < 90 and blue < 90 for red, green, blue in marked.convert("RGB").getdata()))

    def test_bad_target_and_empty_description(self):
        client = MagicMock()
        with patch("jarvis_vision._get_client", return_value=client):
            self.assertFalse(jarvis_vision.locate_element("Save", "desktop").ok)
            self.assertFalse(jarvis_vision.locate_element("  ", "screen").ok)
        client.assert_not_called()

    def test_capture_failure(self):
        client = MagicMock()
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=None), patch("jarvis_vision._get_client", return_value=client):
            self.assertFalse(jarvis_vision.locate_element("Save", "screen").ok)
        client.assert_not_called()

    def test_missing_key(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}), patch("jarvis_vision.KEYFILE", "missing-key-file"), patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(Image.new("RGB", (10, 10)), (0, 0))):
            result = jarvis_vision.locate_element("Save", "screen")
        self.assertFalse(result.ok)
        self.assertIn("key", result.message.lower())

    def test_policy_moderate(self):
        self.assertEqual(jarvis_policy.classify_risk("locate_element"), jarvis_policy.RiskLevel.MODERATE)

    def test_plan_step_refused(self):
        step = jarvis_tasks.TaskStep("find", "Find", "locate_element", {"description": "Save", "target": "screen"})
        self.assertFalse(jarvis_tasks.validate_plan([step])[0])

    def test_execute_tool_wiring(self):
        client = _FakeClient(['{"x": 500, "y": 281}', "YES"])
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(Image.new("RGB", (1920, 1080)), (0, 0))), patch("jarvis_vision._get_client", return_value=client), patch("jarvis_windows.audit_action"):
            result = jarvis_tools.execute_tool("locate_element", {"description": "the SAVE button", "target": "screen"})
        self.assertTrue(result.ok)
        self.assertIn("x", result.data)
        self.assertIn("y", result.data)


if __name__ == "__main__":
    unittest.main()
