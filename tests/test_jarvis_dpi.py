"""Tests for Jarvis's best-effort Windows DPI-awareness startup setup."""

import ctypes
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jarvis_windows


class DpiAwarenessTests(unittest.TestCase):
    def setUp(self):
        self.user32 = SimpleNamespace()
        self.shcore = SimpleNamespace()
        self.windll = SimpleNamespace(user32=self.user32, shcore=self.shcore)
        patcher = patch.object(jarvis_windows.ctypes, "windll", self.windll, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _enable(self):
        with patch("jarvis_windows._is_windows", return_value=True):
            return jarvis_windows._enable_dpi_awareness()

    def test_context_api_success(self):
        context = MagicMock(return_value=True)
        self.user32.SetProcessDpiAwarenessContext = context
        self.shcore.SetProcessDpiAwareness = MagicMock(return_value=0)
        self.assertTrue(self._enable())
        context.assert_called_once()
        self.assertEqual(ctypes.c_ssize_t(context.call_args.args[0].value).value, -4)
        self.shcore.SetProcessDpiAwareness.assert_not_called()

    def test_shcore_fallback(self):
        self.shcore.SetProcessDpiAwareness = MagicMock(return_value=0)
        self.assertTrue(self._enable())
        self.shcore.SetProcessDpiAwareness.assert_called_once_with(2)

    def test_shcore_already_set_counts_as_success(self):
        self.shcore.SetProcessDpiAwareness = MagicMock(return_value=0x80070005)
        self.assertTrue(self._enable())

    def test_legacy_fallback(self):
        self.user32.SetProcessDPIAware = MagicMock(return_value=True)
        del self.windll.shcore
        self.assertTrue(self._enable())
        self.user32.SetProcessDPIAware.assert_called_once_with()

    def test_total_failure_is_silent(self):
        self.user32.SetProcessDpiAwarenessContext = MagicMock(return_value=False)
        self.user32.SetProcessDPIAware = MagicMock(return_value=False)
        self.shcore.SetProcessDpiAwareness = MagicMock(return_value=1)
        self.assertFalse(self._enable())

    def test_non_windows_touches_nothing(self):
        class _PoisonWindll:
            def __getattr__(self, _name):
                raise AssertionError("ctypes.windll must not be accessed")

        with patch.object(jarvis_windows.ctypes, "windll", _PoisonWindll(), create=True), \
             patch("jarvis_windows._is_windows", return_value=False):
            self.assertFalse(jarvis_windows._enable_dpi_awareness())


if __name__ == "__main__":
    unittest.main()
