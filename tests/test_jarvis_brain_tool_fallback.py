"""Tests for brain fallback from native tool calling across multiple models and degraded chat."""

import time
import unittest
from unittest.mock import patch

import jarvis_brain
import jarvis_policy


class _FakeCompletions:
    def __init__(self, script_fn):
        self.script_fn = script_fn
        self.calls = []

    def create(self, **kwargs):
        call_info = {
            "model": kwargs.get("model"),
            "has_tools": "tools" in kwargs,
            "messages": kwargs.get("messages", []),
            "kwargs": kwargs,
        }
        self.calls.append(call_info)
        result = self.script_fn(kwargs.get("model"), "tools" in kwargs, kwargs)
        if isinstance(result, Exception):
            raise result
        message = type("Message", (), {"content": result, "tool_calls": None})()
        choice = type("Choice", (), {"message": message})()
        return type("Response", (), {"choices": [choice]})()


class _FakeClient:
    def __init__(self, completions):
        self.chat = type("Chat", (), {"completions": completions})()


class BrainToolFallbackTests(unittest.TestCase):
    def setUp(self):
        jarvis_brain._history = []
        jarvis_brain._client = None
        jarvis_brain._cooldown_until = 0.0

    def tearDown(self):
        jarvis_brain._history = []
        jarvis_brain._client = None

    def _default_patches(self):
        return [
            patch("jarvis_brain._handle_memory_command", return_value=None),
            patch("jarvis_tasks.is_multistep_objective", return_value=False),
            patch("jarvis_planner.plan_direct_desktop_request", return_value=None),
            patch("jarvis_actions.try_action", return_value=None),
            patch("jarvis_brain._get_api_key", return_value="test-key"),
        ]

    def test_second_model_is_tried_with_tools(self):
        def script(model, has_tools, kwargs):
            if model == jarvis_brain.MODELS[0] and has_tools:
                raise RuntimeError("Model 1 tools failed")
            if model == jarvis_brain.MODELS[1] and has_tools:
                return "tools answer"
            raise RuntimeError(f"Unexpected call: model={model}, has_tools={has_tools}")

        completions = _FakeCompletions(script)
        jarvis_brain._client = _FakeClient(completions)

        with self._default_patches()[0], self._default_patches()[1], \
             self._default_patches()[2], self._default_patches()[3], \
             self._default_patches()[4], \
             patch("jarvis_policy.has_pending_approval", return_value=False):
            result = jarvis_brain.think("find my notes")

        self.assertEqual(result, "tools answer")
        self.assertEqual(len(completions.calls), 2)
        self.assertEqual(completions.calls[0]["model"], jarvis_brain.MODELS[0])
        self.assertTrue(completions.calls[0]["has_tools"])
        self.assertEqual(completions.calls[1]["model"], jarvis_brain.MODELS[1])
        self.assertTrue(completions.calls[1]["has_tools"])
        self.assertTrue(all(c["has_tools"] for c in completions.calls))

    def test_all_models_failing_tools_degrades_honestly(self):
        def script(model, has_tools, kwargs):
            if has_tools:
                raise RuntimeError(f"{model} tools unavailable")
            return "chat answer"

        completions = _FakeCompletions(script)
        jarvis_brain._client = _FakeClient(completions)

        with self._default_patches()[0], self._default_patches()[1], \
             self._default_patches()[2], self._default_patches()[3], \
             self._default_patches()[4], \
             patch("jarvis_policy.has_pending_approval", return_value=False):
            result = jarvis_brain.think("create folder Documents/Projects")

        self.assertEqual(result, "chat answer")
        no_tools_calls = [c for c in completions.calls if not c["has_tools"]]
        self.assertTrue(len(no_tools_calls) > 0)
        system_contents = [
            msg["content"]
            for call in no_tools_calls
            for msg in call["messages"]
            if msg.get("role") == "system"
        ]
        self.assertTrue(
            any("WITHOUT tool access" in content for content in system_contents),
            "Expected 'WITHOUT tool access' substring in degraded chat system messages",
        )
        self.assertTrue(
            any("temporarily unavailable" in content for content in system_contents)
        )
        self.assertTrue(
            any("do NOT say you lack" in content for content in system_contents)
        )

    def test_degraded_chat_failure_uses_local_fallback(self):
        def script(model, has_tools, kwargs):
            raise RuntimeError(f"{model} failed completely")

        completions = _FakeCompletions(script)
        jarvis_brain._client = _FakeClient(completions)

        with self._default_patches()[0], self._default_patches()[1], \
             self._default_patches()[2], self._default_patches()[3], \
             self._default_patches()[4], \
             patch("jarvis_policy.has_pending_approval", return_value=False), \
             patch("jarvis_brain._local_fallback", return_value="local answer"):
            result = jarvis_brain.think("help me with something")

        self.assertEqual(result, "local answer")

    def test_rate_limit_from_tools_call_still_cools_down(self):
        class RateLimit429(Exception):
            status_code = 429

        def script(model, has_tools, kwargs):
            raise RateLimit429()

        completions = _FakeCompletions(script)
        jarvis_brain._client = _FakeClient(completions)

        with self._default_patches()[0], self._default_patches()[1], \
             self._default_patches()[2], self._default_patches()[3], \
             self._default_patches()[4], \
             patch("jarvis_policy.has_pending_approval", return_value=False), \
             patch("jarvis_brain._local_fallback", return_value="local answer"):
            result = jarvis_brain.think("check system status")

        self.assertEqual(result, "local answer")
        self.assertGreater(jarvis_brain._cooldown_until, time.time())
        self.assertEqual(len(completions.calls), 1)
        self.assertEqual(completions.calls[0]["model"], jarvis_brain.MODELS[0])

    def test_pending_approval_still_plain_chat(self):
        def script(model, has_tools, kwargs):
            if not has_tools:
                return "approval answer"
            raise RuntimeError("Tools should not be invoked when approval is pending")

        completions = _FakeCompletions(script)
        jarvis_brain._client = _FakeClient(completions)

        with self._default_patches()[0], self._default_patches()[1], \
             self._default_patches()[2], self._default_patches()[3], \
             self._default_patches()[4], \
             patch("jarvis_policy.has_pending_approval", return_value=True), \
             patch("jarvis_policy.get_pending_approval", return_value=None):
            result = jarvis_brain.think("what is the weather today")

        self.assertEqual(result, "approval answer")
        self.assertTrue(len(completions.calls) > 0)
        self.assertTrue(all(not c["has_tools"] for c in completions.calls))


if __name__ == "__main__":
    unittest.main()
