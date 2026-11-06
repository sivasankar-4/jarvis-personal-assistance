"""Tests for verified Unicode Windows clipboard operations."""

import ctypes
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jarvis_policy
import jarvis_tasks
import jarvis_windows


class ClipboardTests(unittest.TestCase):
    def _write(self, text, set_ok=True, read_value=None):
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows._set_clipboard_text", return_value=set_ok), \
             patch("jarvis_windows._get_clipboard_text", return_value=text if read_value is None else read_value):
            return jarvis_windows.write_clipboard(text)

    def test_write_success_verified(self):
        text = "secret clipboard text"
        result = self._write(text)
        self.assertTrue(result.ok)
        self.assertTrue(result.data["verified"])
        self.assertEqual(result.data["characters"], len(text))
        self.assertNotIn(text, result.message)

    def test_write_readback_mismatch_fails(self):
        result = self._write("hello", read_value="other")
        self.assertFalse(result.ok)

    def test_write_set_failure(self):
        result = self._write("hello", set_ok=False)
        self.assertFalse(result.ok)

    def test_write_too_long(self):
        with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True), patch("jarvis_windows._set_clipboard_text") as setter:
            result = jarvis_windows.write_clipboard("x" * 100001)
        self.assertFalse(result.ok)
        setter.assert_not_called()

    def test_write_empty_clears(self):
        result = self._write("")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Cleared the clipboard.")

    def test_write_payload_bytes(self):
        user32 = MagicMock(OpenClipboard=MagicMock(return_value=True), EmptyClipboard=MagicMock(return_value=True), SetClipboardData=MagicMock(return_value=123), CloseClipboard=MagicMock())
        kernel32 = MagicMock(GlobalAlloc=MagicMock(return_value=123), GlobalLock=MagicMock(return_value=456), GlobalUnlock=MagicMock())
        windll = SimpleNamespace(user32=user32, kernel32=kernel32)
        captured = {}

        def memmove(destination, source, count):
            captured["payload"] = ctypes.string_at(source, count)

        with patch.object(jarvis_windows.ctypes, "windll", windll, create=True), patch("jarvis_windows.ctypes.memmove", side_effect=memmove):
            self.assertTrue(jarvis_windows._set_clipboard_text("hello"))
        self.assertEqual(captured["payload"].decode("utf-16-le"), "hello\x00")

    def test_read_success(self):
        with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True), patch("jarvis_windows._get_clipboard_text", return_value="hello clipboard"):
            result = jarvis_windows.read_clipboard()
        self.assertTrue(result.ok)
        self.assertIn("hello clipboard", result.message)
        self.assertEqual(result.data["characters"], 15)
        self.assertNotIn("text", result.data)

    def test_read_no_text(self):
        with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True), patch("jarvis_windows._get_clipboard_text", return_value=None):
            result = jarvis_windows.read_clipboard()
        self.assertFalse(result.ok)
        self.assertIn("doesn't contain text", result.message)

    def test_read_truncates(self):
        text = "x" * 25000
        with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True), patch("jarvis_windows._get_clipboard_text", return_value=text):
            result = jarvis_windows.read_clipboard()
        self.assertTrue(result.ok)
        self.assertIn("[…truncated]", result.message)
        self.assertEqual(result.data, {"characters": 25000, "truncated": True})

    def test_policy_moderate(self):
        self.assertEqual(jarvis_policy.classify_risk("write_clipboard"), jarvis_policy.RiskLevel.MODERATE)
        self.assertEqual(jarvis_policy.classify_risk("read_clipboard"), jarvis_policy.RiskLevel.MODERATE)

    def test_plan_gate(self):
        read = jarvis_tasks.TaskStep("read", "Read", "read_clipboard", {})
        write = jarvis_tasks.TaskStep("write", "Write", "write_clipboard", {"text": "hello"})
        self.assertFalse(jarvis_tasks.validate_plan([read])[0])
        self.assertTrue(jarvis_tasks.validate_plan([write])[0])

    # ── TASK-018 regression: prototype declarations ───────────────────────────

    @unittest.skipUnless(os.name == "nt", "Windows-only: tests live ctypes.windll prototypes")
    def test_prototypes_declared(self):
        """_clipboard_prototypes() must set the exact argtypes/restype that
        prevent the 32-bit overflow on 64-bit handles."""
        # Call the function so it configures the real windll objects.
        jarvis_windows._clipboard_prototypes()

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        self.assertEqual(kernel32.GlobalAlloc.argtypes,
                         [ctypes.c_uint, ctypes.c_size_t])
        self.assertEqual(kernel32.GlobalAlloc.restype, ctypes.c_void_p)

        self.assertEqual(kernel32.GlobalLock.argtypes, [ctypes.c_void_p])
        self.assertEqual(kernel32.GlobalLock.restype, ctypes.c_void_p)

        self.assertEqual(kernel32.GlobalUnlock.argtypes, [ctypes.c_void_p])
        self.assertEqual(kernel32.GlobalUnlock.restype, ctypes.c_bool)

        self.assertEqual(user32.OpenClipboard.argtypes, [ctypes.c_void_p])

        self.assertEqual(user32.SetClipboardData.argtypes,
                         [ctypes.c_uint, ctypes.c_void_p])
        self.assertEqual(user32.SetClipboardData.restype, ctypes.c_void_p)

        self.assertEqual(user32.GetClipboardData.argtypes, [ctypes.c_uint])
        self.assertEqual(user32.GetClipboardData.restype, ctypes.c_void_p)

    def test_helpers_never_raise(self):
        """If GlobalLock raises ctypes.ArgumentError the helpers must absorb it
        and return False / None — never let any exception escape to the caller."""
        user32 = MagicMock(
            OpenClipboard=MagicMock(return_value=True),
            EmptyClipboard=MagicMock(return_value=True),
            IsClipboardFormatAvailable=MagicMock(return_value=True),
            CloseClipboard=MagicMock(),
        )
        kernel32 = MagicMock(
            GlobalAlloc=MagicMock(return_value=0xDEADBEEFDEADBEEF),
            GlobalLock=MagicMock(
                side_effect=ctypes.ArgumentError("int too long to convert")
            ),
            GlobalUnlock=MagicMock(),
        )
        windll = SimpleNamespace(user32=user32, kernel32=kernel32)

        with patch.object(jarvis_windows.ctypes, "windll", windll, create=True):
            # _set_clipboard_text must return False, not raise
            result_set = jarvis_windows._set_clipboard_text("hello")
            # _get_clipboard_text must return None, not raise
            result_get = jarvis_windows._get_clipboard_text()

        self.assertFalse(result_set)
        self.assertIsNone(result_get)


if __name__ == "__main__":
    unittest.main()
