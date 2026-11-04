"""Local, explicit personal-memory storage for Jarvis.

This module deliberately has no dependency on voice recognition, Groq, or
system automation.  It stores only records that the caller has explicitly
confirmed, in a SQLite database on the current Windows account.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class MemoryError(Exception):
    """Base error for a safe, user-facing memory operation."""


class MemoryValidationError(MemoryError):
    """Raised when memory text is empty, malformed, or sensitive."""


class MemoryStorageError(MemoryError):
    """Raised when the local SQLite database cannot be used."""


class DuplicateMemoryError(MemoryError):
    """Raised when the same approved memory is already present."""

    def __init__(self, memory_id: int):
        self.memory_id = memory_id
        super().__init__("That memory is already stored.")


@dataclass(frozen=True)
class Memory:
    id: int
    category: str
    content: str
    created_at: str
    updated_at: str


_CATEGORIES = {"fact", "preference", "project"}
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]{1,}", re.IGNORECASE)
_SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:api[ _-]?key|password|passphrase|access[ _-]?token|auth(?:entication)?[ _-]?token|secret)\b", re.I),
    re.compile(r"\b(?:sk|gsk)_[a-z0-9_-]{12,}\b", re.I),
    re.compile(r"\bgh[pousr]_[a-z0-9]{20,}\b", re.I),
)


def default_database_path() -> Path:
    """Return a local (non-project) per-user location for Jarvis data."""
    override = os.environ.get("JARVIS_MEMORY_DB")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "Jarvis" / "jarvis_memory.sqlite3"


def validate_content(content: str) -> str:
    if not isinstance(content, str):
        raise MemoryValidationError("Memory must be text.")
    normalized = " ".join(content.split())
    if not normalized:
        raise MemoryValidationError("Memory cannot be empty.")
    if len(normalized) > 1000:
        raise MemoryValidationError("Memory is too long. Please keep it under 1,000 characters.")
    if any(pattern.search(normalized) for pattern in _SENSITIVE_PATTERNS):
        raise MemoryValidationError("I don't store passwords, API keys, tokens, or other secrets.")
    return normalized


class MemoryStore:
    """Small SQLite repository for approved Jarvis memories."""

    def __init__(self, database_path: Path | str | None = None):
        self.database_path = Path(database_path) if database_path else default_database_path()

    def _connect(self) -> sqlite3.Connection:
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise MemoryStorageError("Jarvis could not open local memory storage.") from exc

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL CHECK(category IN ('fact', 'preference', 'project')),
                content TEXT NOT NULL COLLATE NOCASE UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    @staticmethod
    def _as_memory(row: sqlite3.Row) -> Memory:
        return Memory(**dict(row))

    def save(self, content: str, category: str = "fact") -> Memory:
        content = validate_content(content)
        if category not in _CATEGORIES:
            raise MemoryValidationError("Memory category is invalid.")
        try:
            with self._session() as connection:
                self._initialize(connection)
                existing = connection.execute(
                    "SELECT id FROM memories WHERE content = ?", (content,)
                ).fetchone()
                if existing:
                    raise DuplicateMemoryError(existing["id"])
                cursor = connection.execute(
                    "INSERT INTO memories (category, content) VALUES (?, ?)", (category, content)
                )
                row = connection.execute("SELECT * FROM memories WHERE id = ?", (cursor.lastrowid,)).fetchone()
                return self._as_memory(row)
        except MemoryError:
            raise
        except sqlite3.Error as exc:
            raise MemoryStorageError("Jarvis could not save that memory.") from exc

    def get(self, memory_id: int) -> Memory | None:
        try:
            with self._session() as connection:
                self._initialize(connection)
                row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                return self._as_memory(row) if row else None
        except sqlite3.Error as exc:
            raise MemoryStorageError("Jarvis could not read local memory storage.") from exc

    def list(self, limit: int = 50) -> list[Memory]:
        try:
            with self._session() as connection:
                self._initialize(connection)
                rows = connection.execute(
                    "SELECT * FROM memories ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,)
                ).fetchall()
                return [self._as_memory(row) for row in rows]
        except sqlite3.Error as exc:
            raise MemoryStorageError("Jarvis could not read local memory storage.") from exc

    def update(self, memory_id: int, content: str) -> Memory | None:
        content = validate_content(content)
        try:
            with self._session() as connection:
                self._initialize(connection)
                duplicate = connection.execute(
                    "SELECT id FROM memories WHERE content = ? AND id != ?", (content, memory_id)
                ).fetchone()
                if duplicate:
                    raise DuplicateMemoryError(duplicate["id"])
                cursor = connection.execute(
                    "UPDATE memories SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (content, memory_id),
                )
                if cursor.rowcount == 0:
                    return None
                row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                return self._as_memory(row)
        except MemoryError:
            raise
        except sqlite3.Error as exc:
            raise MemoryStorageError("Jarvis could not update that memory.") from exc

    def delete(self, memory_id: int) -> bool:
        try:
            with self._session() as connection:
                self._initialize(connection)
                return connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0
        except sqlite3.Error as exc:
            raise MemoryStorageError("Jarvis could not delete that memory.") from exc

    def relevant(self, query: str, limit: int = 3) -> list[Memory]:
        """Return a small set of lexical matches; never sends all memories."""
        tokens = set(_TOKEN_RE.findall(query.lower()))
        if not tokens:
            return []
        matches: list[tuple[int, Memory]] = []
        for memory in self.list(limit=100):
            content_tokens = set(_TOKEN_RE.findall(memory.content.lower()))
            score = len(tokens & content_tokens)
            if score:
                matches.append((score, memory))
        matches.sort(key=lambda item: (-item[0], -item[1].id))
        return [memory for _, memory in matches[:limit]]
