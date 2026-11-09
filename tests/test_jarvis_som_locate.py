"""TASK-027C: Tests for SoM grid locate and refine."""

import base64
import io
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


class SomLocateTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    # 1. grid math (144 markers, marker 1 = (40,40), marker 144 = (1240,680) at 1280x720/80px; 144 at 480x480/40px)
    def test_grid_math(self):
        img_720p = Image.new("RGB", (1280, 720))
        marked_720p, markers_720p = jarvis_vision._draw_som_grid(img_720p, 80)
        self.assertEqual(len(markers_720p), 144)
        self.assertEqual(markers_720p[0], (40, 40))
        self.assertEqual(markers_720p[143], (1240, 680))

        img_crop = Image.new("RGB", (480, 480))
        marked_crop, markers_crop = jarvis_vision._draw_som_grid(img_crop, 40)
        self.assertEqual(len(markers_crop), 144)
        self.assertEqual(markers_crop[0], (20, 20))
        self.assertEqual(markers_crop[143], (460, 460))

    # 2. scripted {"marker": 144} -> candidate (1860, 1020) on a 1920x1080 capture + cell-confirm image carries NO grid
    def test_scripted_marker_144_candidate_and_cell_confirm_no_grid(self):
        # Create a plain white image
        img = Image.new("RGB", (1920, 1080), (255, 255, 255))
        # Marker 144 on 1280x720 (spacing 80) is (1240, 680). Scaled by 1.5 -> (1860, 1020).
        # Crop centered at (1860, 1020): crop_x0 = 1440, crop_y0 = 600.
        # Refine marker 1 on 480x480 (spacing 40) is (20, 20) -> final point (1460, 620).
        replies = [
            '{"marker": 144}',  # coarse locate
            "YES",              # cell confirm
            '{"marker": 1}',    # refine locate
            "YES",              # refine confirm
        ]
        client = _FakeClient(replies)
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", return_value=(img, (0, 0))), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the element", "screen")
        self.assertTrue(result.ok, result.message)
        # Verify refine final point corresponds to candidate (1860, 1020) -> crop (1440, 600) + refine (20, 20)
        self.assertEqual((result.data["x"], result.data["y"]), (1460, 620))

        # Check call 1 (cell confirm image) carries NO grid
        cell_call = client.create.call_args_list[1]
        self.assertIn("red rectangle", cell_call.kwargs["messages"][1]["content"][0]["text"])
        cell_uri = cell_call.kwargs["messages"][1]["content"][1]["image_url"]["url"]
        cell_bytes = base64.b64decode(cell_uri.split(",", 1)[1])
        with Image.open(io.BytesIO(cell_bytes)) as cell_img:
            # Marker 1 in a grid-marked image would have a red dot at (40, 40) and black text box.
            # In cell_img, (40, 40) must be white (255, 255, 255) since cell_image is a copy of sent with only red rect.
            r, g, b = cell_img.getpixel((40, 40))
            self.assertGreater(r, 200)
            self.assertGreater(g, 200)
            self.assertGreater(b, 200)

    # 3. cell-NO -> attempt 2 succeeds (counts + 2 captures)
    def test_cell_no_attempt2_succeeds(self):
        img = Image.new("RGB", (1920, 1080))
        replies = [
            '{"marker": 72}',   # Attempt 1: coarse locate
            "NO",               # Attempt 1: cell confirm NO -> retry
            '{"marker": 72}',   # Attempt 2: coarse locate
            "YES",              # Attempt 2: cell confirm YES
            '{"marker": 1}',    # Attempt 2: refine locate
            "YES",              # Attempt 2: refine confirm YES
        ]
        client = _FakeClient(replies)
        capture_mock = MagicMock(side_effect=[(img, (0, 0)), (img, (0, 0))])
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the button", "screen")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(client.create.call_count, 6)
        self.assertEqual(capture_mock.call_count, 2)

    # 4. {"marker": 9999} -> fatal, 1 capture
    def test_marker_out_of_range_fatal_one_capture(self):
        img = Image.new("RGB", (1920, 1080))
        client = _FakeClient(['{"marker": 9999}'])
        capture_mock = MagicMock(return_value=(img, (0, 0)))
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the button", "screen")
        self.assertFalse(result.ok)
        self.assertIn("couldn't work out where", result.message)
        self.assertEqual(capture_mock.call_count, 1)

    # 5. {-1} twice -> not-found, 2 captures
    def test_absent_twice_not_found_two_captures(self):
        img = Image.new("RGB", (1920, 1080))
        client = _FakeClient(['{"marker": -1}', '{"marker": -1}'])
        capture_mock = MagicMock(side_effect=[(img, (0, 0)), (img, (0, 0))])
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", side_effect=capture_mock), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the button", "screen")
        self.assertFalse(result.ok)
        self.assertIn("couldn't find", result.message)
        self.assertEqual(capture_mock.call_count, 2)

    # 6. refine marker -> crop origin + marker centre
    def test_refine_marker_crop_origin_plus_marker_centre(self):
        img = Image.new("RGB", (1920, 1080))
        # Marker 72 on 1280x720: cx = 600, cy = 360 -> candidate (900, 540).
        # Crop origin: crop_x0 = 900 - 240 = 660, crop_y0 = 540 - 240 = 300.
        # Refine marker 15 on 480x480 (spacing 40, cols 12):
        # row = 1, col = 2 -> cx = 2 * 40 + 20 = 100, cy = 1 * 40 + 20 = 60.
        # Expected point: (660 + 100, 300 + 60) = (760, 360).
        replies = [
            '{"marker": 72}',
            "YES",
            '{"marker": 15}',
            "YES",
        ]
        client = _FakeClient(replies)
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", return_value=(img, (0, 0))), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the button", "screen")
        self.assertTrue(result.ok, result.message)
        self.assertEqual((result.data["x"], result.data["y"]), (760, 360))

    # 7. refine {-1} -> unconfirmed
    def test_refine_absent_unconfirmed(self):
        img = Image.new("RGB", (1920, 1080))
        replies = [
            '{"marker": 72}',
            "YES",
            '{"marker": -1}',
        ]
        client = _FakeClient(replies)
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", return_value=(img, (0, 0))), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the button", "screen")
        self.assertFalse(result.ok)
        self.assertNotIn("x", result.data)
        self.assertIn("couldn't confirm", result.message)

    # 8. Window-target tests pass with scripts UNCHANGED
    def test_window_target_scripts_unchanged(self):
        img = Image.new("RGB", (800, 600))
        # Window target uses coordinate path: {"x": 500, "y": 250}
        replies = [
            '{"x": 500, "y": 250}',
            "YES",
        ]
        client = _FakeClient(replies)
        with patch("jarvis_vision.os.name", "nt"), \
             patch("jarvis_vision._capture_with_origin", return_value=(img, (100, 50))), \
             patch("jarvis_vision._get_client", return_value=client):
            result = jarvis_vision.locate_element("the button", "active_window")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(client.create.call_count, 2)
        self.assertEqual((result.data["x"], result.data["y"]), (500, 250))


if __name__ == "__main__":
    unittest.main()
