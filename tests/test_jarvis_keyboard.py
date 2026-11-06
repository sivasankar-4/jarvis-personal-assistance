"""Tests for safe keyboard shortcuts and Unicode text input."""

import ctypes
import os
import struct
import unittest
from unittest.mock import MagicMock, patch

import jarvis_policy
import jarvis_windows


class KeyboardTests(unittest.TestCase):
    def setUp(self):
        self.user32 = MagicMock()
        self.user32.keybd_event.return_value = None
        self.user32.SendInput.return_value = 1
        windll = MagicMock()
        windll.user32 = self.user32
        patcher = patch.object(jarvis_windows.ctypes, "windll", windll, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _shortcut(self, shortcut):
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows.foreground_window_title", return_value="Untitled - Notepad"):
            return jarvis_windows.send_shortcut(shortcut)

    def test_single_keys_sendable(self):
        for shortcut, key in (("enter", 0x0D), ("tab", 0x09), ("escape", 0x1B), ("backspace", 0x08)):
            with self.subTest(shortcut=shortcut):
                self.user32.keybd_event.reset_mock()
                result = self._shortcut(shortcut)
                self.assertTrue(result.ok)
                self.assertEqual(self.user32.keybd_event.call_count, 2)
                self.assertEqual(self.user32.keybd_event.call_args_list[0].args, (key, 0, 0, 0))
                self.assertEqual(self.user32.keybd_event.call_args_list[1].args, (key, 0, jarvis_windows._KEYUP, 0))

    def test_unknown_shortcut_still_refused(self):
        for shortcut in ("ctrl+alt+delete", "f13"):
            with self.subTest(shortcut=shortcut):
                self.user32.keybd_event.reset_mock()
                result = self._shortcut(shortcut)
                self.assertFalse(result.ok)
                self.user32.keybd_event.assert_not_called()

    def test_type_text_direct_insert_and_verification(self):
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.foreground_window_title", return_value="Untitled - Notepad"), \
             patch("jarvis_windows._find_edit_control", return_value=123), \
             patch("jarvis_windows._get_control_text", side_effect=["", "hello\r\nworld"]):
            result = jarvis_windows.type_text("hello\nworld", "Notepad")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["characters"], 11)
        self.assertTrue(result.data["verified"])
        self.user32.SendMessageW.assert_called_with(123, 0x00C2, 1, "hello\r\nworld")

    def test_type_text_readback_missing_text_fails(self):
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.foreground_window_title", return_value="Untitled - Notepad"), \
             patch("jarvis_windows._find_edit_control", return_value=123), \
             patch("jarvis_windows._get_control_text", side_effect=["", "other text"]):
            result = jarvis_windows.type_text("hello", "Notepad")
        self.assertFalse(result.ok)
        self.assertIn("could not confirm", result.message)

    def test_type_text_no_edit_control_fails(self):
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows.foreground_window_title", return_value="Untitled - Notepad"), \
             patch("jarvis_windows._find_edit_control", return_value=0):
            result = jarvis_windows.type_text("hello", "Notepad")
        self.assertFalse(result.ok)
        self.assertIn("couldn't find a text area", result.message)

    def test_type_text_wrong_window_refused(self):
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows.foreground_window_title", return_value="Untitled - Notepad"):
            result = jarvis_windows.type_text("hello", "Chrome")
        self.assertFalse(result.ok)
        self.user32.SendInput.assert_not_called()

    @unittest.skipUnless(os.name == "nt" and struct.calcsize("P") == 8, "Windows x64 ABI only")
    def test_input_struct_size(self):
        self.assertEqual(ctypes.sizeof(jarvis_windows._Input), 40)
        self.assertEqual(ctypes.sizeof(jarvis_windows._KeyBdInput), 24)

    def test_policy_new_keys_moderate(self):
        self.assertEqual(jarvis_policy.classify_risk("send_shortcut", {"shortcut": "enter"}), jarvis_policy.RiskLevel.MODERATE)
        self.assertEqual(jarvis_policy.classify_risk("send_shortcut", {"shortcut": "alt+f4"}), jarvis_policy.RiskLevel.HIGH)


if __name__ == "__main__":
    unittest.main()
