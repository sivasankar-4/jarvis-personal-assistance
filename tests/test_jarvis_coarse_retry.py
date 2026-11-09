"""TASK-027B: coarse-retry tests for locate_element."""

import json
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
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))])


def _refine_reply_for(image, coarse_json_str):
    """Compute the auto-refine reply (identity: refine == coarse point within crop)."""
    w, h = image.size
    coords = json.loads(coarse_json_str)
    rx, ry = coords["x"], coords["y"]
    cx = min(max(round(rx / 1000 * w), 0), w - 1)
    cy = min(max(round(ry / 1000 * w), 0), h - 1)
    side = min(480, w, h)
    x0 = min(max(cx - side // 2, 0), w - side)
    y0 = min(max(cy - side // 2, 0), h - side)
    rel_x = cx - x0
    rel_y = cy - y0
    return json.dumps({"x": round(rel_x / side * 1000), "y": round(rel_y / side * 1000)})


class CoarseRetryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    # --- (1) confirm-NO then confirmed -> screen success via refine, 6 model calls, 2 captures ---
    def test_confirm_no_then_confirmed_screen_success(self):
        img = Image.new("RGB", (1920, 1080))
        coarse = '{"x": 500, "y": 281}'
        refine_r = _refine_reply_for(img, coarse)
        # Attempt 1: locate found, confirm NO -> retry
        # Attempt 2: locate found, confirm YES -> enter refine block -> refine locate, refine confirm YES
        replies = [coarse, "NO", coarse, "YES", refine_r, "YES"]
        client = _FakeClient(replies)
        capture_mock = MagicMock(side_effect=[(img, (0, 0)), (img, (0, 0))])
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(client.create.call_count, 6)
        self.assertEqual(capture_mock.call_count, 2)

    # --- (2) absent then found (active_window) -> success, 3 calls, 2 captures ---
    def test_absent_then_found_active_window(self):
        img = Image.new("RGB", (800, 600))
        # Attempt 1: absent (-1,-1), no confirm call; Attempt 2: found + YES
        replies = ['{"x": -1, "y": -1}', '{"x": 500, "y": 250}', "YES"]
        client = _FakeClient(replies)
        capture_mock = MagicMock(side_effect=[(img, (100, 50)), (img, (100, 50))])
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Start button", "active_window")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(client.create.call_count, 3)
        self.assertEqual(capture_mock.call_count, 2)

    # --- (3) NO twice -> second unconfirmed failure, no coords ---
    def test_confirm_no_twice_gives_unconfirmed_failure(self):
        img = Image.new("RGB", (1920, 1080))
        replies = ['{"x": 500, "y": 281}', "NO", '{"x": 500, "y": 281}', "NO"]
        client = _FakeClient(replies)
        capture_mock = MagicMock(side_effect=[(img, (0, 0)), (img, (0, 0))])
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)
        self.assertIn("couldn't confirm", result.message)
        self.assertEqual(capture_mock.call_count, 2)

    # --- (4) malformed answer -> parse wording, capture count stays 1 ---
    def test_malformed_answer_fatal_one_capture(self):
        img = Image.new("RGB", (100, 100))
        replies = ["near the top-left corner"]  # no JSON -> fatal
        client = _FakeClient(replies)
        capture_mock = MagicMock(return_value=(img, (0, 0)))
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertFalse(result.ok)
        self.assertIn("couldn't work out where", result.message)
        self.assertEqual(capture_mock.call_count, 1)

    # --- (5) locate API exception -> fatal, 1 capture ---
    def test_locate_api_exception_fatal_one_capture(self):
        img = Image.new("RGB", (100, 100))
        replies = [RuntimeError("network error")]
        client = _FakeClient(replies)
        capture_mock = MagicMock(return_value=(img, (0, 0)))
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertFalse(result.ok)
        self.assertIn("RuntimeError", result.message)
        self.assertEqual(capture_mock.call_count, 1)

    # --- (6) first-attempt screen success unchanged: 4 calls, 1 capture ---
    def test_first_attempt_screen_success_4_calls_1_capture(self):
        img = Image.new("RGB", (1920, 1080))
        coarse = '{"x": 500, "y": 281}'
        refine_r = _refine_reply_for(img, coarse)
        # coarse-locate, coarse-confirm YES, refine-locate, refine-confirm YES = 4 calls
        replies = [coarse, "YES", refine_r, "YES"]
        client = _FakeClient(replies)
        capture_mock = MagicMock(return_value=(img, (0, 0)))
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(client.create.call_count, 4)
        self.assertEqual(capture_mock.call_count, 1)

    # --- (7) attempt-2 confirmed but refine-confirm NO -> fail-closed, 2 captures, refine ran once ---
    def test_attempt2_confirmed_refine_confirm_no_fail_closed(self):
        img = Image.new("RGB", (1920, 1080))
        coarse = '{"x": 500, "y": 281}'
        refine_r = _refine_reply_for(img, coarse)
        # Attempt 1: NO -> retry; Attempt 2: YES -> refine -> refine-confirm NO -> fail
        replies = [coarse, "NO", coarse, "YES", refine_r, "NO"]
        client = _FakeClient(replies)
        capture_mock = MagicMock(side_effect=[(img, (0, 0)), (img, (0, 0))])
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the Save button", "screen")
        self.assertFalse(result.ok)
        self.assertIn("couldn't confirm", result.message)
        # 6 calls total: 2 coarse-locate + 2 coarse-confirm + 1 refine-locate + 1 refine-confirm
        self.assertEqual(client.create.call_count, 6)
        self.assertEqual(capture_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
