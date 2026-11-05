"""Deterministic action policy and risk classification for Jarvis.

Classifies all actions into risk tiers, enforces approval requirements for
high-risk operations, and provides a strict approval gate that prevents
unauthorized or altered execution.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


class RiskLevel(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"


@dataclass(frozen=True)
class PolicyEvaluation:
    allowed: bool
    risk_level: RiskLevel
    reason: str
    requires_approval: bool = False
    action: str = ""
    arguments: dict = field(default_factory=dict)


@dataclass
class PendingApproval:
    tool_name: str
    arguments: dict
    created_at: float
    description: str = ""


# Low-risk tools: autonomous execution allowed
_LOW_RISK_TOOLS = {
    "launch_application",
    "open_path",
    "open_website",
    "search_youtube",
    "search_web",
    "list_windows",
    "wait_for_window",
    "find_files",
    "create_folder",
    "take_screenshot",
    "adjust_volume",
}

# Moderate-risk tools: autonomous execution with strict target validation safeguards
_MODERATE_RISK_TOOLS = {
    "type_text",
    "send_shortcut",
    "move_path",
    "copy_path",
    "rename_path",
}

# High-risk tools: explicit user approval strictly required before execution
_HIGH_RISK_TOOLS = {
    "close_application",
    "send_email",
    "send_message",
    "send_whatsapp_message",
    "delete_file",
    "overwrite_file",
    "submit_form",
    "purchase_product",
    "publish_content",
    "change_security_settings",
    "execute_transaction",
}

# Shortcuts that are destructive or can close windows / cause work loss
_DESTRUCTIVE_SHORTCUTS = {
    "alt+f4",
    "ctrl+w",
    "ctrl+q",
    "ctrl+shift+w",
}

_pending_approval: PendingApproval | None = None


def classify_risk(tool_name: str, arguments: dict | None = None) -> RiskLevel:
    """Classify an action into a deterministic risk tier. Fails closed (HIGH)."""
    arguments = arguments or {}
    tool_lower = (tool_name or "").strip().lower()

    # Destructive shortcuts close windows or terminate workflows
    if tool_lower == "send_shortcut":
        shortcut = str(arguments.get("shortcut", "")).lower().replace(" ", "")
        if shortcut in _DESTRUCTIVE_SHORTCUTS:
            return RiskLevel.HIGH
        return RiskLevel.MODERATE

    if tool_lower in _LOW_RISK_TOOLS:
        return RiskLevel.LOW

    if tool_lower in _MODERATE_RISK_TOOLS:
        return RiskLevel.MODERATE

    if tool_lower in _HIGH_RISK_TOOLS:
        return RiskLevel.HIGH

    # Missing or ambiguous policy information must fail closed
    return RiskLevel.HIGH


def set_pending_approval(tool_name: str, arguments: dict, description: str = "") -> bool:
    """Register a pending action requiring user confirmation.
    
    A new request cannot silently replace an existing pending approval.
    """
    global _pending_approval
    if _pending_approval is not None:
        if _pending_approval.tool_name != tool_name or _pending_approval.arguments != arguments:
            return False
    _pending_approval = PendingApproval(
        tool_name=tool_name,
        arguments=dict(arguments),
        created_at=time.time(),
        description=description,
    )
    return True


def get_pending_approval() -> PendingApproval | None:
    """Retrieve the currently pending action, if any."""
    return _pending_approval


def clear_pending_approval() -> None:
    """Clear any pending action."""
    global _pending_approval
    _pending_approval = None


def has_pending_approval() -> bool:
    """Check if an action is awaiting confirmation."""
    return _pending_approval is not None


def consume_approval(tool_name: str, arguments: dict) -> bool:
    """Consume approval if and only if the exact tool and arguments match.
    
    Any mismatch or alteration invalidates the pending approval.
    Pending approval is consumed at most once.
    """
    global _pending_approval
    if _pending_approval is None:
        return False
    if _pending_approval.tool_name == tool_name and _pending_approval.arguments == arguments:
        _pending_approval = None
        return True
    _pending_approval = None
    return False


def is_approval_response(text: str, pending: PendingApproval | None = None) -> bool:
    """Check if the user response explicitly confirms the pending action.
    
    Unambiguous affirmative responses ('yes', 'yes proceed', 'yes close it',
    'confirm', 'yes close the Notepad application') authorize execution.
    Conversational enthusiasm or ambiguous replies return False.
    """
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower()).strip()
    words = cleaned.split()
    if not words:
        return False

    # Negative or cancellation tokens reject immediately
    negative_words = {"no", "not", "cancel", "stop", "abort", "never", "dont"}
    if any(w in words for w in negative_words):
        return False
    if "don" in words and "t" in words:
        return False

    # Standalone action phrases that mean "do it"
    if cleaned in {"do it", "go ahead", "yes do it", "sure do it", "confirm it"}:
        return True

    # Check for approval prefix
    first_word = words[0]
    if first_word in {"yes", "yep", "yeah", "confirm", "proceed", "approve", "approved", "sure"}:
        if len(words) <= 3:
            enthusiasm_words = {"awesome", "great", "cool", "wonderful", "interesting", "amazing", "good"}
            if any(w in words for w in enthusiasm_words):
                return False
            return True

        # Multi-word response starting with yes/confirm (e.g. "yes close the Notepad application", "yes close it")
        if pending is not None:
            pending_terms = set(
                re.sub(
                    r"[^a-z0-9\s]+",
                    " ",
                    f"{pending.tool_name} {pending.description} " + " ".join(str(v) for v in pending.arguments.values()),
                ).lower().split()
            )
            user_action_words = set(words[1:])
            referential_words = {"it", "that", "this", "app", "application", "window", "task", "close", "please"}
            if user_action_words.intersection(pending_terms) or user_action_words.intersection(referential_words):
                return True
        else:
            return True

    return False


def is_rejection_response(text: str) -> bool:
    """Check if the user response explicitly cancels the pending action."""
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", (text or "").lower()).strip()
    words = cleaned.split()
    if not words:
        return False
    rejection_tokens = {"no", "cancel", "stop", "abort", "reject", "nevermind", "dont"}
    if any(token in words for token in rejection_tokens) or "never mind" in cleaned:
        return len(words) <= 6
    if cleaned in {"don t do it", "do not do it", "no don t do it", "no do not do it"}:
        return True
    return False


def is_repeating_pending_action(text: str, pending: PendingApproval | None) -> bool:
    """Check if the user is repeating the pending action description rather than saying yes or no."""
    if not pending or not text:
        return False
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", text.lower()).strip()
    words = set(cleaned.split())
    if not words:
        return False
    # If the user utterance is an affirmative or rejection, it's not a mere repetition
    if is_approval_response(text, pending) or is_rejection_response(text):
        return False

    pending_terms = set(
        re.sub(
            r"[^a-z0-9\s]+",
            " ",
            f"{pending.tool_name} {pending.description} " + " ".join(str(v) for v in pending.arguments.values()),
        ).lower().split()
    )
    meaningful = {w for w in pending_terms if w not in {"execute", "tool", "the"}}
    overlap = words.intersection(meaningful)
    return len(overlap) >= 1


def evaluate(
    tool_name: str,
    arguments: dict | None = None,
    approved: bool = False,
) -> PolicyEvaluation:
    """Evaluate whether an action is permitted under the policy."""
    args = dict(arguments) if arguments else {}
    risk = classify_risk(tool_name, args)

    if risk == RiskLevel.LOW:
        return PolicyEvaluation(
            allowed=True,
            risk_level=risk,
            reason="Low-risk action allowed to execute autonomously.",
            requires_approval=False,
            action=tool_name,
            arguments=args,
        )

    if risk == RiskLevel.MODERATE:
        return PolicyEvaluation(
            allowed=True,
            risk_level=risk,
            reason="Moderate-risk action allowed with target validation safeguards.",
            requires_approval=False,
            action=tool_name,
            arguments=args,
        )

    # High-risk action
    if approved:
        return PolicyEvaluation(
            allowed=True,
            risk_level=risk,
            reason="High-risk action authorized by explicit user approval.",
            requires_approval=False,
            action=tool_name,
            arguments=args,
        )

    return PolicyEvaluation(
        allowed=False,
        risk_level=risk,
        reason=f"Action '{tool_name}' is classified as HIGH RISK and requires explicit user approval before execution.",
        requires_approval=True,
        action=tool_name,
        arguments=args,
    )
