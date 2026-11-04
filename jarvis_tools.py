"""Validated registry of desktop tools available to Jarvis planning."""

from __future__ import annotations

import json
import urllib.parse

import jarvis_policy
import jarvis_windows


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": "List visible Windows application windows without exposing titles.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for_window",
            "description": "Wait for a visible Windows application window by process name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "process_name": {"type": "string"},
                    "timeout_seconds": {"type": "integer"},
                },
                "required": ["process_name", "timeout_seconds"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "launch_application",
            "description": "Launch an installed Windows application by name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_path",
            "description": "Open an existing local file or folder using Windows.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Open a valid http or https URL in the default browser.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_youtube",
            "description": "Open a YouTube results page for an explicit search query.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web using Google for an explicit search query.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_shortcut",
            "description": "Send an explicitly requested safe shortcut to the focused window.",
            "parameters": {
                "type": "object",
                "properties": {"shortcut": {"type": "string"}},
                "required": ["shortcut"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type user-supplied text only when the named application is focused. Never press Enter or submit a form.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "expected_window": {"type": "string"},
                },
                "required": ["text", "expected_window"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_application",
            "description": "Close an application window. High-risk action requiring confirmation.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
]


def search_youtube(query: str) -> jarvis_windows.ToolResult:
    if not isinstance(query, str) or not query.strip():
        return jarvis_windows.ToolResult(False, "Please say what you want to search for on YouTube.")
    safe_query = query.strip()
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(safe_query)
    result = jarvis_windows.open_website(url)
    if result.ok:
        return jarvis_windows.ToolResult(True, f"Searching YouTube for {safe_query}.", result.data)
    return result


def search_web(query: str) -> jarvis_windows.ToolResult:
    if not isinstance(query, str) or not query.strip():
        return jarvis_windows.ToolResult(False, "Please say what you want to search for.")
    safe_query = query.strip()
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(safe_query)
    result = jarvis_windows.open_website(url)
    if result.ok:
        return jarvis_windows.ToolResult(True, f"Searching Google for {safe_query}.", result.data)
    return result


_TOOLS: dict[str, tuple[any, dict[str, type]]] = {
    "list_windows": ("list_windows", {}),
    "wait_for_window": ("wait_for_window", {"process_name": str, "timeout_seconds": int}),
    "launch_application": ("launch_application", {"name": str}),
    "open_path": ("open_path", {"path": str}),
    "open_website": ("open_website", {"url": str}),
    "search_youtube": (search_youtube, {"query": str}),
    "search_web": (search_web, {"query": str}),
    "send_shortcut": ("send_shortcut", {"shortcut": str}),
    "type_text": ("type_text", {"text": str, "expected_window": str}),
    "close_application": ("close_application", {"name": str}),
}


def execute_tool(name: str, arguments: str | dict, approved: bool = False) -> jarvis_windows.ToolResult:
    """Parse and validate model arguments, enforce deterministic policy, and execute tool.
    
    Arbitrary functions, shell commands, unknown tools, extra arguments, and
    unauthorized high-risk actions are strictly rejected.
    """
    if name not in _TOOLS:
        res = jarvis_windows.ToolResult(False, "That requested desktop tool is not available.")
        jarvis_windows.audit_action(name, res, risk_level="HIGH", approval_status="NOT_APPROVED")
        return res

    try:
        payload = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (TypeError, json.JSONDecodeError):
        res = jarvis_windows.ToolResult(False, "The desktop tool request was malformed.")
        jarvis_windows.audit_action(name, res, risk_level="HIGH", approval_status="NOT_APPROVED")
        return res

    if not isinstance(payload, dict):
        res = jarvis_windows.ToolResult(False, "The desktop tool request did not pass validation.")
        jarvis_windows.audit_action(name, res, risk_level="HIGH", approval_status="NOT_APPROVED")
        return res

    target_func, allowed_types = _TOOLS[name]
    function = getattr(jarvis_windows, target_func) if isinstance(target_func, str) else target_func

    # Reject unknown, extra, or missing arguments
    if set(payload.keys()) != set(allowed_types.keys()):
        res = jarvis_windows.ToolResult(False, "The desktop tool request did not pass validation.")
        jarvis_windows.audit_action(name, res, risk_level="HIGH", approval_status="NOT_APPROVED")
        return res

    # Reject invalid argument types
    for key, expected_type in allowed_types.items():
        if not isinstance(payload[key], expected_type):
            res = jarvis_windows.ToolResult(False, "The desktop tool request did not pass validation.")
            jarvis_windows.audit_action(name, res, risk_level="HIGH", approval_status="NOT_APPROVED")
            return res

    # Enforce deterministic risk policy
    is_approved = approved
    eval_res = jarvis_policy.evaluate(name, payload, approved=is_approved)

    if not eval_res.allowed:
        desc = f"close {payload.get('name', '')}" if name == "close_application" else f"execute {name}"
        set_ok = jarvis_policy.set_pending_approval(name, payload, description=desc)
        if not set_ok:
            existing = jarvis_policy.get_pending_approval()
            existing_desc = existing.description if existing else "a pending action"
            res = jarvis_windows.ToolResult(
                False,
                f"Cannot request approval for '{name}': another action ({existing_desc}) is already awaiting confirmation. Please say 'yes' or 'no' first.",
                data={
                    "requires_approval": True,
                    "risk_level": eval_res.risk_level.value,
                    "tool": name,
                    "arguments": payload,
                },
            )
            jarvis_windows.audit_action(
                name,
                res,
                risk_level=eval_res.risk_level.value,
                approval_status="NOT_APPROVED",
            )
            return res

        res = jarvis_windows.ToolResult(
            False,
            f"Action '{name}' requires your confirmation before execution.",
            data={
                "requires_approval": True,
                "risk_level": eval_res.risk_level.value,
                "tool": name,
                "arguments": payload,
            },
        )
        jarvis_windows.audit_action(
            name,
            res,
            risk_level=eval_res.risk_level.value,
            approval_status="PENDING" if eval_res.requires_approval else "NOT_APPROVED",
        )
        return res

    # Execute validated tool
    result = function(**payload)
    if not isinstance(result, jarvis_windows.ToolResult):
        result = jarvis_windows.ToolResult(True, str(result))

    approval_status = "APPROVED" if is_approved else "AUTONOMOUS"
    jarvis_windows.audit_action(
        name,
        result,
        risk_level=eval_res.risk_level.value,
        approval_status=approval_status,
    )
    return result
