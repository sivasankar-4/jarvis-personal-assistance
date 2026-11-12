"""Tests for YouTube video lookup and playback reliability."""

from __future__ import annotations

import io
import os
import unittest
from unittest.mock import MagicMock, patch

import jarvis_tools
import jarvis_windows


class FakeHTTPResponse:
    def __init__(self, content: bytes, status: int = 200):
        self._content = content
        self.status = status

    def read(self) -> bytes:
        return self._content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class TestJarvisPlayYoutube(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "yt_results_lofi.html")
        with open(fixture_path, "rb") as f:
            cls.lofi_html_bytes = f.read()

    @patch("urllib.request.urlopen")
    def test_01_first_youtube_video_fixture_success(self, mock_urlopen):
        mock_urlopen.return_value = FakeHTTPResponse(self.lofi_html_bytes)

        video = jarvis_tools._first_youtube_video("lofi")
        self.assertIsNotNone(video)
        self.assertEqual(video["video_id"], "KumAuTVPORg")
        self.assertEqual(
            video["title"],
            "Trending Songs Playlist \U0001f940\U0001f3a7 // Slowed + Reverb // Non Stop Hindi Lofi Jukebox 2026",
        )
        self.assertEqual(video["url"], "https://www.youtube.com/watch?v=KumAuTVPORg")

    @patch("urllib.request.urlopen")
    def test_02_first_youtube_video_no_video_renderer(self, mock_urlopen):
        html = b'<html><body><script>var ytInitialData = {"contents": {"twoColumnSearchResultsRenderer": {}}};</script></body></html>'
        mock_urlopen.return_value = FakeHTTPResponse(html)

        video = jarvis_tools._first_youtube_video("empty")
        self.assertIsNone(video)

    @patch("urllib.request.urlopen")
    def test_03_first_youtube_video_skip_invalid_video_id_length(self, mock_urlopen):
        html = (
            b'<html><body><script>var ytInitialData = {"contents": ['
            b'{"videoRenderer": {"videoId": "short", "title": {"runs": [{"text": "Too Short"}]}}},'
            b'{"videoRenderer": {"videoId": "toolongvideoid123", "title": {"runs": [{"text": "Too Long"}]}}},'
            b'{"videoRenderer": {"videoId": "valid_id123", "title": {"runs": [{"text": "Valid Target"}]}}}'
            b']};</script></body></html>'
        )
        mock_urlopen.return_value = FakeHTTPResponse(html)

        video = jarvis_tools._first_youtube_video("search")
        self.assertIsNotNone(video)
        self.assertEqual(video["video_id"], "valid_id123")
        self.assertEqual(video["title"], "Valid Target")
        self.assertEqual(video["url"], "https://www.youtube.com/watch?v=valid_id123")

    @patch("time.sleep")
    @patch("jarvis_vision.ask_screen")
    @patch("jarvis_windows.open_website")
    @patch("jarvis_tools._first_youtube_video")
    def test_04_play_youtube_success(self, mock_first_video, mock_open, mock_ask, mock_sleep):
        mock_first_video.return_value = {
            "video_id": "KumAuTVPORg",
            "title": "Lofi Hip Hop Radio",
            "url": "https://www.youtube.com/watch?v=KumAuTVPORg",
        }
        mock_open.return_value = jarvis_windows.ToolResult(True, "Opened YouTube.")
        mock_ask.return_value = jarvis_windows.ToolResult(True, "YES - playing")

        res = jarvis_tools.play_youtube("lofi")

        self.assertTrue(res.ok)
        self.assertEqual(res.message, "Playing lofi on YouTube.")
        self.assertEqual(
            mock_open.call_args[0][0],
            "https://www.youtube.com/watch?v=KumAuTVPORg",
        )
        self.assertEqual(
            res.data,
            {
                "query": "lofi",
                "video_id": "KumAuTVPORg",
                "title": "Lofi Hip Hop Radio",
                "url": "https://www.youtube.com/watch?v=KumAuTVPORg",
            },
        )

    @patch("time.sleep")
    @patch("jarvis_windows.open_website")
    @patch("jarvis_tools._first_youtube_video")
    def test_05_play_youtube_no_video_id(self, mock_first_video, mock_open, mock_sleep):
        mock_first_video.return_value = None
        mock_open.return_value = jarvis_windows.ToolResult(True, "Opened YouTube.")

        res = jarvis_tools.play_youtube("rare unknown query")

        self.assertFalse(res.ok)
        self.assertEqual(
            mock_open.call_args[0][0],
            "https://www.youtube.com/results?search_query=rare+unknown+query",
        )
        self.assertEqual(res.data.get("reason"), "no_video_id")
        self.assertEqual(
            res.message,
            "I couldn't find a video for rare unknown query on YouTube, so I opened the results instead.",
        )

    @patch("time.sleep")
    @patch("jarvis_vision.ask_screen")
    @patch("jarvis_windows.open_website")
    @patch("jarvis_tools._first_youtube_video")
    def test_06_play_youtube_unconfirmed(self, mock_first_video, mock_open, mock_ask, mock_sleep):
        mock_first_video.return_value = {
            "video_id": "KumAuTVPORg",
            "title": "Lofi Hip Hop Radio",
            "url": "https://www.youtube.com/watch?v=KumAuTVPORg",
        }
        mock_open.return_value = jarvis_windows.ToolResult(True, "Opened YouTube.")
        mock_ask.return_value = jarvis_windows.ToolResult(True, "NO - still loading")

        res = jarvis_tools.play_youtube("lofi")

        self.assertFalse(res.ok)
        self.assertEqual(res.data.get("reason"), "unconfirmed")
        self.assertEqual(res.data.get("video_id"), "KumAuTVPORg")
        self.assertEqual(res.data.get("title"), "Lofi Hip Hop Radio")
        self.assertEqual(res.data.get("url"), "https://www.youtube.com/watch?v=KumAuTVPORg")
        self.assertEqual(
            res.message,
            "I opened the video for lofi but couldn't confirm it is playing.",
        )

    @patch("jarvis_windows.open_website")
    @patch("jarvis_tools._first_youtube_video")
    def test_07_play_youtube_open_website_failure(self, mock_first_video, mock_open):
        mock_first_video.return_value = {
            "video_id": "KumAuTVPORg",
            "title": "Lofi Hip Hop Radio",
            "url": "https://www.youtube.com/watch?v=KumAuTVPORg",
        }
        mock_open.return_value = jarvis_windows.ToolResult(False, "Browser failed to launch.")

        res = jarvis_tools.play_youtube("lofi")

        self.assertFalse(res.ok)
        self.assertEqual(res.message, "Browser failed to launch.")

    def test_08_play_youtube_empty_query(self):
        res1 = jarvis_tools.play_youtube("")
        self.assertFalse(res1.ok)
        self.assertEqual(res1.message, "Please say what you want to play on YouTube.")

        res2 = jarvis_tools.play_youtube("   ")
        self.assertFalse(res2.ok)
        self.assertEqual(res2.message, "Please say what you want to play on YouTube.")

    @patch("time.sleep")
    @patch("jarvis_vision.ask_screen")
    @patch("jarvis_windows.open_website")
    @patch("jarvis_tools._first_youtube_video")
    def test_09_play_youtube_ask_screen_retry_succeeds(
        self, mock_first_video, mock_open, mock_ask, mock_sleep
    ):
        mock_first_video.return_value = {
            "video_id": "KumAuTVPORg",
            "title": "Lofi Track",
            "url": "https://www.youtube.com/watch?v=KumAuTVPORg",
        }
        mock_open.return_value = jarvis_windows.ToolResult(True, "Opened YouTube.")
        mock_ask.side_effect = [
            jarvis_windows.ToolResult(True, "NO - buffering"),
            jarvis_windows.ToolResult(True, "YES - now playing"),
        ]

        res = jarvis_tools.play_youtube("lofi")

        self.assertTrue(res.ok)
        self.assertEqual(res.message, "Playing lofi on YouTube.")
        self.assertEqual(res.data.get("video_id"), "KumAuTVPORg")

    @patch("jarvis_tools.search_youtube")
    @patch("jarvis_tools._first_youtube_video")
    def test_10_play_youtube_search_failure(self, mock_first_video, mock_search):
        mock_first_video.return_value = None
        mock_search.return_value = jarvis_windows.ToolResult(False, "Failed to reach YouTube.", {})

        res = jarvis_tools.play_youtube("lofi")

        self.assertFalse(res.ok)
        self.assertEqual(res.message, "Failed to reach YouTube.")


if __name__ == "__main__":
    unittest.main()
