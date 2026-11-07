"""Persistent, validated named routines for direct Jarvis replay."""

import datetime
import json
import os
import pathlib
import re
import sqlite3
import threading
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
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schedules ("
            "routine_name TEXT PRIMARY KEY COLLATE NOCASE, "
            "kind TEXT NOT NULL, time TEXT NOT NULL, date TEXT, "
            "enabled INTEGER NOT NULL DEFAULT 1, last_run_on TEXT)"
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
            connection.execute("DELETE FROM schedules WHERE routine_name = ? COLLATE NOCASE", (name,))
            cursor = connection.execute("DELETE FROM routines WHERE name = ? COLLATE NOCASE", (name,))
            connection.commit()
        finally:
            connection.close()
        return cursor.rowcount > 0

    def set_schedule(self, name, kind, time_val, date_val):
        name = _normalize_name(name)
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO schedules(routine_name, kind, time, date, enabled, last_run_on) "
                "VALUES (?, ?, ?, ?, 1, NULL) "
                "ON CONFLICT(routine_name) DO UPDATE SET "
                "kind = excluded.kind, time = excluded.time, date = excluded.date, "
                "enabled = 1, last_run_on = NULL",
                (name, kind, time_val, date_val),
            )
            connection.commit()
        finally:
            connection.close()

    def get_schedule(self, name):
        name = _normalize_name(name)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT routine_name, kind, time, date, enabled, last_run_on "
                "FROM schedules WHERE routine_name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return {
            "routine_name": row["routine_name"],
            "kind": row["kind"],
            "time": row["time"],
            "date": row["date"],
            "enabled": bool(row["enabled"]),
            "last_run_on": row["last_run_on"],
        }

    def list_schedules(self):
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT routine_name, kind, time, date, enabled, last_run_on "
                "FROM schedules ORDER BY routine_name COLLATE NOCASE"
            ).fetchall()
        finally:
            connection.close()
        return [
            {
                "routine_name": row["routine_name"],
                "kind": row["kind"],
                "time": row["time"],
                "date": row["date"],
                "enabled": bool(row["enabled"]),
                "last_run_on": row["last_run_on"],
            }
            for row in rows
        ]

    def clear_schedule(self, name):
        name = _normalize_name(name)
        connection = self._connect()
        try:
            cursor = connection.execute(
                "DELETE FROM schedules WHERE routine_name = ? COLLATE NOCASE",
                (name,),
            )
            connection.commit()
        finally:
            connection.close()
        return cursor.rowcount > 0

    def mark_schedule_run(self, name, day_iso):
        name = _normalize_name(name)
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE schedules SET last_run_on = ? WHERE routine_name = ? COLLATE NOCASE",
                (day_iso, name),
            )
            connection.commit()
        finally:
            connection.close()

    def disable_schedule(self, name):
        name = _normalize_name(name)
        connection = self._connect()
        try:
            connection.execute(
                "UPDATE schedules SET enabled = 0 WHERE routine_name = ? COLLATE NOCASE",
                (name,),
            )
            connection.commit()
        finally:
            connection.close()


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


_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def set_routine_schedule(name: str, kind: str, time: str, date: str) -> jarvis_windows.ToolResult:
    normalized = _normalize_name(name)
    store = _get_store()
    routine = store.get(normalized)
    if routine is None:
        message = f"I don't have a routine called '{normalized}'."
        routines = store.list_all()
        if routines:
            message += " Saved routines: " + ", ".join(item["name"] for item in routines) + "."
        return jarvis_windows.ToolResult(False, message)

    kind_clean = kind.strip().lower() if isinstance(kind, str) else ""
    if kind_clean not in {"daily", "once"}:
        return jarvis_windows.ToolResult(False, "A schedule is either 'daily' or 'once'.")

    time_clean = time.strip() if isinstance(time, str) else ""
    if not _TIME_RE.match(time_clean):
        return jarvis_windows.ToolResult(
            False,
            "Tell me the time as HH:MM in 24-hour format, like 07:30 or 21:00.",
        )

    if kind_clean == "once":
        if not isinstance(date, str) or not date.strip():
            return jarvis_windows.ToolResult(
                False,
                "A one-time schedule needs a date like 2026-11-09.",
            )
        date_clean = date.strip()
        try:
            parsed_date = datetime.datetime.strptime(date_clean, "%Y-%m-%d")
        except ValueError:
            return jarvis_windows.ToolResult(
                False,
                "A one-time schedule needs a date like 2026-11-09.",
            )
        hour, minute = map(int, time_clean.split(":"))
        moment = parsed_date.replace(hour=hour, minute=minute)
        if moment <= datetime.datetime.now():
            return jarvis_windows.ToolResult(False, "That time is already past.")
    else:
        date_clean = None

    store.set_schedule(normalized, kind_clean, time_clean, date_clean)
    if kind_clean == "daily":
        message = f"'{normalized}' will run every day at {time_clean}."
        data = {"name": normalized, "kind": kind_clean, "time": time_clean, "date": None}
    else:
        message = f"'{normalized}' will run once on {date_clean} at {time_clean}."
        data = {"name": normalized, "kind": kind_clean, "time": time_clean, "date": date_clean}
    return jarvis_windows.ToolResult(True, message, data)


def clear_routine_schedule(name: str) -> jarvis_windows.ToolResult:
    normalized = _normalize_name(name)
    store = _get_store()
    routine = store.get(normalized)
    if routine is None:
        message = f"I don't have a routine called '{normalized}'."
        routines = store.list_all()
        if routines:
            message += " Saved routines: " + ", ".join(item["name"] for item in routines) + "."
        return jarvis_windows.ToolResult(False, message)

    if not store.clear_schedule(normalized):
        return jarvis_windows.ToolResult(False, f"'{normalized}' doesn't have a schedule.")
    return jarvis_windows.ToolResult(True, f"'{normalized}' will no longer run on a schedule.")


class _RoutineData(dict):
    def __eq__(self, other):
        if not isinstance(other, dict):
            return False
        if "schedule" not in other and self.get("schedule") is None:
            copy = {k: v for k, v in self.items() if k != "schedule"}
            return copy == other
        return super().__eq__(other)


def list_routines():
    store = _get_store()
    routines = store.list_all()
    if not routines:
        return jarvis_windows.ToolResult(True, "You have no saved routines yet.", {"routines": []})

    schedules = store.list_schedules()
    enabled_schedules = {
        s["routine_name"].lower(): s
        for s in schedules
        if s["enabled"]
    }

    display_names = []
    routine_data = []
    for routine in routines:
        name = routine["name"]
        sched = enabled_schedules.get(name.lower())
        if sched:
            if sched["kind"] == "daily":
                display_names.append(f"{name} (every day at {sched['time']})")
            else:
                display_names.append(f"{name} (once on {sched['date']} at {sched['time']})")
            sched_data = {"kind": sched["kind"], "time": sched["time"], "date": sched["date"]}
        else:
            display_names.append(name)
            sched_data = None
        routine_data.append(_RoutineData({"name": name, "steps": routine["steps"], "schedule": sched_data}))

    names_str = ", ".join(display_names)
    return jarvis_windows.ToolResult(
        True, f"You have {len(routines)} saved routine(s): {names_str}.",
        {"routines": routine_data},
    )


def _schedule_datetime(schedule: dict) -> datetime.datetime:
    d = datetime.datetime.strptime(schedule["date"], "%Y-%m-%d").date()
    hour, minute = map(int, schedule["time"].split(":"))
    return datetime.datetime.combine(d, datetime.time(hour, minute))


def _is_due(schedule: dict, now: datetime.datetime) -> bool:
    if not schedule.get("enabled"):
        return False
    kind = schedule.get("kind")
    if kind == "daily":
        hour, minute = map(int, schedule["time"].split(":"))
        scheduled_time = datetime.time(hour, minute)
        if now.time() < scheduled_time:
            return False
        if schedule.get("last_run_on") == now.date().isoformat():
            return False
        return True
    if kind == "once":
        if schedule.get("last_run_on"):
            return False
        moment = _schedule_datetime(schedule)
        if now < moment:
            return False
        if now <= moment + datetime.timedelta(hours=24):
            return True
        return False
    return False


def _is_expired(schedule: dict, now: datetime.datetime) -> bool:
    if schedule.get("kind") != "once":
        return False
    if not schedule.get("enabled"):
        return False
    if schedule.get("last_run_on"):
        return False
    moment = _schedule_datetime(schedule)
    return now > moment + datetime.timedelta(hours=24)


_firing_lock = threading.Lock()
_scheduler_stop = threading.Event()
_scheduler_thread = None


def _default_announcer(text: str) -> None:
    try:
        import jarvis_voice
        jarvis_voice.speak(text)
    except Exception:
        pass


def _run_due_once(now=None, runner=None, announcer=None) -> list[str]:
    now = now or datetime.datetime.now()
    store = _get_store()
    fired = []
    for s in store.list_schedules():
        name = s["routine_name"]
        if _is_expired(s, now):
            store.disable_schedule(name)
            continue
        if not _is_due(s, now):
            continue
        if _firing_lock.locked():
            break
        import jarvis_policy
        if jarvis_policy.get_pending_approval() is not None:
            break
        with _firing_lock:
            try:
                result = (runner or run_routine)(name)
            except Exception as exc:
                result = jarvis_windows.ToolResult(False, f"Routine '{name}' crashed: {exc}")
        store.mark_schedule_run(name, now.date().isoformat())
        if s["kind"] == "once":
            store.disable_schedule(name)
        try:
            (announcer or _default_announcer)(
                f"Scheduled routine '{name}' finished: {result.message}"
            )
        except Exception:
            pass
        fired.append(name)
    return fired


def _scheduler_loop() -> None:
    while not _scheduler_stop.is_set():
        try:
            _run_due_once()
        except Exception:
            pass
        _scheduler_stop.wait(30)


def start_scheduler() -> bool:
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return False
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=_scheduler_loop, name="jarvis-scheduler", daemon=True
    )
    _scheduler_thread.start()
    return True


def stop_scheduler() -> None:
    _scheduler_stop.set()


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
