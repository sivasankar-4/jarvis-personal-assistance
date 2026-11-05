"""Persistent, validated named routines for direct Jarvis replay."""

import json
import os
import pathlib
import re
import sqlite3
import time

import jarvis_windows


ROUTINE_STEP_TOOLS = {
    "launch_application", "open_website", "open_path", "create_folder", "adjust_volume",
}
_STEP_ARG_TYPES = {
    "launch_application": {"name": str},
    "open_website": {"url": str},
    "open_path": {"path": str},
    "create_folder": {"path": str},
    "adjust_volume": {"action": str},
}


def _normalize_name(name):
    return " ".join(name.split()) if isinstance(name, str) else ""


def _default_db_path():
    override = os.environ.get("JARVIS_ROUTINES_DB")
    if override:
        return pathlib.Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = pathlib.Path(local_app_data) if local_app_data else pathlib.Path.home() / "AppData" / "Local"
    return base / "Jarvis" / "jarvis_routines.sqlite3"


class RoutineStore:
    """Small SQLite repository for normalized user routine definitions."""

    def __init__(self, database_path=None):
        self.database_path = pathlib.Path(database_path) if database_path else _default_db_path()

    def _connect(self):
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE IF NOT EXISTS routines ("
            "name TEXT PRIMARY KEY COLLATE NOCASE, steps_json TEXT NOT NULL, "
            "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
        )
        return connection

    def save(self, name, steps):
        name = _normalize_name(name)
        now = time.time()
        connection = self._connect()
        try:
            existed_before = connection.execute(
                "SELECT 1 FROM routines WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone() is not None
            connection.execute(
                "INSERT INTO routines(name, steps_json, created_at, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET name = excluded.name, steps_json = excluded.steps_json, "
                "updated_at = excluded.updated_at",
                (name, json.dumps(steps), now, now),
            )
            connection.commit()
        finally:
            connection.close()
        return existed_before

    def get(self, name):
        name = _normalize_name(name)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT name, steps_json, created_at, updated_at FROM routines "
                "WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return {
            "name": row["name"], "steps": json.loads(row["steps_json"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }

    def list_all(self):
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT name, steps_json, updated_at FROM routines ORDER BY name COLLATE NOCASE"
            ).fetchall()
        finally:
            connection.close()
        return [
            {"name": row["name"], "steps": len(json.loads(row["steps_json"])), "updated_at": row["updated_at"]}
            for row in rows
        ]

    def delete(self, name):
        name = _normalize_name(name)
        connection = self._connect()
        try:
            cursor = connection.execute("DELETE FROM routines WHERE name = ? COLLATE NOCASE", (name,))
            connection.commit()
        finally:
            connection.close()
        return cursor.rowcount > 0


def _get_store():
    return RoutineStore(_default_db_path())


def validate_steps(steps):
    if not isinstance(steps, list) or not 1 <= len(steps) <= 20:
        return "A routine needs between 1 and 20 steps."
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"tool", "arguments"}:
            return "Each step must have a tool and its arguments."
        tool = step["tool"]
        if tool not in ROUTINE_STEP_TOOLS:
            return f"'{tool}' can't be used in a routine yet."
        arguments = step["arguments"]
        expected = _STEP_ARG_TYPES[tool]
        if not isinstance(arguments, dict) or set(arguments) != set(expected):
            return f"Step for '{tool}' has wrong arguments."
        if any(not isinstance(arguments[key], arg_type) for key, arg_type in expected.items()):
            return f"Step for '{tool}' has wrong arguments."
    return None


def save_routine(name, steps):
    normalized = _normalize_name(name)
    if not 1 <= len(normalized) <= 60:
        return jarvis_windows.ToolResult(False, "Routine names must be 1-60 characters.")
    error = validate_steps(steps)
    if error:
        return jarvis_windows.ToolResult(False, error)
    replaced = _get_store().save(normalized, steps)
    count = len(steps)
    message = (
        f"Updated routine '{normalized}' (replaced the old version) with {count} step(s)."
        if replaced else f"Saved routine '{normalized}' with {count} step(s)."
    )
    return jarvis_windows.ToolResult(True, message, {"name": normalized, "steps": count, "replaced": replaced})


def list_routines():
    routines = _get_store().list_all()
    if not routines:
        return jarvis_windows.ToolResult(True, "You have no saved routines yet.", {"routines": []})
    names = ", ".join(routine["name"] for routine in routines)
    return jarvis_windows.ToolResult(
        True, f"You have {len(routines)} saved routine(s): {names}.",
        {"routines": [{"name": routine["name"], "steps": routine["steps"]} for routine in routines]},
    )


def run_routine(name):
    normalized = _normalize_name(name)
    store = _get_store()
    routine = store.get(normalized)
    if routine is None:
        message = f"I don't have a routine called '{normalized}'."
        routines = store.list_all()
        if routines:
            message += " Saved routines: " + ", ".join(item["name"] for item in routines) + "."
        return jarvis_windows.ToolResult(False, message)

    import jarvis_tasks

    task = jarvis_tasks.Task(task_id=f"routine-{normalized}", objective=f"Routine: {routine['name']}")
    task.steps = [
        jarvis_tasks.TaskStep(
            step_id=f"step-{index + 1}",
            description=f"{step['tool']}({json.dumps(step['arguments'], sort_keys=True)})",
            tool_name=step["tool"], arguments=step["arguments"],
        )
        for index, step in enumerate(routine["steps"])
    ]
    report = jarvis_tasks.execute_task(task)
    completed = sum(step.status == jarvis_tasks.StepStatus.COMPLETED for step in task.steps)
    return jarvis_windows.ToolResult(
        task.status == jarvis_tasks.TaskStatus.COMPLETED, report,
        {"routine": routine["name"], "status": task.status.value,
         "steps_completed": completed, "steps_total": len(task.steps)},
    )


def delete_routine(name):
    normalized = _normalize_name(name)
    if not _get_store().delete(normalized):
        return jarvis_windows.ToolResult(False, f"I don't have a routine called '{normalized}'.")
    return jarvis_windows.ToolResult(True, f"Deleted routine '{normalized}'.", {"name": normalized})
