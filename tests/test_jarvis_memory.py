import tempfile
import unittest
from pathlib import Path

from jarvis_memory import (
    DuplicateMemoryError,
    MemoryStorageError,
    MemoryStore,
    MemoryValidationError,
)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "memory.sqlite3"
        self.store = MemoryStore(self.path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_retrieve_after_new_store_instance(self):
        saved = self.store.save("My primary backend language is Java.", "preference")
        restarted_store = MemoryStore(self.path)
        self.assertEqual(restarted_store.get(saved.id).content, "My primary backend language is Java.")

    def test_update_list_delete_and_relevant(self):
        saved = self.store.save("My main backend language is Java.", "preference")
        updated = self.store.update(saved.id, "My main backend language is Kotlin.")
        self.assertEqual(updated.content, "My main backend language is Kotlin.")
        self.assertEqual(self.store.relevant("Help with a backend problem")[0].id, saved.id)
        self.assertEqual(self.store.list()[0].id, saved.id)
        self.assertTrue(self.store.delete(saved.id))
        self.assertIsNone(self.store.get(saved.id))

    def test_rejects_empty_duplicate_and_secret_memory(self):
        with self.assertRaises(MemoryValidationError):
            self.store.save("   ")
        self.store.save("I prefer dark mode.")
        with self.assertRaises(DuplicateMemoryError):
            self.store.save("I prefer dark mode.")
        with self.assertRaises(MemoryValidationError):
            self.store.save("My API key is sk_example_value_that_is_long_enough")

    def test_database_unavailable_is_safe(self):
        blocked_parent = Path(self.temp_dir.name) / "not-a-directory"
        blocked_parent.write_text("file", encoding="utf-8")
        with self.assertRaises(MemoryStorageError):
            MemoryStore(blocked_parent / "memory.sqlite3").save("A normal fact")


if __name__ == "__main__":
    unittest.main()
