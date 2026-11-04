import unittest
from unittest.mock import patch

import jarvis_brain


class _FakeCompletions:
    def __init__(self, responses=None, error=None):
        self.calls = []
        self.responses = iter(responses or [])
        self.error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        content = next(self.responses)
        message = type("Message", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class _FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


class BrainIntelligenceTests(unittest.TestCase):
    def setUp(self):
        jarvis_brain._history = []
        jarvis_brain._client = None
        jarvis_brain._cooldown_until = 0.0
        jarvis_brain._pending_memory_operation = None

    def tearDown(self):
        jarvis_brain._history = []
        jarvis_brain._client = None

    def test_follow_up_receives_prior_conversation_context_and_instructions(self):
        completions = _FakeCompletions(["A Python list is mutable.", "A tuple is the immutable alternative."])
        jarvis_brain._client = _FakeClient(completions)
        with patch("jarvis_brain._get_api_key", return_value="test-key"), patch(
            "jarvis_actions.try_action", return_value=None
        ), patch("jarvis_brain._relevant_memory_context", return_value=""):
            self.assertEqual(jarvis_brain.think("Explain Python lists."), "A Python list is mutable.")
            self.assertEqual(jarvis_brain.think("What is the immutable alternative?"), "A tuple is the immutable alternative.")

        second_request = completions.calls[1]["messages"]
        self.assertIn("Treat the conversation history as context", second_request[0]["content"])
        self.assertEqual(second_request[1]["content"], "Explain Python lists.")
        self.assertEqual(second_request[2]["content"], "A Python list is mutable.")

    def test_reliable_local_action_is_preferred_and_recorded_for_follow_up(self):
        with patch("jarvis_planner.plan_direct_desktop_request", return_value=None), patch(
            "jarvis_actions.try_action", return_value="Opening Notepad."
        ):
            self.assertEqual(jarvis_brain.think("could you open notepad"), "Opening Notepad.")
        self.assertEqual(jarvis_brain._history[-2:], [
            {"role": "user", "content": "could you open notepad"},
            {"role": "assistant", "content": "Opening Notepad."},
        ])

    def test_provider_errors_use_safe_diagnostics_and_local_fallback(self):
        completions = _FakeCompletions(error=RuntimeError("sensitive-provider-detail"))
        jarvis_brain._client = _FakeClient(completions)
        output = []
        with patch("jarvis_brain._get_api_key", return_value="test-key"), patch(
            "jarvis_actions.try_action", return_value=None
        ), patch("jarvis_brain._local_fallback", return_value="Local mode is available."), patch(
            "builtins.print", side_effect=lambda *parts, **kwargs: output.append(" ".join(map(str, parts)))
        ):
            self.assertEqual(jarvis_brain.think("Explain a stack trace."), "Local mode is available.")

        self.assertTrue(any("RuntimeError" in line for line in output))
        self.assertFalse(any("sensitive-provider-detail" in line for line in output))

    def test_native_model_tool_call_is_sent_to_validated_registry(self):
        tool_call = type("ToolCall", (), {
            "function": type("Function", (), {"name": "launch_application", "arguments": '{"name":"Notepad"}'})()
        })()
        message = type("Message", (), {"content": None, "tool_calls": [tool_call]})()
        response = type("Response", (), {"choices": [type("Choice", (), {"message": message})()]})()
        completions = _FakeCompletions()
        completions.create = lambda **kwargs: response
        jarvis_brain._client = _FakeClient(completions)
        with patch("jarvis_tools.execute_tool", return_value=type("Result", (), {"message": "Started Notepad."})()) as execute:
            result = jarvis_brain._request_with_tools("test-model", [{"role": "user", "content": "open notepad"}])
        self.assertEqual(result, "Started Notepad.")
        execute.assert_called_once_with("launch_application", '{"name":"Notepad"}')


if __name__ == "__main__":
    unittest.main()
