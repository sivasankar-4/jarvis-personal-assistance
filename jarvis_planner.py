"""Conservative local planner for direct Windows-control requests.

The model may use the same registry through native tool calls; this layer makes
high-confidence voice commands work even when a provider lacks tool calling.
"""

from __future__ import annotations

import re

from jarvis_tools import execute_tool


def plan_direct_desktop_request(text: str):
    cleaned = " ".join((text or "").strip().split())
    cleaned = re.sub(r"^(?:hey\s+)?(?:jarvis[,:]?\s+)?", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"^(?:(?:can|could|would|will)\s+you\s+|please\s+)", "", cleaned, flags=re.I).strip()
    cleaned = cleaned.rstrip(".!?")
    lowered = cleaned.lower()

    if lowered in {"cancel", "cancel task", "stop"}:
        return "Cancelled the current desktop task."

    shortcut = re.match(r"^(?:press|send)\s+(.+?)(?:\s+(?:shortcut|keys?))?$", lowered)
    if shortcut:
        return execute_tool("send_shortcut", {"shortcut": shortcut.group(1)})

    switch = re.match(r"^(?:switch|change)\s+(?:the\s+)?window$", lowered)
    if switch:
        return execute_tool("send_shortcut", {"shortcut": "alt+tab"})

    if lowered in {"click", "click on"}:
        return "What would you like me to click on?"

    click = re.match(r"^click\s+(?:on\s+)?(.+)$", cleaned, re.I)
    if click:
        target_desc = click.group(1).strip()
        if target_desc.lower() in {"", "the"}:
            return "What would you like me to click on?"
        if " and " in f" {target_desc.lower()} ":
            return None
        located = execute_tool("locate_element", {"description": target_desc, "target": "screen"})
        if not located.ok:
            return located
        data = located.data or {}
        if data.get("confirmed") is not True or "x" not in data or "y" not in data:
            return f"I found something that might be {target_desc}, but I couldn't confirm it, so I won't click."
        clicked = execute_tool(
            "click_and_verify",
            {"x": int(data["x"]), "y": int(data["y"]), "button": "left", "clicks": 1},
        )
        if not clicked.ok:
            return clicked
        cdata = clicked.data or {}
        if cdata.get("changed") or cdata.get("foreground_after") != cdata.get("foreground_before"):
            return f"Clicked on {target_desc}. {clicked.message}"
        return f"I clicked on {target_desc}, but no visible change was detected."

    type_match = re.match(r"^type\s+(.+?)\s+(?:into|in)\s+(.+)$", cleaned, re.I)
    if type_match:
        text_arg = type_match.group(1).strip().strip('"\'')
        window_arg = type_match.group(2).strip().rstrip(".!?").strip('"\'')
        return execute_tool("type_text", {"text": text_arg, "expected_window": window_arg})

    folder = re.match(r"^(?:open|show)\s+(?:the\s+)?(?:folder|file)\s+(.+)$", cleaned, re.I)
    if folder:
        return execute_tool("open_path", {"path": folder.group(1).strip('"')})

    website = re.match(r"^(?:open|visit|go to)\s+(https?://\S+)$", cleaned, re.I)
    if website:
        return execute_tool("open_website", {"url": website.group(1)})

    youtube = re.match(
        r"^(?:open|launch|start)\s+youtube(?:\s+and\s+(?:search(?:\s+for)?|type)\s+(.+))?$",
        cleaned,
        re.I,
    )
    if youtube:
        query = youtube.group(1)
        if query:
            return execute_tool("search_youtube", {"query": query.strip()})
        return execute_tool("open_website", {"url": "https://www.youtube.com"})

    close_match = re.match(r"^(?:close|quit|exit)\s+(?:the\s+)?(.+?)(?:\s+(?:app|application|window))?$", cleaned, re.I)
    if close_match:
        target = close_match.group(1).strip().strip('"\'')
        if target.lower() not in {"application", "app", "window", "task", "the application", "the window", ""}:
            return execute_tool("close_application", {"name": target})

    app = re.match(r"^(?:open|launch|start|run)\s+(.+)$", cleaned, re.I)
    if app:
        target = app.group(1).strip().strip('"')
        if re.match(r"^(?:[a-zA-Z]:[\\/]|[\\/]|~[\\/])", target):
            return execute_tool("open_path", {"path": target})
        if target.lower() in {"whatsapp", "gmail", "google", "github", "maps", "calendar", "drive"}:
            return None
        return execute_tool("launch_application", {"name": target})

    return None
