"""Tests for recoverable, approval-gated Recycle Bin deletion."""

import ctypes
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import jarvis_policy
import jarvis_tools
import jarvis_windows


class DeleteFileTests(unittest.TestCase):
    def setUp(self):
        jarvis_policy.clear_pending_approval()

    def tearDown(self):
        jarvis_policy.clear_pending_approval()

    @staticmethod
    def _remove(path):
        if path.is_dir():
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True, ""

    def _delete(self, target, helper=None):
        helper = helper or self._remove
        with patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows._send_to_recycle_bin", side_effect=helper):
            return jarvis_windows.delete_file(str(target))

    def test_file_recycled(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "discard.txt"
            target.write_text("x", encoding="utf-8")
            result = self._delete(target)
        self.assertTrue(result.ok)
        self.assertIn("Recycle Bin", result.message)
        self.assertEqual(result.data["name"], "discard.txt")
        self.assertTrue(result.data["verified"])
        self.assertFalse(any("path" in key.lower() for key in result.data))

    def test_folder_recycled(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "discard"
            target.mkdir()
            (target / "child.txt").write_text("x", encoding="utf-8")
            result = self._delete(target)
        self.assertTrue(result.ok)
        self.assertTrue(result.data["is_directory"])

    def test_missing_path_refused(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows._send_to_recycle_bin") as recycle:
            result = jarvis_windows.delete_file(str(Path(temp) / "missing.txt"))
        self.assertFalse(result.ok)
        recycle.assert_not_called()

    def test_drive_root_refused(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True), \
             patch("jarvis_windows._send_to_recycle_bin") as recycle:
            result = jarvis_windows.delete_file(Path(temp).anchor)
        self.assertFalse(result.ok)
        self.assertIn("drive", result.message)
        recycle.assert_not_called()

    def test_home_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            with patch("jarvis_windows.audit_action"), \
                 patch("jarvis_windows._is_windows", return_value=True), \
                 patch("jarvis_windows.Path.home", return_value=target), \
                 patch("jarvis_windows._send_to_recycle_bin") as recycle:
                result = jarvis_windows.delete_file(str(target))
        self.assertFalse(result.ok)
        self.assertIn("home", result.message)
        recycle.assert_not_called()

    def _shell_result(self, target, code=0, aborted=False):
        shell32 = MagicMock()

        def operation(ptr):
            struct = ctypes.cast(ptr, ctypes.POINTER(jarvis_windows._SHFILEOPSTRUCTW)).contents
            struct.fAnyOperationsAborted = aborted
            return code

        shell32.SHFileOperationW.side_effect = operation
        windll = MagicMock()
        windll.shell32 = shell32
        with patch.object(jarvis_windows.ctypes, "windll", windll, create=True), \
             patch("jarvis_windows.audit_action"), \
             patch("jarvis_windows._is_windows", return_value=True):
            result = jarvis_windows.delete_file(str(target))
        return result, shell32

    def test_shell_failure_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "keep.txt"
            target.write_text("x", encoding="utf-8")
            result, _ = self._shell_result(target, code=114)
            self.assertFalse(result.ok)
            self.assertTrue(target.exists())

    def test_aborted_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "keep.txt"
            target.write_text("x", encoding="utf-8")
            result, _ = self._shell_result(target, aborted=True)
            self.assertFalse(result.ok)

    def test_shell_struct_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "keep.txt"
            target.write_text("x", encoding="utf-8")
            captured = {}
            shell32 = MagicMock()

            def operation(ptr):
                captured["operation"] = ctypes.cast(ptr, ctypes.POINTER(jarvis_windows._SHFILEOPSTRUCTW)).contents
                return 0

            shell32.SHFileOperationW.side_effect = operation
            windll = MagicMock()
            windll.shell32 = shell32
            with patch.object(jarvis_windows.ctypes, "windll", windll, create=True):
                ok, _ = jarvis_windows._send_to_recycle_bin(target)
            operation_struct = captured["operation"]
            self.assertTrue(ok)
            self.assertEqual(operation_struct.wFunc, 0x0003)
            self.assertTrue(operation_struct.fFlags & 0x0040)
            self.assertTrue(operation_struct.fFlags & 0x0010)
            self.assertEqual(
                ctypes.wstring_at(operation_struct.pFrom, len(str(target)) + 2),
                str(target) + "\x00\x00",
            )

    def test_success_but_still_there_is_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "still.txt"
            target.write_text("x", encoding="utf-8")
            result = self._delete(target, helper=lambda _path: (True, ""))
        self.assertFalse(result.ok)
        self.assertIn("still there", result.message)

    def test_non_windows_refused(self):
        with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=False):
            result = jarvis_windows.delete_file("anything")
        self.assertFalse(result.ok)

    def test_execute_tool_requires_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "keep.txt"
            target.write_text("x", encoding="utf-8")
            with patch("jarvis_windows.audit_action"):
                result = jarvis_tools.execute_tool("delete_file", {"path": str(target)})
            self.assertFalse(result.ok)
            self.assertTrue(result.data["requires_approval"])
            self.assertTrue(target.exists())

    def test_execute_tool_approved_recycles(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "discard.txt"
            target.write_text("x", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), \
                 patch("jarvis_windows._is_windows", return_value=True), \
                 patch("jarvis_windows._send_to_recycle_bin", side_effect=self._remove):
                result = jarvis_tools.execute_tool("delete_file", {"path": str(target)}, approved=True)
            self.assertTrue(result.ok)
            self.assertFalse(target.exists())

    def test_risk_is_high(self):
        self.assertEqual(jarvis_policy.classify_risk("delete_file"), jarvis_policy.RiskLevel.HIGH)


if __name__ == "__main__":
    unittest.main()
