"""
jarvis_tasks.py - Structured Task Orchestrator for Jarvis (M2)

Coordinates multi-step objectives, plans sequential tool steps, validates
schemas and dependencies, enforces deterministic risk policies, pauses for
explicit user approvals, and reports verified task outcomes.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

import jarvis_actions
import jarvis_policy
import jarvis_tools
import jarvis_windows

MAX_TASK_STEPS = 10
MAX_EXECUTION_LOOPS = 15


class TaskStatus(str, Enum):
    CREATED = "CREATED"
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


@dataclass
class TaskStep:
    step_id: str
    description: str
    tool_name: str
    arguments: dict
    dependencies: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: dict | None = None
    verification_status: str = "UNVERIFIED"

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "dependencies": list(self.dependencies),
            "status": self.status.value,
            "result": self.result,
            "verification_status": self.verification_status,
        }


@dataclass
class Task:
    task_id: str
    objective: str
    status: TaskStatus = TaskStatus.CREATED
    steps: list[TaskStep] = field(default_factory=list)
    current_step_index: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    outcome: str = ""
    error: str | None = None

    def get_step(self, step_id: str) -> TaskStep | None:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def get_current_step(self) -> TaskStep | None:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def is_terminal(self) -> bool:
        return self.status in {
            TaskStatus.COMPLETED,
            TaskStatus.PARTIALLY_COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "outcome": self.outcome,
            "error": self.error,
        }


def validate_plan(steps: list[TaskStep]) -> tuple[bool, str]:
    """Validate plan structure, tool registration, arguments schema, and dependencies."""
    if not isinstance(steps, list) or len(steps) == 0:
        return False, "Plan contains no steps."
    if len(steps) > MAX_TASK_STEPS:
        return False, f"Plan exceeds maximum limit of {MAX_TASK_STEPS} steps."

    step_ids = set()
    for idx, step in enumerate(steps):
        if not isinstance(step, TaskStep):
            return False, f"Invalid step format at index {idx}."
        if not step.step_id or step.step_id in step_ids:
            return False, f"Duplicate or missing step_id '{step.step_id}'."
        step_ids.add(step.step_id)

        # 1. Tool existence check
        if step.tool_name not in jarvis_tools._TOOLS:
            return False, f"Tool '{step.tool_name}' is not a registered desktop tool."
        if step.tool_name == "click_at":
            return False, (
                f"Step '{step.step_id}': plans may use click_and_verify, not click_at — "
                "every plan click must verify its own effect."
            )
        if step.tool_name in ("ask_screen", "take_screenshot", "locate_element"):
            return False, (
                f"Step '{step.step_id}': '{step.tool_name}' is an observation tool "
                "and cannot be a plan step."
            )

        _, allowed_types = jarvis_tools._TOOLS[step.tool_name]

        # 2. Arguments schema check
        if not isinstance(step.arguments, dict):
            return False, f"Step '{step.step_id}' arguments must be a dictionary."
        if set(step.arguments.keys()) != set(allowed_types.keys()):
            return False, f"Step '{step.step_id}' arguments do not match tool schema for '{step.tool_name}'."
        for key, expected_type in allowed_types.items():
            if not isinstance(step.arguments[key], expected_type):
                return False, f"Step '{step.step_id}' argument '{key}' must be of type {expected_type.__name__}."

        # 3. Dependencies check (must refer only to preceding step IDs)
        preceding_ids = {s.step_id for s in steps[:idx]}
        for dep in step.dependencies:
            if dep not in preceding_ids:
                return False, f"Step '{step.step_id}' depends on '{dep}', which does not precede it."

    return True, "Plan is valid."


def is_multistep_objective(text: str) -> bool:
    """Determine whether a natural-language request represents a multi-step objective."""
    cleaned = (text or "").lower().strip().rstrip(".!?")
    cleaned = re.sub(r"^(?:hey\s+)?(?:jarvis[,:]?\s+)?", "", cleaned)
    cleaned = re.sub(r"^(?:(?:can|could|would|will)\s+you\s+|please\s+)", "", cleaned).strip()

    # Route unsupported tasks to orchestrator for honest limitation reporting
    unsupported_indicators = ("save it on", "save file", "save as", "save the file", "send email", "send an email", "send whatsapp")
    if any(k in cleaned for k in unsupported_indicators):
        if any(v in cleaned for v in ("open", "write", "type", "and", "then", "please", "jarvis")):
            return True

    # Sequence connectors
    if re.search(r"\b(?:and\s+then|then|after\s+that|followed\s+by)\b", cleaned):
        return True

    # Sequence numbering: "1. ..., 2. ..."
    if re.search(r"\b1\.\s+.+?\b2\.\s+", cleaned):
        return True

    # Split by comma-and or and-then
    action_verbs = ("type", "write", "search", "press", "send", "close", "quit", "switch", "open", "launch", "run", "play", "save")
    clauses = [c.strip() for c in re.split(r",\s*(?:and\s+)?|\s+and\s+", cleaned) if c.strip()]
    if len(clauses) >= 2:
        matching = sum(1 for c in clauses if any(c.startswith(v) for v in action_verbs))
        if matching >= 2:
            return True
        if clauses[0].startswith("open ") and any(clauses[1].startswith(v) for v in action_verbs):
            return True

    return False


def _parse_direct_part(part_text: str, default_window: str | None = None) -> tuple[str, dict, str] | None:
    """Parse a single action segment into (tool_name, arguments, description)."""
    cleaned = " ".join((part_text or "").strip().split())
    cleaned = re.sub(r"^(?:(?:can|could|would|will)\s+you\s+|please\s+)", "", cleaned, flags=re.I).strip().rstrip(".!?")
    lowered = cleaned.lower()

    # Type / Write command with explicit target window
    type_match = re.match(r"^(?:type|write)\s+(.+?)\s+(?:into|in)\s+(.+)$", cleaned, re.I)
    if type_match:
        text_arg = type_match.group(1).strip().strip('"\'')
        win_arg = type_match.group(2).strip().rstrip(".!?").strip('"\'')
        return "type_text", {"text": text_arg, "expected_window": win_arg}, f"Type text into {win_arg}"

    # Type / Write command with inferred target window from previous step
    if default_window:
        type_simple = re.match(r"^(?:type|write)\s+(.+)$", cleaned, re.I)
        if type_simple:
            text_arg = type_simple.group(1).strip().strip('"\'')
            return "type_text", {"text": text_arg, "expected_window": default_window}, f"Type text into {default_window}"

    # YouTube search
    yt_match = re.match(r"^(?:search(?:\s+for)?|play)\s+(.+?)\s+on\s+youtube$", cleaned, re.I)
    if yt_match:
        q = yt_match.group(1).strip()
        return "search_youtube", {"query": q}, f"Search YouTube for {q}"

    # Google / Web search
    web_match = re.match(r"^(?:search(?:\s+for)?|google|look up)\s+(.+)$", cleaned, re.I)
    if web_match:
        q = web_match.group(1).strip()
        return "search_web", {"query": q}, f"Search Google for {q}"

    # Close application
    close_match = re.match(r"^(?:close|quit|exit)\s+(?:the\s+)?(.+?)(?:\s+(?:app|application|window))?$", cleaned, re.I)
    if close_match:
        target = close_match.group(1).strip().strip('"\'')
        if target.lower() not in {"application", "app", "window", "task", ""}:
            return "close_application", {"name": target}, f"Close application {target}"

    # Switch window
    if lowered in {"switch window", "switch the window", "change window"}:
        return "send_shortcut", {"shortcut": "alt+tab"}, "Switch window"

    # Send shortcut
    sc_match = re.match(r"^(?:press|send)\s+(.+?)(?:\s+(?:shortcut|keys?))?$", lowered)
    if sc_match:
        sc = sc_match.group(1).strip()
        return "send_shortcut", {"shortcut": sc}, f"Send shortcut {sc}"

    # Open website URL
    url_match = re.match(r"^(?:open|visit|go to)\s+(https?://\S+)$", cleaned, re.I)
    if url_match:
        u = url_match.group(1).strip()
        return "open_website", {"url": u}, f"Open website {u}"

    # Open folder or file
    file_match = re.match(r"^(?:open|show)\s+(?:the\s+)?(?:folder|file)\s+(.+)$", cleaned, re.I)
    if file_match:
        p = file_match.group(1).strip().strip('"\'')
        return "open_path", {"path": p}, f"Open path {p}"

    # App launch
    app_match = re.match(r"^(?:open|launch|start|run)\s+(.+)$", cleaned, re.I)
    if app_match:
        target = app_match.group(1).strip().strip('"\'')
        if target.lower() in jarvis_actions.SITES:
            return "open_website", {"url": jarvis_actions.SITES[target.lower()]}, f"Open {target}"
        return "launch_application", {"name": target}, f"Launch {target}"

    return None


def create_task_from_objective(objective: str) -> Task:
    """Create and validate a structured Task from a natural-language objective."""
    task_id = f"task_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    task = Task(task_id=task_id, objective=objective, status=TaskStatus.PLANNING)

    cleaned = objective.strip()

    # Detect unsupported operations honestly
    unsupported_patterns = [
        (r"\b(?:save\b.*?\b(?:desktop|file|disk|folder)|save\s+as|save\s+it)\b", "saving files or creating files directly on disk is not supported yet"),
        (r"\b(?:send\s+email|send\s+an\s+email|compose\s+email)\b", "sending emails is not supported yet"),
        (r"\b(?:send\s+whatsapp|message\s+on\s+whatsapp)\b", "automated WhatsApp messaging is not supported yet"),
        (r"\b(?:click\s+(?:on\s+)?button|click\s+icon|move\s+mouse)\b", "mouse and GUI clicking automation is not supported yet"),
        (r"\b(?:dom\s+automation|inspect\s+element|read\s+browser\s+dom)\b", "browser DOM automation is not supported yet"),
    ]
    for pattern, reason in unsupported_patterns:
        if re.search(pattern, cleaned, re.I):
            task.status = TaskStatus.FAILED
            task.error = f"Unsupported operation: {reason}."
            task.outcome = f"I cannot complete this task because {reason}."
            return task

    # Segment objective into actions
    parts = []
    split_regex = r"\b(?:and\s+then|then|after\s+that|followed\s+by)\b"
    raw_segments = re.split(split_regex, cleaned, flags=re.I)
    if len(raw_segments) > 1:
        parts = [s.strip().rstrip(",").strip() for s in raw_segments if s.strip()]
    else:
        # Split by comma-and or and if at least 2 action verbs
        action_verbs = ("type", "write", "search", "press", "send", "close", "quit", "switch", "open", "launch", "run", "play")
        clauses = [c.strip().rstrip(",").strip() for c in re.split(r",\s*(?:and\s+)?|\s+and\s+", cleaned, flags=re.I) if c.strip()]
        matching = sum(1 for c in clauses if any(c.lower().startswith(v) for v in action_verbs))
        if len(clauses) >= 2 and (matching >= 2 or clauses[0].lower().startswith("open ")):
            parts = clauses
        else:
            parts = [cleaned]

    steps: list[TaskStep] = []
    last_launched_app: str | None = None
    for idx, part in enumerate(parts, start=1):
        parsed = _parse_direct_part(part, default_window=last_launched_app)
        if not parsed:
            task.status = TaskStatus.FAILED
            task.error = f"Could not determine valid tool step for action: '{part}'."
            task.outcome = task.error
            return task

        tool_name, arguments, desc = parsed
        if tool_name == "launch_application" and "name" in arguments:
            last_launched_app = arguments["name"]

        step_id = f"step_{idx}"
        deps = [f"step_{idx - 1}"] if idx > 1 else []
        steps.append(TaskStep(
            step_id=step_id,
            description=desc,
            tool_name=tool_name,
            arguments=arguments,
            dependencies=deps,
        ))

    # Validate plan
    is_valid, validation_msg = validate_plan(steps)
    if not is_valid:
        task.status = TaskStatus.FAILED
        task.error = validation_msg
        task.outcome = f"Plan validation failed: {validation_msg}"
        return task

    task.steps = steps
    task.status = TaskStatus.READY
    task.updated_at = time.time()
    return task


def verify_step_result(step: TaskStep, tool_result: jarvis_windows.ToolResult) -> tuple[str, str]:
    """Inspect tool execution output and determine verification status and evidence."""
    if not tool_result.ok:
        return "FAILED", f"Tool execution returned failure: {tool_result.message}"

    data = tool_result.data or {}
    tool = step.tool_name

    if tool == "launch_application":
        if "pid" in data:
            return "VERIFIED", f"Process created with PID {data['pid']} from {data.get('source', 'system')}."
        return "VERIFIED", "Application launch confirmed by Windows."

    if tool == "type_text":
        if "characters" in data and "window" in data:
            return "VERIFIED", f"Injected {data['characters']} characters into verified foreground window '{data['window']}'."
        return "VERIFIED", "Text typed into focused window."

    if tool == "open_path":
        kind = data.get("kind", "path")
        return "VERIFIED", f"Opened {kind} with default handler."

    if tool == "open_website":
        return "VERIFIED", "URL dispatched to default web browser."

    if tool == "search_youtube":
        return "VERIFIED", "Search query dispatched to YouTube."

    if tool == "search_web":
        return "VERIFIED", "Search query dispatched to Google."

    if tool == "send_shortcut":
        win = data.get("window", "active window")
        return "VERIFIED", f"Keystrokes sent to {win}."

    if tool == "close_application":
        return "VERIFIED", f"Application '{data.get('app', 'target')}' closed."

    if tool == "click_and_verify":
        pixel_changed = data.get("changed") is True
        window_changed = (
            "foreground_before" in data
            and data.get("foreground_after") != data.get("foreground_before")
        )
        evidence = []
        if pixel_changed:
            evidence.append(f"Click changed {data.get('changed_percent')}% of the screen.")
        if window_changed:
            evidence.append(
                f"Active window changed from '{data.get('foreground_before')}' "
                f"to '{data.get('foreground_after')}'."
            )
        if evidence:
            return "VERIFIED", " ".join(evidence)
        return "FAILED", (
            "Click executed but produced no visible change "
            "(no screen change detected and the active window did not change)."
        )

    return "VERIFIED", "Operation confirmed by tool result."


def generate_task_report(task: Task) -> str:
    """Format structured, honest summary of task execution."""
    lines = []
    if task.status == TaskStatus.COMPLETED:
        lines.append(f"Task completed successfully: {task.objective}")
    elif task.status == TaskStatus.PARTIALLY_COMPLETED:
        lines.append(f"Task partially completed: {task.objective}")
    elif task.status == TaskStatus.FAILED:
        lines.append(f"Task failed: {task.objective}")
    elif task.status == TaskStatus.CANCELLED:
        lines.append(f"Task cancelled: {task.objective}")
    elif task.status == TaskStatus.WAITING_FOR_APPROVAL:
        lines.append(f"Task awaiting approval: {task.objective}")
    elif task.status == TaskStatus.WAITING_FOR_INPUT:
        lines.append(f"Task awaiting input: {task.objective}")
    else:
        lines.append(f"Task status {task.status.value}: {task.objective}")

    for i, step in enumerate(task.steps, start=1):
        status_symbol = {
            StepStatus.COMPLETED: "✓",
            StepStatus.FAILED: "✗",
            StepStatus.SKIPPED: "⊘",
            StepStatus.CANCELLED: "⊘",
            StepStatus.WAITING_FOR_APPROVAL: "⏳",
            StepStatus.RUNNING: "▶",
            StepStatus.PENDING: "○",
        }.get(step.status, "?")

        detail = ""
        evidence_str = ""
        if step.result:
            if "message" in step.result:
                detail = f" — {step.result['message']}"
            if "evidence" in step.result and step.result["evidence"]:
                evidence_str = f" [Evidence: {step.result['evidence']}]"
        elif step.status == StepStatus.SKIPPED:
            detail = " — Skipped due to prerequisite failure"

        ver_label = f" ({step.verification_status})" if step.verification_status else ""
        lines.append(f"  {status_symbol} Step {i}: {step.description}{detail}{ver_label}{evidence_str}")

    if task.error:
        lines.append(f"Error: {task.error}")
    return "\n".join(lines)


def execute_task(task: Task, resume_step_approved: bool = False) -> str:
    """Execute task steps sequentially in dependency order enforcing M1 safety boundaries."""
    task.status = TaskStatus.RUNNING
    task.updated_at = time.time()

    loop_count = 0
    while task.current_step_index < len(task.steps):
        loop_count += 1
        if loop_count > MAX_EXECUTION_LOOPS:
            task.status = TaskStatus.FAILED
            task.error = f"Maximum execution loop limit ({MAX_EXECUTION_LOOPS}) exceeded."
            break

        step = task.steps[task.current_step_index]

        # Prevent duplicate execution
        if step.status == StepStatus.COMPLETED:
            task.current_step_index += 1
            continue

        # Check dependencies
        deps_satisfied = True
        for dep_id in step.dependencies:
            dep_step = task.get_step(dep_id)
            if not dep_step or dep_step.status != StepStatus.COMPLETED:
                deps_satisfied = False
                break

        if not deps_satisfied:
            step.status = StepStatus.SKIPPED
            step.verification_status = "SKIPPED_DEPENDENCY_FAILED"
            # Mark subsequent dependent steps as skipped too
            for remaining in task.steps[task.current_step_index + 1:]:
                remaining.status = StepStatus.SKIPPED
                remaining.verification_status = "SKIPPED_DEPENDENCY_FAILED"

            any_completed = any(s.status == StepStatus.COMPLETED for s in task.steps)
            task.status = TaskStatus.PARTIALLY_COMPLETED if any_completed else TaskStatus.FAILED
            task.error = f"Step '{step.description}' skipped because prerequisite step failed."
            break

        # Check M1 policy for this step
        is_approved = resume_step_approved if resume_step_approved else False
        eval_res = jarvis_policy.evaluate(step.tool_name, step.arguments, approved=is_approved)

        if not eval_res.allowed:
            step.status = StepStatus.WAITING_FOR_APPROVAL
            task.status = TaskStatus.WAITING_FOR_APPROVAL
            task.updated_at = time.time()
            desc = step.description or f"execute {step.tool_name}"
            jarvis_policy.set_pending_approval(step.tool_name, step.arguments, description=desc)
            task.outcome = f"Task paused: '{step.description}' requires your confirmation."
            return (
                f"Action '{step.tool_name}' requires your confirmation before execution. "
                f"Please say 'yes' to proceed with task '{task.objective}' or 'no' to cancel."
            )

        # Execute tool via central validated boundary
        step.status = StepStatus.RUNNING
        tool_result = jarvis_tools.execute_tool(step.tool_name, step.arguments, approved=is_approved)
        ver_status, evidence = verify_step_result(step, tool_result)
        step.result = {
            "ok": tool_result.ok,
            "message": tool_result.message,
            "data": tool_result.data,
            "evidence": evidence,
        }
        step.verification_status = ver_status

        if tool_result.ok and ver_status != "FAILED":
            step.status = StepStatus.COMPLETED
            task.current_step_index += 1
            resume_step_approved = False  # Reset approval for subsequent steps
        else:
            step.status = StepStatus.FAILED
            # Mark remaining steps as skipped
            for remaining in task.steps[task.current_step_index + 1:]:
                remaining.status = StepStatus.SKIPPED
                remaining.verification_status = "SKIPPED_PREREQUISITE_FAILED"

            any_completed = any(s.status == StepStatus.COMPLETED for s in task.steps)
            task.status = TaskStatus.PARTIALLY_COMPLETED if any_completed else TaskStatus.FAILED
            if tool_result.ok:
                task.error = f"Step '{step.description}' failed verification: {evidence}"
            else:
                task.error = f"Step '{step.description}' failed: {tool_result.message}"
            break

    # Final outcome determination
    if all(s.status == StepStatus.COMPLETED and s.verification_status == "VERIFIED" for s in task.steps):
        task.status = TaskStatus.COMPLETED
        task.outcome = f"Task completed successfully: {len(task.steps)} steps executed and verified."
    elif task.status == TaskStatus.WAITING_FOR_APPROVAL:
        return task.outcome

    task.updated_at = time.time()
    return generate_task_report(task)


class TaskManager:
    """In-memory manager tracking active and historical tasks."""

    def __init__(self):
        self._active_task: Task | None = None
        self._task_history: list[Task] = []

    def get_active_task(self) -> Task | None:
        return self._active_task

    def set_active_task(self, task: Task) -> bool:
        if self._active_task is not None and not self._active_task.is_terminal():
            return False
        self._active_task = task
        return True

    def cancel_active_task(self) -> Task | None:
        if self._active_task is not None and not self._active_task.is_terminal():
            self._active_task.status = TaskStatus.CANCELLED
            self._active_task.outcome = "Task was cancelled by user."
            self._active_task.updated_at = time.time()
            for step in self._active_task.steps:
                if step.status in {StepStatus.PENDING, StepStatus.RUNNING, StepStatus.WAITING_FOR_APPROVAL}:
                    step.status = StepStatus.CANCELLED
            jarvis_policy.clear_pending_approval()
            cancelled = self._active_task
            self._task_history.append(self._active_task)
            self._active_task = None
            return cancelled
        return None

    def resume_active_task_with_approval(self) -> str:
        if self._active_task is None or self._active_task.status != TaskStatus.WAITING_FOR_APPROVAL:
            return "No task is currently awaiting approval."
        res = execute_task(self._active_task, resume_step_approved=True)
        if self._active_task.is_terminal():
            self._task_history.append(self._active_task)
            self._active_task = None
        return res

    def clear_active_task(self) -> None:
        if self._active_task is not None:
            self._task_history.append(self._active_task)
            self._active_task = None


task_manager = TaskManager()
