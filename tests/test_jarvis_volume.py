"""Tests for Jarvis volume control feature.

Covers:
  - jarvis_windows.adjust_volume: happy paths, invalid action, non-Windows guard
  - jarvis_policy: adjust_volume classified as LOW risk
  - jarvis_actions.try_action: phrase routing maps to the right action
"""

from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Minimal stub for ctypes.windll so tests run on any OS
# ---------------------------------------------------------------------------
def _stub_windll():
    mock_user32 = MagicMock()
    mock_user32.keybd_event.return_value = None
    windll = MagicMock()
    windll.user32 = mock_user32
    return windll, mock_user32


# ---------------------------------------------------------------------------
# jarvis_windows.adjust_volume
# ---------------------------------------------------------------------------
class TestAdjustVolume(unittest.TestCase):

    def _import(self):
        """Re-import jarvis_windows with a stubbed ctypes.windll."""
        import importlib
        import jarvis_windows
        importlib.reload(jarvis_windows)
        return jarvis_windows

    def _make_windll_mock(self):
        _, user32 = _stub_windll()
        return user32

    # -- happy paths ---------------------------------------------------------

    def test_volume_up_sends_keybd_event(self):
        import jarvis_windows
        user32 = self._make_windll_mock()
        with patch.object(jarvis_windows.ctypes.windll, "user32", user32):
            result = jarvis_windows.adjust_volume("up")
        self.assertTrue(result.ok, msg=result.message)
        self.assertIn("increased", result.message.lower())
        # 5 presses × 2 keybd_event calls (down + up) = 10 total
        self.assertEqual(user32.keybd_event.call_count, 10)
        calls = user32.keybd_event.call_args_list
        self.assertEqual(calls[0],  call(0xAF, 0, 0, 0))       # press 1 key-down
        self.assertEqual(calls[1],  call(0xAF, 0, 0x0002, 0))  # press 1 key-up
        self.assertEqual(calls[8],  call(0xAF, 0, 0, 0))       # press 5 key-down
        self.assertEqual(calls[9],  call(0xAF, 0, 0x0002, 0))  # press 5 key-up
        self.assertEqual(result.data["presses"], 5)

    def test_volume_down_sends_keybd_event(self):
        import jarvis_windows
        user32 = self._make_windll_mock()
        with patch.object(jarvis_windows.ctypes.windll, "user32", user32):
            result = jarvis_windows.adjust_volume("down")
        self.assertTrue(result.ok, msg=result.message)
        self.assertIn("decreased", result.message.lower())
        # 5 presses × 2 = 10 keybd_event calls
        self.assertEqual(user32.keybd_event.call_count, 10)
        calls = user32.keybd_event.call_args_list
        self.assertEqual(calls[0], call(0xAE, 0, 0, 0))       # press 1 key-down  VK_VOLUME_DOWN
        self.assertEqual(calls[1], call(0xAE, 0, 0x0002, 0))  # press 1 key-up
        self.assertEqual(result.data["presses"], 5)

    def test_volume_mute_sends_keybd_event(self):
        import jarvis_windows
        user32 = self._make_windll_mock()
        with patch.object(jarvis_windows.ctypes.windll, "user32", user32):
            result = jarvis_windows.adjust_volume("mute")
        self.assertTrue(result.ok, msg=result.message)
        self.assertIn("muted", result.message.lower())
        # mute sends exactly 1 press = 2 keybd_event calls
        self.assertEqual(user32.keybd_event.call_count, 2)
        calls = user32.keybd_event.call_args_list
        self.assertEqual(calls[0], call(0xAD, 0, 0, 0))       # VK_VOLUME_MUTE key-down
        self.assertEqual(calls[1], call(0xAD, 0, 0x0002, 0))  # key-up
        self.assertEqual(result.data["presses"], 1)

    def test_action_case_insensitive(self):
        import jarvis_windows
        user32 = self._make_windll_mock()
        with patch.object(jarvis_windows.ctypes.windll, "user32", user32):
            result = jarvis_windows.adjust_volume("UP")
        self.assertTrue(result.ok)

    # -- error paths ---------------------------------------------------------

    def test_invalid_action_returns_failure(self):
        import jarvis_windows
        result = jarvis_windows.adjust_volume("louder")
        self.assertFalse(result.ok)
        self.assertIn("louder", result.message)

    def test_non_string_action_returns_failure(self):
        import jarvis_windows
        result = jarvis_windows.adjust_volume(42)   # type: ignore[arg-type]
        self.assertFalse(result.ok)

    def test_keybd_event_oserror_returns_failure(self):
        import jarvis_windows
        user32 = self._make_windll_mock()
        user32.keybd_event.side_effect = OSError("test error")
        with patch.object(jarvis_windows.ctypes.windll, "user32", user32):
            result = jarvis_windows.adjust_volume("up")
        self.assertFalse(result.ok)
        self.assertIn("Windows could not change the volume", result.message)

    def test_non_windows_guard(self):
        import jarvis_windows
        with patch("jarvis_windows._is_windows", return_value=False):
            result = jarvis_windows.adjust_volume("up")
        self.assertFalse(result.ok)
        self.assertIn("Windows", result.message)


# ---------------------------------------------------------------------------
# jarvis_policy: risk classification
# ---------------------------------------------------------------------------
class TestAdjustVolumePolicy(unittest.TestCase):

    def test_adjust_volume_classified_low(self):
        import jarvis_policy
        risk = jarvis_policy.classify_risk("adjust_volume", {"action": "up"})
        self.assertEqual(risk, jarvis_policy.RiskLevel.LOW)

    def test_adjust_volume_allowed_autonomous(self):
        import jarvis_policy
        result = jarvis_policy.evaluate("adjust_volume", {"action": "mute"})
        self.assertTrue(result.allowed)
        self.assertEqual(result.risk_level, jarvis_policy.RiskLevel.LOW)
        self.assertFalse(result.requires_approval)


# ---------------------------------------------------------------------------
# jarvis_actions.try_action: phrase routing
# ---------------------------------------------------------------------------
class TestVolumeActionRouting(unittest.TestCase):

    def _route(self, phrase: str) -> str | None:
        import jarvis_actions
        return jarvis_actions.try_action(phrase)

    def _patch_execute(self, side_effect_map: dict):
        """Patch execute_tool to return a fake ToolResult based on action arg."""
        import jarvis_windows

        def fake_execute(tool_name, arguments, approved=False):
            if tool_name == "adjust_volume":
                action = arguments.get("action", "?")
                return jarvis_windows.ToolResult(True, f"Volume {action} ok.")
            return jarvis_windows.ToolResult(False, "unexpected tool")

        import jarvis_tools
        return patch.object(jarvis_tools, "execute_tool", side_effect=fake_execute)

    def test_volume_up_phrase(self):
        with self._patch_execute({}):
            msg = self._route("volume up")
        self.assertIsNotNone(msg)
        self.assertIn("up", msg)

    def test_louder_phrase(self):
        with self._patch_execute({}):
            msg = self._route("louder")
        self.assertIsNotNone(msg)
        self.assertIn("up", msg)

    def test_volume_down_phrase(self):
        with self._patch_execute({}):
            msg = self._route("volume down")
        self.assertIsNotNone(msg)
        self.assertIn("down", msg)

    def test_quieter_phrase(self):
        with self._patch_execute({}):
            msg = self._route("quieter")
        self.assertIsNotNone(msg)
        self.assertIn("down", msg)

    def test_mute_phrase(self):
        with self._patch_execute({}):
            msg = self._route("mute")
        self.assertIsNotNone(msg)
        self.assertIn("mute", msg)

    def test_mute_volume_phrase(self):
        with self._patch_execute({}):
            msg = self._route("mute volume")
        self.assertIsNotNone(msg)
        self.assertIn("mute", msg)

    def test_silence_phrase_not_captured(self):
        """'silence' was removed from the mute keyword list; must not route to volume."""
        msg = self._route("silence")
        # Either returns None (not handled) or something unrelated to volume
        if msg is not None:
            self.assertNotIn("mute", msg.lower())

    def test_unrelated_phrase_not_captured(self):
        msg = self._route("open notepad")
        # Should not return volume-related text
        if msg is not None:
            self.assertNotIn("volume", msg.lower())


if __name__ == "__main__":
    unittest.main()
