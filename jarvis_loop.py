"""Bounded observe -> act -> verify GUI loop (TASK-024).

Runs only as the fallback for multi-step objectives the deterministic
parser cannot build (see jarvis_brain.think). The brain proposes ONE
action per iteration as strict JSON; every action is policy-checked,
executed through the grounded tool path, and verified. The loop never
trusts a claim of completion: only a fresh observation answering YES
ends it COMPLETED.
"""

import json
import re

import jarvis_brain
import jarvis_policy
import jarvis_tasks
from jarvis_planner import grounded_click
from jarvis_tools import execute_tool
from jarvis_vision import ask_screen

MAX_LOOP_STEPS = 5

_GUI_VERBS = re.compile(r"\b(?:click|type|press|play)\b", re.I)

_OBSERVE_QUESTION = (
    "Goal: {goal}. What is visible on the screen now? "
    "Is the goal already done? Answer YES or NO first. "
    "If not, name the single next UI element to interact with and the kind of action it needs (click, type, or key) "
    "— or, if an app or website the goal needs is not open yet, say OPEN followed by its name instead."
)


def should_attempt_loop(objective):
    """True when a parser-failed objective plausibly needs GUI steps."""
    return bool(_GUI_VERBS.search(objective or ""))


def _observation_done(observation):
    tokens = re.findall(r"[a-z]+", (observation or "").lower())
    return bool(tokens) and tokens[0] == "yes"


def _normalise_observation(observation):
    return re.sub(r"\s+", " ", (observation or "").strip().lower())


_ACTION_KEYS = {
    "click": {"action", "target"},
    "type": {"action", "text", "window"},
    "key": {"action", "shortcut"},
    "open": {"action", "kind", "value"},
}


def _parse_choice(raw):
    """Strictly validate one chooser reply. Returns a dict or None."""
    if not raw:
        return None
    try:
        choice = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(choice, dict):
        return None
    if choice.get("done") is True:
        if set(choice) == {"done", "evidence"} and isinstance(choice.get("evidence"), str):
            return choice
        return None
    if "stuck" in choice:
        if set(choice) == {"stuck"} and isinstance(choice.get("stuck"), str):
            return choice
        return None
    action = choice.get("action")
    if action not in _ACTION_KEYS or set(choice) != _ACTION_KEYS[action]:
        return None
    if action == "click" and not isinstance(choice.get("target"), str):
        return None
    if action == "type" and not (isinstance(choice.get("text"), str) and isinstance(choice.get("window"), str)):
        return None
    if action == "key" and not isinstance(choice.get("shortcut"), str):
        return None
    if action == "open" and not (choice.get("kind") in ("app", "website") and isinstance(choice.get("value"), str)):
        return None
    return choice


def _tool_for(choice):
    action = choice["action"]
    if action == "click":
        return "click_and_verify", {}
    if action == "type":
        return "type_text", {"text": choice["text"], "expected_window": choice["window"]}
    if action == "key":
        return "send_shortcut", {"shortcut": choice["shortcut"]}
    if action == "open":
        if choice["kind"] == "app":
            return "launch_application", {"name": choice["value"]}
        return "open_website", {"url": choice["value"]}
    return None, {}


def _describe(choice):
    action = choice["action"]
    if action == "click":
        return f"Click on {choice['target']}"
    if action == "type":
        return f"Type text into {choice['window']}"
    if action == "key":
        return f"Press {choice['shortcut']}"
    if action == "open":
        return f"Open {choice['kind']} {choice['value']}"
    return action


def _report(objective, steps, outcome, reason):
    lines = [f"Goal: {objective}"]
    for step in steps:
        lines.append(
            f"Step {step['iteration']}: {step['action']} — {step['verification']} ({step['evidence']})"
        )
    lines.append(f"Outcome: {outcome} — {reason}")
    return "\n".join(lines)


def run_gui_loop(objective):
    steps = []
    previous_observation = None
    outcome = None
    reason = ""
    for iteration in range(1, MAX_LOOP_STEPS + 1):
        observed = ask_screen(_OBSERVE_QUESTION.format(goal=objective), "screen")
        if not observed.ok:
            outcome, reason = "FAILED", f"I couldn't observe the screen: {observed.message}"
            break
        observation = observed.message or ""
        if _observation_done(observation):
            outcome, reason = "COMPLETED", "The latest observation confirms the goal is done."
            break
        normalised = _normalise_observation(observation)
        if previous_observation is not None and normalised == previous_observation:
            outcome, reason = "PARTIAL", "The screen stopped changing, so I stopped instead of repeating myself."
            break
        previous_observation = normalised
        choice = _parse_choice(jarvis_brain.choose_loop_action(objective, observation, steps))
        if choice is None:
            outcome, reason = "PARTIAL", "I couldn't decide the next step, so I stopped."
            break
        if choice.get("done") is True:
            confirm = ask_screen(_OBSERVE_QUESTION.format(goal=objective), "screen")
            if confirm.ok and _observation_done(confirm.message or ""):
                outcome, reason = "COMPLETED", "The latest observation confirms the goal is done."
            else:
                outcome, reason = "PARTIAL", "I thought the goal was done, but the screen does not confirm it."
            break
        if "stuck" in choice:
            outcome, reason = "PARTIAL", f"I got stuck: {choice['stuck']}"
            break
        tool_name, arguments = _tool_for(choice)
        evaluation = jarvis_policy.evaluate(tool_name, arguments)
        if not evaluation.allowed or evaluation.requires_approval:
            outcome, reason = "PARTIAL", (
                f"Step {iteration} needs '{tool_name}', which needs your direct say-so. "
                "Please say it as a direct command instead."
            )
            break
        if choice["action"] == "click":
            result = grounded_click(choice["target"])
        else:
            result = execute_tool(tool_name, arguments)
        step = jarvis_tasks.TaskStep(
            step_id=f"loop_{iteration}",
            description=_describe(choice),
            tool_name=tool_name,
            arguments=arguments,
        )
        status, evidence = jarvis_tasks.verify_step_result(step, result)
        steps.append({
            "iteration": iteration,
            "action": _describe(choice),
            "verification": status,
            "evidence": evidence,
        })
        if status == "FAILED":
            outcome, reason = "PARTIAL", f"Step {iteration} did not verify: {evidence}"
            break
    else:
        outcome, reason = "PARTIAL", f"I reached the {MAX_LOOP_STEPS}-step limit without the goal being confirmed done."
    return _report(objective, steps, outcome, reason)


def spoken_summary(report):
    """One spoken line for a loop report: the outcome, without the step detail."""
    outcome = ""
    reason = ""
    for line in (report or "").splitlines():
        if line.startswith("Outcome: "):
            outcome, _, reason = line[len("Outcome: "):].partition(" — ")
    if outcome == "COMPLETED":
        return f"Done. {reason}".strip()
    if outcome == "FAILED":
        return f"That failed. {reason}".strip()
    if outcome == "PARTIAL":
        return f"I couldn't finish that. {reason}".strip()
    return "I couldn't finish that."

