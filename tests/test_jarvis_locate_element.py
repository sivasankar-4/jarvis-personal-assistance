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
        if target == "screen" and refine_reply is None:
            m = re.search(r"\{.*?\}", str(locate_reply))
            if m:
                try:
                    coords = json.loads(m.group(0))
                    rx, ry = coords.get("x", 500), coords.get("y", 500)
                    if rx >= 0 and ry >= 0:
                        w, h = image.size
                        cx = min(max(round(rx / 1000 * w), 0), w - 1)
                        cy = min(max(round(ry / 1000 * w), 0), h - 1)
                        side = min(480, w, h)
                        x0 = min(max(cx - side // 2, 0), w - side)
                        y0 = min(max(cy - side // 2, 0), h - side)
                        rel_x = cx - x0
                        rel_y = cy - y0
                        ref_rx = round(rel_x / side * 1000)
                        ref_ry = round(rel_y / side * 1000)
                        refine_reply = json.dumps({"x": ref_rx, "y": ref_ry})
                except Exception:
                    pass
        replies = [locate_reply, confirm_reply]
        if refine_reply is not None:
            replies.extend([refine_reply, refine_confirm_reply])
        client = _FakeClient(replies)
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
        # Both attempts return absent; call_count = 2 (one locate per attempt, no confirm on absent)
        client = _FakeClient(['{"x": -1, "y": -1}', '{"x": -1, "y": -1}'])
        image = Image.new("RGB", (100, 100))
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, (0, 0))), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertFalse(result.ok)
        self.assertIn("couldn't find", result.message)
        self.assertEqual(client.create.call_count, 2)

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
        # Both attempts: locate found, confirm NO -> retry, attempt 2 same -> final unconfirmed
        client = _FakeClient(['{"x": 500, "y": 281}', "NO", '{"x": 500, "y": 281}', "NO"])
        image = Image.new("RGB", (1920, 1080))
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, (0, 0))), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Print button", "screen")
        self.assertFalse(result.ok)
        self.assertIn("couldn't confirm", result.message)
        self.assertNotIn("x", result.data)

    def test_confirm_garbage_rejected(self):
        # Both attempts: garbage confirm -> not confirmed -> retry; attempt 2 same -> final unconfirmed
        client = _FakeClient(['{"x": 500, "y": 500}', "It looks like it.", '{"x": 500, "y": 500}', "It looks like it."])
        image = Image.new("RGB", (100, 100))
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, (0, 0))), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertFalse(result.ok)

    def test_confirm_call_failure_rejected(self):
        # Both attempts: confirm raises -> not confirmed -> retry; attempt 2 same -> final unconfirmed
        client = _FakeClient(['{"x": 500, "y": 500}', RuntimeError("offline"), '{"x": 500, "y": 500}', RuntimeError("offline")])
        image = Image.new("RGB", (100, 100))
        with patch("jarvis_vision.os.name", "nt"), patch("jarvis_vision._capture_with_origin", return_value=(image, (0, 0))), patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
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

    def test_crop_geometry_centre_candidate_refine(self):
        # centre candidate -> 480x480 crop, scripted refine (500, 500) -> final point = crop centre
        result, client = self._locate(
            Image.new("RGB", (1920, 1080)),
            (0, 0),
            '{"x": 500, "y": 281}',
            refine_reply='{"x": 500, "y": 500}',
            refine_confirm_reply="YES",
        )
        self.assertTrue(result.ok)
        # Verify refine payload image is 480x480
        crop_uri = self._content(client, 2)[1]["image_url"]["url"]
        crop_bytes = base64.b64decode(crop_uri.split(",", 1)[1])
        with Image.open(io.BytesIO(crop_bytes)) as crop_img:
            self.assertEqual(crop_img.size, (480, 480))
        # Final point is crop centre (720 + 240, 300 + 240) = (960, 540)
        self.assertEqual((result.data["x"], result.data["y"]), (960, 540))

    def test_corner_clamp(self):
        # candidate (10, 1070) -> crop origin (0, 600)
        img = Image.new("RGB", (1000, 1080), (255, 255, 255))
        # Paint a red region at the crop origin (0, 600)
        img.paste(Image.new("RGB", (50, 50), (255, 0, 0)), (0, 600))
        result, client = self._locate(
            img,
            (0, 0),
            '{"x": 10, "y": 1070}',
            refine_reply='{"x": 0, "y": 0}',
            refine_confirm_reply="YES",
        )
        self.assertTrue(result.ok)
        # Verify the crop starts at (0, 600)
        crop_uri = self._content(client, 2)[1]["image_url"]["url"]
        crop_bytes = base64.b64decode(crop_uri.split(",", 1)[1])
        with Image.open(io.BytesIO(crop_bytes)) as crop_img:
            self.assertEqual(crop_img.size, (480, 480))
            r, g, b = crop_img.getpixel((25, 25))
            self.assertGreater(r, 200)
            self.assertLess(g, 50)
            self.assertLess(b, 50)
        # Refine (0, 0) relative to crop gives final point at crop origin (0, 600)
        self.assertEqual((result.data["x"], result.data["y"]), (0, 600))

    def test_refine_negative_coordinates_unconfirmed(self):
        # refine {-1, -1} -> unconfirmed, no x/y in data
        result, _ = self._locate(
            Image.new("RGB", (1920, 1080)),
            (0, 0),
            '{"x": 500, "y": 281}',
            refine_reply='{"x": -1, "y": -1}',
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
            '{"x": 500, "y": 281}',
            refine_reply='{"x": 500, "y": 500}',
            refine_confirm_reply="NO",
        )
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)
        self.assertNotIn("y", result.data)
        self.assertIn("couldn't confirm", result.message)

    def test_refine_yes_carries_refined_point(self):
        # YES -> data carries the REFINED point
        # Coarse candidate at (960, 540). Crop origin is (720, 300).
        # Refine (250, 750) -> refine_x = 120, refine_y = 360.
        # Refined point = (720 + 120, 300 + 360) = (840, 660).
        result, _ = self._locate(
            Image.new("RGB", (1920, 1080)),
            (0, 0),
            '{"x": 500, "y": 281}',
            refine_reply='{"x": 250, "y": 750}',
            refine_confirm_reply="YES",
        )
        self.assertTrue(result.ok)
        self.assertEqual((result.data["x"], result.data["y"]), (840, 660))
        self.assertNotEqual((result.data["x"], result.data["y"]), (960, 540))
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
