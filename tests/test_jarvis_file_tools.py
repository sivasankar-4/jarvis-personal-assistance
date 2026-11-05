"""Tests for the file-management tools: find_files, create_folder, move_path, copy_path, rename_path."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_policy
import jarvis_tools
import jarvis_windows


class FindFilesTests(unittest.TestCase):
    def test_find_files_hit_file_and_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "report.txt").write_text("data", encoding="utf-8")
            (Path(temp) / "report_dir").mkdir()
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.find_files("report", temp)
            self.assertTrue(result.ok)
            self.assertEqual(result.data["count"], 2)
            self.assertIn("report.txt", result.data["matches"])
            self.assertIn("report_dir", result.data["matches"])

    def test_find_files_miss(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.find_files("nonexistent", temp)
            self.assertFalse(result.ok)
            self.assertIn("couldn't find anything", result.message)

    def test_find_files_case_insensitive(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "MyReport.PDF").write_text("data", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.find_files("myreport", temp)
            self.assertTrue(result.ok)
            self.assertEqual(result.data["count"], 1)

    def test_find_files_caps_at_25(self):
        with tempfile.TemporaryDirectory() as temp:
            for i in range(30):
                (Path(temp) / f"item_{i:02d}.txt").write_text("x", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.find_files("item_", temp)
            self.assertTrue(result.ok)
            self.assertEqual(result.data["count"], 25)
            self.assertIn("at least", result.message)


class CreateFolderTests(unittest.TestCase):
    def test_create_folder_success(self):
        with tempfile.TemporaryDirectory() as temp:
            target = str(Path(temp) / "NewFolder")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.create_folder(target)
            self.assertTrue(result.ok)
            self.assertIn("Created folder", result.message)
            self.assertEqual(result.data["kind"], "folder")
            self.assertTrue(result.data["verified"])
            self.assertTrue(Path(target).is_dir())

    def test_create_folder_already_exists(self):
        with tempfile.TemporaryDirectory() as temp:
            existing = Path(temp) / "Existing"
            existing.mkdir()
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.create_folder(str(existing))
            self.assertFalse(result.ok)
            self.assertIn("already exists", result.message)

    def test_create_folder_file_exists_at_target(self):
        with tempfile.TemporaryDirectory() as temp:
            existing = Path(temp) / "BlockingFile"
            existing.write_text("x", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.create_folder(str(existing))
            self.assertFalse(result.ok)
            self.assertIn("already exists", result.message)

    def test_create_folder_missing_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            target = str(Path(temp) / "missing_parent" / "child")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.create_folder(target)
            self.assertFalse(result.ok)
            self.assertIn("parent folder does not exist", result.message)


class MovePathTests(unittest.TestCase):
    def test_move_file_success(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "a.txt"
            src.write_text("hello", encoding="utf-8")
            dest = Path(temp) / "b.txt"
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.move_path(str(src), str(dest))
            self.assertTrue(result.ok)
            self.assertTrue(result.data["verified"])
            self.assertFalse(src.exists())
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_text(encoding="utf-8"), "hello")

    def test_move_into_folder_keeps_name(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "doc.pdf"
            src.write_text("pdf", encoding="utf-8")
            sub = Path(temp) / "subdir"
            sub.mkdir()
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.move_path(str(src), str(sub))
            self.assertTrue(result.ok)
            self.assertTrue((sub / "doc.pdf").exists())
            self.assertFalse(src.exists())

    def test_move_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "a.txt"
            src.write_text("source", encoding="utf-8")
            dst = Path(temp) / "b.txt"
            dst.write_text("dest", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.move_path(str(src), str(dst))
            self.assertFalse(result.ok)
            self.assertIn("already exists", result.message)
            # Both files unchanged
            self.assertEqual(src.read_text(encoding="utf-8"), "source")
            self.assertEqual(dst.read_text(encoding="utf-8"), "dest")

    def test_move_missing_source(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.move_path(str(Path(temp) / "nope.txt"), temp)
            self.assertFalse(result.ok)
            self.assertIn("couldn't find", result.message)

    def test_move_onto_itself(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "a.txt"
            src.write_text("data", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.move_path(str(src), str(src))
            self.assertFalse(result.ok)
            self.assertIn("already in that location", result.message)


class CopyPathTests(unittest.TestCase):
    def test_copy_file_success(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "original.txt"
            src.write_text("content", encoding="utf-8")
            dst = Path(temp) / "copy.txt"
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.copy_path(str(src), str(dst))
            self.assertTrue(result.ok)
            self.assertTrue(result.data["verified"])
            self.assertTrue(src.exists())
            self.assertTrue(dst.exists())
            self.assertEqual(dst.read_text(encoding="utf-8"), "content")

    def test_copy_folder_success(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "mydir"
            src.mkdir()
            (src / "inner.txt").write_text("hi", encoding="utf-8")
            dst = Path(temp) / "mydir_copy"
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.copy_path(str(src), str(dst))
            self.assertTrue(result.ok)
            self.assertTrue(result.data["verified"])
            self.assertEqual(result.data["kind"], "folder")
            self.assertTrue((dst / "inner.txt").exists())
            self.assertTrue(src.exists())

    def test_copy_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "a.txt"
            src.write_text("source", encoding="utf-8")
            dst = Path(temp) / "b.txt"
            dst.write_text("existing", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.copy_path(str(src), str(dst))
            self.assertFalse(result.ok)
            self.assertIn("already exists", result.message)
            self.assertEqual(dst.read_text(encoding="utf-8"), "existing")


class RenamePathTests(unittest.TestCase):
    def test_rename_success(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "old.txt"
            src.write_text("data", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.rename_path(str(src), "new.txt")
            self.assertTrue(result.ok)
            self.assertTrue(result.data["verified"])
            self.assertIn("Renamed", result.message)
            self.assertFalse(src.exists())
            self.assertTrue((Path(temp) / "new.txt").exists())

    def test_rename_separator_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "file.txt"
            src.write_text("data", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.rename_path(str(src), "sub/file.txt")
            self.assertFalse(result.ok)
            self.assertIn("simple name", result.message)

    def test_rename_backslash_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "file.txt"
            src.write_text("data", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.rename_path(str(src), "sub\\file.txt")
            self.assertFalse(result.ok)
            self.assertIn("simple name", result.message)

    def test_rename_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "old.txt"
            src.write_text("old data", encoding="utf-8")
            existing = Path(temp) / "taken.txt"
            existing.write_text("taken data", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.rename_path(str(src), "taken.txt")
            self.assertFalse(result.ok)
            self.assertIn("already exists", result.message)
            self.assertTrue(src.exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "taken data")


class PolicyTests(unittest.TestCase):
    def test_find_files_is_low_risk(self):
        self.assertEqual(jarvis_policy.classify_risk("find_files"), jarvis_policy.RiskLevel.LOW)

    def test_create_folder_is_low_risk(self):
        self.assertEqual(jarvis_policy.classify_risk("create_folder"), jarvis_policy.RiskLevel.LOW)

    def test_move_path_is_moderate_risk(self):
        self.assertEqual(jarvis_policy.classify_risk("move_path"), jarvis_policy.RiskLevel.MODERATE)

    def test_copy_path_is_moderate_risk(self):
        self.assertEqual(jarvis_policy.classify_risk("copy_path"), jarvis_policy.RiskLevel.MODERATE)

    def test_rename_path_is_moderate_risk(self):
        self.assertEqual(jarvis_policy.classify_risk("rename_path"), jarvis_policy.RiskLevel.MODERATE)

    def test_delete_file_is_high_risk(self):
        self.assertEqual(jarvis_policy.classify_risk("delete_file"), jarvis_policy.RiskLevel.HIGH)


class RegistryTests(unittest.TestCase):
    def test_execute_tool_move_path_success(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "moveme.txt"
            src.write_text("moveme", encoding="utf-8")
            dst = Path(temp) / "moved.txt"
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_tools.execute_tool("move_path", {"source": str(src), "destination": str(dst)})
            self.assertTrue(result.ok)

    def test_execute_tool_rejects_extra_argument(self):
        result = jarvis_tools.execute_tool("move_path", {"source": "a", "destination": "b", "force": True})
        self.assertFalse(result.ok)



class SelfDestinationTests(unittest.TestCase):
    """OBS-1: copy/move a folder into its own subfolder must be refused cleanly."""

    def test_copy_folder_into_own_subfolder_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "Src"
            src.mkdir()
            (src / "file.txt").write_text("x", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.copy_path(str(src), str(src / "Inner"))
            self.assertFalse(result.ok)
            self.assertIn("into itself", result.message)
            # Nothing must have been created inside src
            self.assertFalse((src / "Inner").exists())

    def test_move_folder_into_own_subfolder_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "Src"
            src.mkdir()
            (src / "file.txt").write_text("x", encoding="utf-8")
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.move_path(str(src), str(src / "Inner"))
            self.assertFalse(result.ok)
            self.assertIn("into itself", result.message)
            # src and its contents must be fully intact
            self.assertTrue(src.exists())
            self.assertTrue((src / "file.txt").exists())

    def test_copy_folder_to_sibling_still_works(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "Src"
            src.mkdir()
            (src / "file.txt").write_text("x", encoding="utf-8")
            sibling = Path(temp) / "SrcCopy"
            with patch("jarvis_windows.audit_action"), patch("jarvis_windows._is_windows", return_value=True):
                result = jarvis_windows.copy_path(str(src), str(sibling))
            self.assertTrue(result.ok)
            self.assertTrue(sibling.exists())
            self.assertTrue((sibling / "file.txt").exists())


if __name__ == "__main__":
    unittest.main()
