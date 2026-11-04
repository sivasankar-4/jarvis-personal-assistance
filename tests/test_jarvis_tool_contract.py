"""Tests verifying signature and dispatch contracts for all Jarvis tools."""

import inspect
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import jarvis_tools
import jarvis_windows


class ToolContractTests(unittest.TestCase):
    def test_tool_signatures_match_registry_arguments(self):
        """(a) Signature contract: every registered arg must be a parameter of the real callable."""
        for name, (target_func, allowed_types) in jarvis_tools._TOOLS.items():
            func = (
                getattr(jarvis_windows, target_func)
                if isinstance(target_func, str)
                else target_func
            )
            sig = inspect.signature(func)
            for arg_name in allowed_types:
                self.assertIn(
                    arg_name,
                    sig.parameters,
                    f"Tool '{name}' registry specifies argument '{arg_name}', "
                    f"but function '{getattr(func, '__name__', str(func))}' signature lacks it: {sig}",
                )

    def test_tool_dispatch_passes_exact_registry_arguments(self):
        """(b) Dispatch contract: execute_tool dispatches exact argument dict to the callable."""
        original_tools = dict(jarvis_tools._TOOLS)
        try:
            with patch("jarvis_windows.audit_action"):
                for name, (_target_func, allowed_types) in original_tools.items():
                    received_kwargs = {}

                    def make_stub(recorder):
                        def stub(**kwargs):
                            recorder.update(kwargs)
                            return jarvis_windows.ToolResult(True, "stub")
                        return stub

                    jarvis_tools._TOOLS[name] = (make_stub(received_kwargs), allowed_types)

                    dummy_args = {}
                    for arg_name, arg_type in allowed_types.items():
                        if arg_type is str:
                            dummy_args[arg_name] = "contract"
                        elif arg_type is int:
                            dummy_args[arg_name] = 1
                        else:
                            dummy_args[arg_name] = "contract"

                    result = jarvis_tools.execute_tool(name, dummy_args, approved=True)
                    self.assertTrue(result.ok, f"execute_tool failed for tool '{name}': {result.message}")
                    self.assertEqual(
                        set(received_kwargs.keys()),
                        set(allowed_types.keys()),
                        f"Stub for '{name}' received keys {set(received_kwargs.keys())}, expected {set(allowed_types.keys())}",
                    )
                    self.assertEqual(received_kwargs, dummy_args)
        finally:
            jarvis_tools._TOOLS = original_tools

    def test_open_path_regression_end_to_end(self):
        """(c) open_path regression: execute_tool('open_path', {'path': tempdir}) invokes real open_path."""
        process_mock = MagicMock(pid=4321)
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("jarvis_windows._is_windows", return_value=True), \
                 patch("jarvis_windows.subprocess.Popen", return_value=process_mock) as popen_mock, \
                 patch("jarvis_windows.audit_action"):
                result = jarvis_tools.execute_tool("open_path", {"path": temp_dir})

                self.assertTrue(result.ok, f"execute_tool('open_path') failed: {result.message}")
                self.assertIn("Started File Explorer", result.message)
                self.assertEqual(result.data.get("pid"), 4321)
                self.assertEqual(result.data.get("kind"), "folder")
                popen_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
