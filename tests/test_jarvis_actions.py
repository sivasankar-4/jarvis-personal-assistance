import unittest
from unittest.mock import MagicMock, patch

import jarvis_actions
import jarvis_planner
import jarvis_tools
import jarvis_windows


class NaturalActionRoutingTests(unittest.TestCase):
    def test_polite_app_launch_phrase_uses_existing_local_action(self):
        with patch("jarvis_tools.execute_tool", return_value=jarvis_windows.ToolResult(True, "Started Visual Studio Code.")) as mock_tool:
            reply = jarvis_actions.try_action("Could you please open VSCode?")
        self.assertEqual(reply, "Started Visual Studio Code.")
        mock_tool.assert_called_once_with("launch_application", {"name": "vscode"})

    def test_natural_web_lookup_opens_search_without_claiming_results(self):
        with patch("jarvis_actions._open", return_value=True) as open_site:
            reply = jarvis_actions.try_action("Would you look up Python dataclasses?")
        self.assertEqual(reply, "Searching Google for python dataclasses.")
        self.assertIn("python%20dataclasses", open_site.call_args.args[0][0])

    def test_youtube_sequence_is_a_search_tool_not_an_app_name(self):
        with patch("jarvis_planner.execute_tool", return_value=jarvis_windows.ToolResult(True, "YouTube search opened.")) as execute:
            result = jarvis_planner.plan_direct_desktop_request("Open YouTube and type English songs")
        self.assertTrue(result.ok)
        execute.assert_called_once_with("search_youtube", {"query": "English songs"})

    def test_youtube_search_phrase_variation_is_case_insensitive(self):
        with patch("jarvis_planner.execute_tool", return_value=jarvis_windows.ToolResult(True, "YouTube search opened.")) as execute:
            jarvis_planner.plan_direct_desktop_request("please OPEN youtube and search for lo fi beats")
        execute.assert_called_once_with("search_youtube", {"query": "lo fi beats"})

    def test_youtube_search_variation_and_url_encoding(self):
        with patch("jarvis_tools.jarvis_windows.open_website", return_value=jarvis_windows.ToolResult(True, "Accepted.")) as open_site:
            result = jarvis_tools.search_youtube("English songs & covers")
        self.assertTrue(result.ok)
        self.assertIn("English songs", result.message)
        self.assertIn("English+songs+%26+covers", open_site.call_args.args[0])

    def test_explicit_date_and_time_queries(self):
        """Explicit date and time questions are handled deterministically."""
        time_queries = [
            "what time is it",
            "what is the time",
            "what's the time",
        ]
        for query in time_queries:
            reply = jarvis_actions.try_action(query)
            self.assertIsNotNone(reply, f"Expected a response for '{query}'")
            self.assertTrue(reply.startswith("It is currently "), f"Unexpected response '{reply}' for '{query}'")

        date_queries = [
            "what is today's date",
            "what day is it today",
            "current date",
        ]
        for query in date_queries:
            reply = jarvis_actions.try_action(query)
            self.assertIsNotNone(reply, f"Expected a response for '{query}'")
            self.assertTrue(reply.startswith("Today is "), f"Unexpected response '{reply}' for '{query}'")

    def test_conversational_queries_do_not_trigger_date_time_actions(self):
        """Conversational sentences containing date/time words return None to reach the conversational brain."""
        conversational_phrases = [
            "did you eat today",
            "how are you today",
            "update Python",
            "candidate profile",
            "validate this file",
            "time complexity of mergesort",
            "take your time",
            "sometimes",
            "runtime",
        ]
        for phrase in conversational_phrases:
            reply = jarvis_actions.try_action(phrase)
            self.assertIsNone(
                reply,
                f"Expected None for conversational phrase '{phrase}', but got: {reply}",
            )


if __name__ == "__main__":
    unittest.main()
