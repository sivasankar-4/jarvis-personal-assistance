import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_brain
from jarvis_memory import MemoryStore


class BrainMemoryCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.api_key_patch = patch("jarvis_brain._get_api_key", return_value=None)
        self.api_key_patch.start()
        jarvis_brain._memory_store = MemoryStore(Path(self.temp_dir.name) / "memory.sqlite3")
        jarvis_brain._pending_memory_operation = None

    def tearDown(self):
        jarvis_brain._pending_memory_operation = None
        self.api_key_patch.stop()
        self.temp_dir.cleanup()

    def test_save_requires_confirmation_then_lists_memory(self):
        reply = jarvis_brain.think("remember that my main backend language is Java")
        self.assertIn("Should I save", reply)
        self.assertEqual(jarvis_brain._memory_store.list(), [])

        reply = jarvis_brain.think("yes")
        self.assertIn("Saved memory", reply)
        self.assertIn("main backend language is Java", jarvis_brain.think("list memories"))

    def test_update_and_delete_each_require_confirmation(self):
        saved = jarvis_brain._memory_store.save("My backend language is Java.")
        self.assertIn("Should I replace", jarvis_brain.think(f"update memory {saved.id} to My backend language is Kotlin."))
        self.assertIn("Updated memory", jarvis_brain.think("confirm"))
        self.assertIn("Kotlin", jarvis_brain._memory_store.get(saved.id).content)

        self.assertIn("Should I delete", jarvis_brain.think(f"forget memory {saved.id}"))
        self.assertIn("Deleted memory", jarvis_brain.think("yes"))
        self.assertIsNone(jarvis_brain._memory_store.get(saved.id))

    def test_secret_is_never_placed_in_pending_memory(self):
        reply = jarvis_brain.think("remember that my password is correct-horse-battery-staple")
        self.assertIn("don't store", reply)
        self.assertIsNone(jarvis_brain._pending_memory_operation)

    def test_unrelated_action_still_works_and_cancels_old_confirmation(self):
        jarvis_brain.think("remember that I prefer dark mode")
        with patch("jarvis_planner.plan_direct_desktop_request", return_value=None), patch(
            "jarvis_actions.try_action", return_value="Opening Notepad."
        ):
            self.assertEqual(jarvis_brain.think("open notepad"), "Opening Notepad.")
        with patch("jarvis_brain._local_fallback", return_value="No pending memory action."):
            self.assertEqual(jarvis_brain.think("yes"), "No pending memory action.")
        self.assertEqual(jarvis_brain._memory_store.list(), [])


if __name__ == "__main__":
    unittest.main()
