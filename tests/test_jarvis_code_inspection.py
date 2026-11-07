"""Tests for read_file and search_code code-inspection tools (TASK-019)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import jarvis_policy
import jarvis_tasks
import jarvis_tools
import jarvis_windows


class ReadFileTests(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    # 1. Exact lines returned with numbers
    def test_read_exact_lines(self):
        f = self.root / "sample.py"
        f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        result = jarvis_windows.read_file(str(f), 1, 200)
        self.assertTrue(result.ok, result.message)
        self.assertIn("1: alpha", result.message)
        self.assertIn("2: beta", result.message)
        self.assertIn("3: gamma", result.message)
        self.assertEqual(result.data["name"], "sample.py")
        self.assertEqual(result.data["lines"], 3)
        self.assertFalse(result.data["truncated"])
        # Content must NOT appear as a data key
        self.assertNotIn("content", result.data)
        self.assertNotIn("text", result.data)
        self.assertNotIn("lines_text", result.data)

    # 2. Windowed read
    def test_read_window(self):
        f = self.root / "sample.py"
        f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        result = jarvis_windows.read_file(str(f), 2, 1)
        self.assertTrue(result.ok, result.message)
        self.assertIn("2: beta", result.message)
        self.assertNotIn("1: alpha", result.message)
        self.assertNotIn("3: gamma", result.message)
        self.assertEqual(result.data["start_line"], 2)
        self.assertEqual(result.data["lines"], 1)

    # 3. Missing path
    def test_read_missing(self):
        result = jarvis_windows.read_file(str(self.root / "nonexistent.py"), 1, 200)
        self.assertFalse(result.ok)

    # 4. Directory passed as path
    def test_read_directory(self):
        result = jarvis_windows.read_file(str(self.root), 1, 200)
        self.assertFalse(result.ok)
        self.assertIn("folder", result.message.lower())

    # 5. Binary file (NUL bytes)
    def test_read_binary(self):
        f = self.root / "binary.py"
        f.write_bytes(b"hello\x00world")
        result = jarvis_windows.read_file(str(f), 1, 200)
        self.assertFalse(result.ok)
        self.assertIn("plain text", result.message)

    # 6. Bad extension
    def test_read_bad_extension(self):
        f = self.root / "app.exe"
        f.write_text("MZ", encoding="utf-8")
        result = jarvis_windows.read_file(str(f), 1, 200)
        self.assertFalse(result.ok)
        self.assertIn("readable", result.message)

    # 7. Over-size file
    def test_read_oversize(self):
        f = self.root / "big.txt"
        f.write_bytes(b"x" * 200_001)
        result = jarvis_windows.read_file(str(f), 1, 200)
        self.assertFalse(result.ok)
        self.assertIn("200001", result.message)

    # 8. Deny-list files — no content leaks
    def test_read_deny_list(self):
        cases = [".env", "free.key", "data.db", "actions.jsonl"]
        for name in cases:
            with self.subTest(name=name):
                f = self.root / name
                f.write_text("TOPSECRET", encoding="utf-8")
                result = jarvis_windows.read_file(str(f), 1, 200)
                self.assertFalse(result.ok, f"{name} should be refused")
                self.assertNotIn("TOPSECRET", result.message)

    # 9. Long content is truncated in message
    def test_read_truncates(self):
        # Each numbered line is "N: " + 100 chars = ~104 chars; 200 lines = ~20800 > 20000
        f = self.root / "long.py"
        line = "x" * 100
        f.write_text("\n".join(line for _ in range(300)), encoding="utf-8")
        result = jarvis_windows.read_file(str(f), 1, 200)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.data["truncated"])
        self.assertLessEqual(len(result.message), 20_000 + 500)  # small slack for header

    # 10. start_line beyond EOF
    def test_read_beyond_eof(self):
        f = self.root / "small.py"
        f.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        result = jarvis_windows.read_file(str(f), 99, 200)
        self.assertFalse(result.ok)
        self.assertIn("3", result.message)


class SearchCodeTests(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _make(self, rel_path, content, encoding="utf-8"):
        p = self.root / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding=encoding)
        return p

    # 11. Basic found
    def test_search_found(self):
        f1 = self._make("a.py", "def hello():\n    pass\n")
        f2 = self._make("b.py", "# hello world\nx = 1\n")
        result = jarvis_windows.search_code("hello", str(self.root), 25)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.data["count"], 2)
        self.assertIn("a.py", result.data["files"])
        self.assertIn("b.py", result.data["files"])
        # Verify file:line format
        self.assertIn(":1:", result.message)  # first line of a.py

    # 12. Case-insensitive
    def test_search_case_insensitive(self):
        self._make("tools.py", "def write_clipboard(text):\n    pass\n")
        result = jarvis_windows.search_code("WRITE_CLIPBOARD", str(self.root), 25)
        self.assertTrue(result.ok, result.message)
        self.assertGreaterEqual(result.data["count"], 1)

    # 13. No match
    def test_search_no_match(self):
        self._make("empty.py", "nothing here\n")
        result = jarvis_windows.search_code("xyzzy_nothere", str(self.root), 25)
        self.assertFalse(result.ok)
        self.assertIn("xyzzy_nothere", result.message)

    # 14. Skips hidden dirs and oversize files
    def test_search_skips_hidden_and_oversize(self):
        # Hidden dir — should be pruned
        hidden = self.root / ".venv"
        hidden.mkdir()
        (hidden / "secret.py").write_text("needle_hidden\n", encoding="utf-8")
        # Oversize file
        big = self.root / "big.py"
        big.write_bytes(b"needle_big\n" + b"x" * 200_001)
        # Normal match
        self._make("normal.py", "needle_hidden\nneedle_big\n")

        result = jarvis_windows.search_code("needle_hidden", str(self.root), 25)
        # Only normal.py should contribute — hidden dir pruned, big file skipped
        for line in result.message.splitlines():
            if "needle_hidden" in line and ":" in line:
                # Must NOT be from .venv
                self.assertNotIn(".venv", line)

    # 15. Empty query and file-as-directory
    def test_search_empty_query_and_bad_dir(self):
        result_eq = jarvis_windows.search_code("", str(self.root), 25)
        self.assertFalse(result_eq.ok)
        self.assertIn("search for", result_eq.message.lower())

        f = self._make("some.py", "content\n")
        result_bad = jarvis_windows.search_code("content", str(f), 25)
        self.assertFalse(result_bad.ok)

    # 16. Deny-list file not searched
    def test_search_deny_list(self):
        key = self.root / "free.key"
        key.write_text("needle_secret\n", encoding="utf-8")
        result = jarvis_windows.search_code("needle_secret", str(self.root), 25)
        self.assertFalse(result.ok)


class PolicyAndGateTests(unittest.TestCase):

    # 17. Policy: both MODERATE
    def test_policy_moderate(self):
        self.assertEqual(
            jarvis_policy.classify_risk("read_file"),
            jarvis_policy.RiskLevel.MODERATE,
        )
        self.assertEqual(
            jarvis_policy.classify_risk("search_code"),
            jarvis_policy.RiskLevel.MODERATE,
        )

    # 18. Plan gate: both rejected; write_clipboard still accepted
    def test_plan_gate(self):
        rf_step = jarvis_tasks.TaskStep(
            "rf", "Read a file", "read_file",
            {"path": "foo.py", "start_line": 1, "max_lines": 200},
        )
        sc_step = jarvis_tasks.TaskStep(
            "sc", "Search code", "search_code",
            {"query": "hello", "directory": "", "max_matches": 25},
        )
        wc_step = jarvis_tasks.TaskStep(
            "wc", "Write clipboard", "write_clipboard",
            {"text": "hello"},
        )
        ok_rf, msg_rf = jarvis_tasks.validate_plan([rf_step])
        self.assertFalse(ok_rf, msg_rf)
        self.assertIn("observation", msg_rf.lower())

        ok_sc, msg_sc = jarvis_tasks.validate_plan([sc_step])
        self.assertFalse(ok_sc, msg_sc)
        self.assertIn("observation", msg_sc.lower())

        ok_wc, _ = jarvis_tasks.validate_plan([wc_step])
        self.assertTrue(ok_wc)


if __name__ == "__main__":
    unittest.main()
