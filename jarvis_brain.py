"""
jarvis_brain.py - Hybrid Cloud + Offline AI Brain with Rate Limit Failover
Uses free Groq endpoints for ultra-fast responses, and smoothly falls back
to offline local processing if API rate limits or network issues occur.
"""

import os
import time
import re
from openai import OpenAI
import jarvis_actions
import jarvis_planner
import jarvis_policy
import jarvis_tasks
import jarvis_tools
from jarvis_memory import (
    DuplicateMemoryError,
    MemoryError,
    MemoryStore,
    MemoryValidationError,
    validate_content,
)

KEYFILE = os.path.expanduser("~/.openjarvis/free.key")
MODELS = ["openai/gpt-oss-20b", "qwen/qwen3.8-27b"]

_client = None
_history = []
_cooldown_until = 0.0
_memory_store = MemoryStore()
_pending_memory_operation = None
MAX_HISTORY_MESSAGES = 12

SYSTEM_PROMPT = """You are Jarvis, a capable, practical personal AI assistant running on the user's Windows computer.

Your job is to help with coding, technical explanations, research planning, everyday desktop assistance, and clear thinking. You receive text transcribed from microphone audio by speech recognition software; you do not interpret raw audio directly. When asked about your ability to hear, explain that you process transcribed text from microphone audio. Treat the conversation history as context for follow-up questions; resolve references such as "that error", "the second option", or "continue" from that history when possible.

First identify the request type: a factual question, an explanation, a local command, research request, or a multi-step task. Give accurate, directly useful answers. For coding: explain the likely cause, give a small safe example or concrete next step, and state assumptions. For research or time-sensitive facts: never imply you searched, browsed, checked a source, or know live information unless a retrieval result is actually supplied. If current information is needed and no result is available, say so plainly and offer a search action when appropriate.

Local actions are handled separately and their completion messages are reliable. Do not claim you launched an app, read a file, sent a message, changed a setting, or completed an external task unless the action layer returned that result. If an action needs missing details, ask one concise clarification rather than inventing values. For multi-step tasks, state the next useful step and ask only for information needed to proceed.

When tool definitions are available, use only those tools for supported Windows actions. Never generate shell commands, scripts, or tool arguments outside their schemas. Observe tool results before describing the outcome. Do not use a tool for deletion, sending messages, purchases, form submission, security changes, or other consequential actions; ask the user for confirmation and explain that the operation is not available yet.

Use approved personal context only when relevant, and never reveal it unnecessarily. Never request, retain, or expose passwords, API keys, authentication tokens, or other secrets. Keep normal replies concise (usually 1-3 sentences); provide a structured, detailed explanation only when the user asks for depth or the task clearly needs it. Be direct and natural; avoid filler and corporate jargon.
"""


def _memory_category(text):
    lowered = text.lower()
    if any(word in lowered for word in ("prefer", "preference", "favorite", "favourite")):
        return "preference"
    if any(word in lowered for word in ("project", "repository", "repo", "working on")):
        return "project"
    return "fact"


def _format_memories(memories):
    return "; ".join(f"{memory.id}: {memory.content}" for memory in memories)


def _perform_pending_memory_operation():
    global _pending_memory_operation
    operation = _pending_memory_operation
    _pending_memory_operation = None
    try:
        if operation["kind"] == "save":
            saved = _memory_store.save(operation["content"], operation["category"])
            return f"Saved memory {saved.id}. I'll use it when relevant."
        if operation["kind"] == "delete":
            if _memory_store.delete(operation["id"]):
                return f"Deleted memory {operation['id']}."
            return "That memory no longer exists."
        if operation["kind"] == "update":
            updated = _memory_store.update(operation["id"], operation["content"])
            if updated:
                return f"Updated memory {updated.id}."
            return "That memory no longer exists."
    except DuplicateMemoryError as exc:
        return f"That is already stored as memory {exc.memory_id}."
    except MemoryError:
        return "I couldn't update local memory storage. Your existing memories were left unchanged."
    return "I couldn't complete that memory request."


def _handle_memory_command(q):
    """Handle explicit memory commands, returning None when this is not one."""
    global _pending_memory_operation
    cleaned = " ".join(q.strip().split())
    lowered = cleaned.lower().rstrip(".!?")

    if _pending_memory_operation:
        if lowered in {"yes", "yes please", "confirm", "save it", "do it"}:
            return _perform_pending_memory_operation()
        if lowered in {"no", "no thanks", "cancel", "never mind", "nevermind"}:
            _pending_memory_operation = None
            return "Okay, I did not change your memories."
        # Do not let an old confirmation remain active after the user moves on.
        _pending_memory_operation = None

    if lowered in {"list memories", "show memories", "what do you remember", "what do you remember about me"}:
        try:
            memories = _memory_store.list()
            return "I don't have any saved memories yet." if not memories else "Saved memories: " + _format_memories(memories)
        except MemoryError:
            return "I couldn't open local memory storage right now."

    if lowered in {"remember", "remember that"}:
        return "What should I remember? I will ask before saving it."
    match = re.match(r"^(?:please )?remember(?: that)?\s+(.+)$", cleaned, re.I)
    if match:
        content = match.group(1)
        try:
            content = validate_content(content)
        except MemoryValidationError as exc:
            return str(exc)
        _pending_memory_operation = {"kind": "save", "content": content, "category": _memory_category(content)}
        return f"Should I save this memory: {content}?"

    update_match = re.match(r"^(?:update|change) memory (\d+) to\s+(.+)$", cleaned, re.I)
    if update_match:
        memory_id, content = int(update_match.group(1)), update_match.group(2)
        try:
            content = validate_content(content)
        except MemoryValidationError as exc:
            return str(exc)
        _pending_memory_operation = {"kind": "update", "id": memory_id, "content": content}
        return f"Should I replace memory {memory_id} with: {content}?"

    delete_by_id = re.match(r"^(?:forget|delete|remove) memory (\d+)$", lowered)
    if delete_by_id:
        memory_id = int(delete_by_id.group(1))
        try:
            memory = _memory_store.get(memory_id)
        except MemoryError:
            return "I couldn't open local memory storage right now."
        if not memory:
            return f"I couldn't find memory {memory_id}."
        _pending_memory_operation = {"kind": "delete", "id": memory.id}
        return f"Should I delete memory {memory.id}: {memory.content}?"

    delete_match = re.match(r"^(?:forget|delete|remove)(?: that)?\s+(.+)$", cleaned, re.I)
    if delete_match:
        phrase = delete_match.group(1)
        try:
            matches = _memory_store.relevant(phrase)
        except MemoryError:
            return "I couldn't open local memory storage right now."
        if not matches:
            return "I couldn't find a saved memory matching that."
        if len(matches) > 1:
            return "I found multiple memories: " + _format_memories(matches) + ". Say 'forget memory' followed by its number."
        memory = matches[0]
        _pending_memory_operation = {"kind": "delete", "id": memory.id}
        return f"Should I delete memory {memory.id}: {memory.content}?"

    return None


def _relevant_memory_context(q):
    """Format only relevant approved memories for a cloud request."""
    try:
        memories = _memory_store.relevant(q)
    except MemoryError:
        return ""
    if not memories:
        return ""
    items = "\n".join(f"- {memory.category}: {memory.content}" for memory in memories)
    return "Approved personal context. Use it only if it is relevant; do not claim other memory:\n" + items


def _record_turn(user_text, assistant_text):
    """Keep a bounded local conversation context for cloud follow-up questions."""
    global _history
    _history.extend(({"role": "user", "content": user_text}, {"role": "assistant", "content": assistant_text}))
    _history = _history[-MAX_HISTORY_MESSAGES:]


def _safe_diagnostic(message, error=None):
    """Log useful failure context without echoing provider messages or secrets."""
    detail = f" ({type(error).__name__})" if error else ""
    print(f"[Jarvis brain] {message}{detail}")


def _fallback_response(q):
    response = _local_fallback(q)
    _record_turn(q, response)
    return response


def _execute_tool_calls(tool_calls):
    """Execute only the registered, schema-validated desktop tools."""
    results = []
    for tool_call in list(tool_calls)[:3]:
        function = getattr(tool_call, "function", None)
        name = getattr(function, "name", "")
        arguments = getattr(function, "arguments", "")
        result = jarvis_tools.execute_tool(name, arguments)
        results.append(result.message)
    return " ".join(results) if results else None


class _ToolsUnavailableError(Exception):
    """A model's native-tools call failed; another model may still support tools."""


def _request_with_tools(model, messages):
    """Use native tool calls when supported, then degrade to normal chat safely."""
    if jarvis_policy.has_pending_approval():
        response = _client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=350,
            temperature=0.6,
        )
        return (response.choices[0].message.content or "").strip()

    try:
        response = _client.chat.completions.create(
            model=model,
            messages=messages,
            tools=jarvis_tools.TOOL_SCHEMAS,
            tool_choice="auto",
            max_tokens=350,
            temperature=0.6,
        )
        message = response.choices[0].message
        tool_result = _execute_tool_calls(getattr(message, "tool_calls", None) or [])
        if tool_result:
            return tool_result
        return (message.content or "").strip()
    except Exception as tool_error:
        _safe_diagnostic(f"Native tools unavailable for {model}; using chat", tool_error)
        if _is_rate_limited(tool_error):
            raise
        raise _ToolsUnavailableError(str(type(tool_error).__name__)) from tool_error


_DEGRADED_SYSTEM_NOTICE = (
    "IMPORTANT: You are answering WITHOUT tool access for this reply only, because "
    "the tool service is temporarily unavailable. Answer normal questions normally. "
    "If the user asks you to DO something on the computer (open, find, create, move, "
    "copy, rename, search, type, or similar actions), do NOT say you lack that tool "
    "or ability. Say instead that action execution is temporarily unavailable and "
    "ask them to try again in a moment."
)


def _degraded_chat_response(messages):
    """Plain-chat answer used only after every model failed WITH tools."""
    degraded = list(messages) + [{"role": "system", "content": _DEGRADED_SYSTEM_NOTICE}]
    for model in MODELS:
        try:
            response = _client.chat.completions.create(
                model=model, messages=degraded, max_tokens=350, temperature=0.6,
            )
            text = (response.choices[0].message.content or "").strip()
            if text:
                return text
        except Exception as e:
            if _is_rate_limited(e):
                raise
            _safe_diagnostic(f"Degraded chat failed for {model}", e)
    return None


def _get_api_key():
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"].strip()
    if os.path.exists(KEYFILE):
        with open(KEYFILE, encoding="utf-8") as key_file:
            return key_file.read().strip()
    return None


def ready():
    """Returns True if the cloud brain is active and not currently rate-limited."""
    key = _get_api_key()
    if not key:
        return False
    return time.time() >= _cooldown_until


def _is_rate_limited(e):
    status = getattr(e, "status_code", None)
    if status == 429:
        return True
    s = str(e).lower()
    return any(w in s for w in ("429", "rate limit", "too many requests", "rate_limit", "exceeded"))


def _retry_after_seconds(e, default=60):
    s = str(e).lower()
    m = re.search(r"try again in\s*(\d+)m\s*([0-9]+(?:\.[0-9]+)?)s", s)
    if m:
        return int(float(m.group(1)) * 60 + float(m.group(2))) + 2
    m = re.search(r"try again in\s*([0-9]+(?:\.[0-9]+)?)s", s)
    if m:
        return int(float(m.group(1))) + 2
    return default


def _set_cooldown(secs):
    global _cooldown_until
    _cooldown_until = max(_cooldown_until, time.time() + float(secs))


def _local_fallback(q):
    """
    Called when Groq API is temporarily rate-limited.
    Provides immediate local action execution or cached briefing without crashing.
    """
    # 1. Try deterministic local actions first
    action_res = jarvis_actions.try_action(q)
    if action_res:
        return action_res

    # 2. General intelligent local response
    return (
        "The AI service is unavailable right now, but local commands still work. "
        "I can open supported apps, search YouTube or Google, and provide market updates."
    )


def think(q):
    """
    Main entry point for queries. Tries local actions first, then cloud AI,
    with automatic fallback if rate limited.
    """
    global _client, _history

    # Conversational approval gate for pending high-risk actions
    if jarvis_policy.has_pending_approval():
        pending = jarvis_policy.get_pending_approval()
        cleaned = " ".join(q.strip().split())
        lowered = cleaned.lower().rstrip(".!?")
        if jarvis_policy.is_approval_response(lowered, pending):
            active_task = jarvis_tasks.task_manager.get_active_task()
            if active_task and active_task.status == jarvis_tasks.TaskStatus.WAITING_FOR_APPROVAL:
                response = jarvis_tasks.task_manager.resume_active_task_with_approval()
                jarvis_policy.clear_pending_approval()
                _record_turn(q, response)
                return response
            res = jarvis_tools.execute_tool(pending.tool_name, pending.arguments, approved=True)
            jarvis_policy.clear_pending_approval()
            response = res.message
            _record_turn(q, response)
            return response
        if jarvis_policy.is_rejection_response(lowered):
            active_task = jarvis_tasks.task_manager.get_active_task()
            if active_task and active_task.status == jarvis_tasks.TaskStatus.WAITING_FOR_APPROVAL:
                jarvis_tasks.task_manager.cancel_active_task()
            jarvis_policy.clear_pending_approval()
            response = "Okay, I cancelled that action."
            _record_turn(q, response)
            return response
        if jarvis_policy.is_repeating_pending_action(lowered, pending):
            desc = pending.description or f"execute {pending.tool_name}"
            response = f"I am waiting for your confirmation to {desc}. Please say 'yes' to proceed or 'no' to cancel."
            _record_turn(q, response)
            return response

    memory_response = _handle_memory_command(q)
    if memory_response:
        return memory_response

    # Explicit task cancellation command
    lowered_q = " ".join(q.strip().lower().split()).rstrip(".!?")
    if lowered_q in {"cancel task", "stop task", "cancel current task", "abort task"}:
        cancelled = jarvis_tasks.task_manager.cancel_active_task()
        if cancelled:
            response = f"Task cancelled: {cancelled.objective}"
            _record_turn(q, response)
            return response
        return "There is no active task running."

    # Multi-step task orchestrator
    if jarvis_tasks.is_multistep_objective(q):
        task = jarvis_tasks.create_task_from_objective(q)
        if task.status == jarvis_tasks.TaskStatus.FAILED:
            response = task.outcome or task.error or "Could not create task plan."
            _record_turn(q, response)
            return response
        if not jarvis_tasks.task_manager.set_active_task(task):
            response = "Another task is already in progress. Please wait for it to finish or say 'cancel task'."
            _record_turn(q, response)
            return response
        response = jarvis_tasks.execute_task(task)
        if task.is_terminal():
            jarvis_tasks.task_manager.clear_active_task()
        _record_turn(q, response)
        return response

    planned = jarvis_planner.plan_direct_desktop_request(q)
    if planned:
        response = planned if isinstance(planned, str) else planned.message
        _record_turn(q, response)
        return response

    # 1. Immediate local action check
    local_done = jarvis_actions.try_action(q)
    if local_done:
        _record_turn(q, local_done)
        return local_done

    key = _get_api_key()
    if not key or time.time() < _cooldown_until:
        return _fallback_response(q)

    if _client is None:
        try:
            _client = OpenAI(api_key=key, base_url="https://api.groq.com/openai/v1")
        except Exception as exc:
            _safe_diagnostic("Could not initialize the AI provider", exc)
            return _fallback_response(q)

    context = _relevant_memory_context(q)
    system_prompt = SYSTEM_PROMPT if not context else SYSTEM_PROMPT + "\n" + context
    msgs = [{"role": "system", "content": system_prompt}] + _history[-6:] + [{"role": "user", "content": q}]

    tools_unavailable = False
    for model in MODELS:
        try:
            answer = _request_with_tools(model, msgs)
            if not answer:
                raise ValueError("Empty model response")
            _record_turn(q, answer)
            return answer
        except Exception as e:
            if isinstance(e, _ToolsUnavailableError):
                tools_unavailable = True
                _safe_diagnostic(f"Model {model} did not respond; trying the next option", e)
                continue
            if _is_rate_limited(e):
                wait_sec = _retry_after_seconds(e)
                _set_cooldown(wait_sec)
                print(f"[Rate-limit triggered: cooldown for {wait_sec}s]")
                return _fallback_response(q)
            _safe_diagnostic(f"Model {model} did not respond; trying the next option", e)

    if tools_unavailable:
        degraded = _degraded_chat_response(msgs)
        if degraded:
            _record_turn(q, degraded)
            return degraded
    return _fallback_response(q)


if __name__ == "__main__":
    print("Testing Jarvis Brain...")
    print(think("what time is it"))
    print(think("how are you"))
