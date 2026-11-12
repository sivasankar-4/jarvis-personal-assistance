"""Tests for grounded visual UI element location and confirmation."""

import base64
import io
import json
import os
import re
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


class LocateElementTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def _locate(self, image, origin, locate_reply, description="the Save button", target="screen", confirm_reply="YES", refine_reply=None, refine_confirm_reply="YES"):
        replies = [locate_reply, confirm_reply]
        if target == "screen":
            if refine_reply is None:
                refine_reply = '{"marker": 15}'
            replies.extend([refine_reply, refine_confirm_reply])
        client = _FakeClient(replies)
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, origin)), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element(description, target)
        return result, client

    @staticmethod
    def _content(client, index):
        return client.create.call_args_list[index].kwargs["messages"][1]["content"]

    def test_screen_coordinates_corrected(self):
        # Marker 72 on 1280x720 -> candidate (900, 540). Crop origin (660, 300).
        # Refine marker 15 on 480x480 -> (100, 60). Final point: (760, 360).
        result, client = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"marker": 72}', refine_reply='{"marker": 15}')
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (760, 360))
        self.assertTrue(result.data["confirmed"])
        self.assertIn("(760, 360)", result.message)
        self.assertIn("the Save button", self._content(client, 0)[0]["text"])

    def test_active_window_origin_added(self):
        result, _ = self._locate(Image.new("RGB", (800, 600)), (100, 50), '{"x": 500, "y": 250}', target="active_window")
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (500, 250))

    def test_not_found_skips_confirmation(self):
        # Both attempts return absent; call_count = 2 (one locate per attempt, no confirm on absent)
        client = _FakeClient(['{"marker": -1}', '{"marker": -1}'])
        image = Image.new("RGB", (100, 100))
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, (0, 0))), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertFalse(result.ok)
        self.assertIn("couldn't find", result.message)
        self.assertEqual(client.create.call_count, 2)

    def test_prose_wrapped_json(self):
        result, _ = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), 'At {"marker": 72}.', refine_reply='{"marker": 15}')
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (760, 360))

    def test_garbage_answer_fails(self):
        result, _ = self._locate(Image.new("RGB", (100, 100)), (0, 0), "near the top")
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)

    def test_clamped_to_image(self):
        # Marker 144 -> candidate (1860, 1020). Crop origin (1440, 600).
        # Refine marker 144 -> (460, 460). Final: (1900, 1060).
        result, _ = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"marker": 144}', refine_reply='{"marker": 144}')
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (1900, 1060))

    def test_confirm_rejects_hallucination(self):
        # Both attempts: locate found, confirm NO -> retry, attempt 2 same -> final unconfirmed
        client = _FakeClient(['{"marker": 72}', "NO", '{"marker": 72}', "NO"])
        image = Image.new("RGB", (1920, 1080))
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, (0, 0))), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Print button", "screen")
        self.assertFalse(result.ok)
        self.assertIn("couldn't confirm", result.message)
        self.assertNotIn("x", result.data)

    def test_confirm_garbage_rejected(self):
        # Both attempts: garbage confirm -> not confirmed -> retry; attempt 2 same -> final unconfirmed
        client = _FakeClient(['{"marker": 72}', "It looks like it.", '{"marker": 72}', "It looks like it."])
        image = Image.new("RGB", (100, 100))
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, (0, 0))), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertFalse(result.ok)

    def test_confirm_call_failure_rejected(self):
        # Both attempts: confirm raises -> not confirmed -> retry; attempt 2 same -> final unconfirmed
        client = _FakeClient(['{"marker": 72}', RuntimeError("offline"), '{"marker": 72}', RuntimeError("offline")])
        image = Image.new("RGB", (100, 100))
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, (0, 0))), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)

    def test_confirmation_request_content(self):
        result, client = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"marker": 72}', refine_reply='{"marker": 15}')
        self.assertTrue(result.ok)
        first, second = self._content(client, 0), self._content(client, 1)
        self.assertIn("red rectangle", second[0]["text"])
        self.assertIn("the Save button", second[0]["text"])
        self.assertIn("YES", second[0]["text"])
        first_uri = next(p["image_url"]["url"] for p in first if p["type"] == "image_url")
        second_uri = next(p["image_url"]["url"] for p in second if p["type"] == "image_url")
        self.assertNotEqual(first_uri, second_uri)

    def test_marker_drawn(self):
        result, client = self._locate(Image.new("RGB", (1920, 1080)), (0, 0), '{"marker": 72}', refine_reply='{"marker": 15}')
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
        client = _FakeClient(['{"marker": 72}', "YES", '{"marker": 15}', "YES"])
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(Image.new("RGB", (1920, 1080)), (0, 0))), patch("jarvis_vision._get_client", return_value=client), patch("jarvis_windows.audit_action"):
            result = jarvis_tools.execute_tool("locate_element", {"description": "the SAVE button", "target": "screen"})
        self.assertTrue(result.ok)
        self.assertIn("x", result.data)
        self.assertIn("y", result.data)

    def test_crop_geometry_centre_candidate_refine(self):
        # Marker 72 -> (900, 540). Crop 480x480 around it: (660, 300).
        # Refine marker 15 -> (100, 60). Final point: (760, 360).
        result, client = self._locate(
            Image.new("RGB", (1920, 1080)),
            (0, 0),
            '{"marker": 72}',
            refine_reply='{"marker": 15}',
            refine_confirm_reply="YES",
        )
        self.assertTrue(result.ok)
        # Verify refine payload image is 480x480
        crop_uri = self._content(client, 2)[1]["image_url"]["url"]
        crop_bytes = base64.b64decode(crop_uri.split(",", 1)[1])
        with Image.open(io.BytesIO(crop_bytes)) as crop_img:
            self.assertEqual(crop_img.size, (480, 480))
        self.assertEqual((result.data["x"], result.data["y"]), (760, 360))

    def test_corner_clamp(self):
        # Marker 144 -> candidate (1860, 1020). Crop origin (1440, 600).
        # Refine marker 1 -> (20, 20). Final point: (1460, 620).
        result, client = self._locate(
            Image.new("RGB", (1920, 1080)),
            (0, 0),
            '{"marker": 144}',
            refine_reply='{"marker": 1}',
            refine_confirm_reply="YES",
        )
        self.assertTrue(result.ok)
        crop_uri = self._content(client, 2)[1]["image_url"]["url"]
        crop_bytes = base64.b64decode(crop_uri.split(",", 1)[1])
        with Image.open(io.BytesIO(crop_bytes)) as crop_img:
            self.assertEqual(crop_img.size, (480, 480))
        self.assertEqual((result.data["x"], result.data["y"]), (1460, 620))

    def test_refine_negative_coordinates_unconfirmed(self):
        # refine {-1} -> unconfirmed, no x/y in data
        result, _ = self._locate(
            Image.new("RGB", (1920, 1080)),
            (0, 0),
            '{"marker": 72}',
            refine_reply='{"marker": -1}',
        )
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)
        self.assertNotIn("y", result.data)
        self.assertIn("couldn't confirm", result.message)

    def test_crop_confirm_no_unconfirmed(self):
        # crop-confirm NO -> unconfirmed
        result, _ = self._locate(
            Image.new("RGB", (1920, 1080)),
            (0, 0),
            '{"marker": 72}',
            refine_reply='{"marker": 15}',
            refine_confirm_reply="NO",
        )
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)
        self.assertNotIn("y", result.data)
        self.assertIn("couldn't confirm", result.message)

    def test_refine_yes_carries_refined_point(self):
        # Marker 72 -> candidate (900, 540). Crop origin (660, 300).
        # Refine marker 1 -> (20, 20). Refined point: (680, 320) != (900, 540).
        result, _ = self._locate(
            Image.new("RGB", (1920, 1080)),
            (0, 0),
            '{"marker": 72}',
            refine_reply='{"marker": 1}',
            refine_confirm_reply="YES",
        )
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (680, 320))
        self.assertNotEqual((result.data["x"], result.data["y"]), (900, 540))
        self.assertTrue(result.data["confirmed"])

    def test_active_window_target_no_refine(self):
        # active_window -> exactly 2 model calls, no refine
        result, client = self._locate(
            Image.new("RGB", (800, 600)),
            (100, 50),
            '{"x": 500, "y": 250}',
            target="active_window",
            confirm_reply="YES",
        )
        self.assertTrue(result.ok)
        self.assertEqual(client.create.call_count, 2)


if __name__ == "__main__":
    unittest.main()
