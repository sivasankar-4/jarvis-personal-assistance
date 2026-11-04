import unittest
from unittest.mock import MagicMock, patch
import jarvis_actions

class TestCompoundCommands(unittest.TestCase):

    @patch("jarvis_actions._open")
    def test_compound_youtube_search(self, mock_open):
        # Action: "Open YouTube and search for Java tutorials"
        reply = jarvis_actions.try_action("Open YouTube and search for Java tutorials")
        
        # Verify first action (Open YouTube)
        self.assertIn("https://www.youtube.com", mock_open.call_args_list[0][0][0])
        # Verify second action (Search YouTube)
        self.assertIn("searching youtube for java tutorials", reply.lower())

    def test_generic_and_preservation(self):
        # Ensure it doesn't break "Explain X and Y"
        reply = jarvis_actions.try_action("Explain Java and Python")
        # Should return None (passed to LLM)
        self.assertIsNone(reply)

    def test_google_routing(self):
        # Verify search defaults to Google
        reply = jarvis_actions.try_action("Search for Java tutorials")
        self.assertIn("searching google for java tutorials", reply.lower())

    def test_youtube_routing(self):
        # Verify youtube routing
        reply = jarvis_actions.try_action("Search for Java tutorials on YouTube")
        self.assertIn("searching youtube for java tutorials", reply.lower())

    def test_microphone_capability(self):
        # Verify brain prompt (via indirect check of system logic if possible, or skip as it's a prompt change)
        import jarvis_brain
        self.assertIn("transcribed from microphone audio", jarvis_brain.SYSTEM_PROMPT)

if __name__ == "__main__":
    unittest.main()
