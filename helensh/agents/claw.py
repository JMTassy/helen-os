"""HELEN OS — CLAW Skills Agent.

CLAW handles external connections: Telegram, web fetch, notifications.

Design constraints:
  - require_approval=True always (ClawAction never self-executes)
  - claw_governor_gate() returns PENDING for known actions, DENY for unknown
  - `claw_external` is the kernel action type (added to KNOWN_ACTIONS + WRITE_ACTIONS)
  - all planned actions are receipted proposals; CLAW never directly mutates state
  - authority: False always enforced
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Constants ─────────────────────────────────────────────────────────

# These are CLAW-internal skill names (sub-types of claw_external)
CLAW_KNOWN_SKILLS = frozenset({
    "telegram_send",
    "telegram_read",
    "web_fetch",
    "notify",
    "ping",
})

CLAW_WRITE_SKILLS = frozenset({
    "telegram_send",
    "notify",
})

# ── ClawAction ───────────────────────────────────────────────────────


@dataclass
class ClawAction:
    """A planned external action — always requires approval before execution.

    Fields:
      skill          — one of CLAW_KNOWN_SKILLS
      payload        — skill-specific parameters
      rationale      — why this action is proposed
      require_approval — always True; structural invariant
      authority      — always False; structural invariant
      planned_at_ns  — planning timestamp (nanoseconds)
    """
    skill: str
    payload: Dict[str, Any]
    rationale: str
    require_approval: bool = field(default=True, init=False)
    authority: bool = field(default=False, init=False)
    planned_at_ns: int = field(default_factory=lambda: time.monotonic_ns())

    def __post_init__(self) -> None:
        # Structural invariants — cannot be overridden
        object.__setattr__(self, "require_approval", True)
        object.__setattr__(self, "authority", False)

    def to_kernel_proposal(self) -> dict:
        """Convert to a kernel-compatible proposal dict for claw_external."""
        return {
            "action": "claw_external",
            "target": self.skill,
            "payload": {
                "skill": self.skill,
                "params": self.payload,
                "rationale": self.rationale,
                "require_approval": True,
            },
            "authority": False,
        }


# ── Governor gate ─────────────────────────────────────────────────────


def claw_governor_gate(action: ClawAction) -> str:
    """Evaluate a ClawAction. Returns PENDING (known) or DENY (unknown).

    CLAW actions never return ALLOW directly — they must go through
    the approval flow. This gate is called before kernel routing.
    """
    if action.skill in CLAW_KNOWN_SKILLS:
        return "PENDING"
    return "DENY"


# ── Skill implementations ─────────────────────────────────────────────
# These are PLAN-ONLY stubs. They return a dict describing what would happen.
# Actual execution requires explicit approval and is done by the execution layer.


def _plan_telegram_send(chat_id: str, text: str, **_kwargs: Any) -> dict:
    return {
        "type": "telegram_send",
        "chat_id": chat_id,
        "text": text,
        "estimated_chars": len(text),
        "status": "PLANNED",
    }


def _plan_telegram_read(chat_id: str, limit: int = 10, **_kwargs: Any) -> dict:
    return {
        "type": "telegram_read",
        "chat_id": chat_id,
        "limit": limit,
        "status": "PLANNED",
    }


def _plan_web_fetch(url: str, method: str = "GET", **_kwargs: Any) -> dict:
    return {
        "type": "web_fetch",
        "url": url,
        "method": method.upper(),
        "status": "PLANNED",
    }


def _plan_notify(title: str, body: str, channel: str = "local", **_kwargs: Any) -> dict:
    return {
        "type": "notify",
        "title": title,
        "body": body,
        "channel": channel,
        "status": "PLANNED",
    }


def _plan_ping(host: str, **_kwargs: Any) -> dict:
    return {
        "type": "ping",
        "host": host,
        "status": "PLANNED",
    }


_SKILL_PLANNERS = {
    "telegram_send": _plan_telegram_send,
    "telegram_read": _plan_telegram_read,
    "web_fetch": _plan_web_fetch,
    "notify": _plan_notify,
    "ping": _plan_ping,
}


# ── ClawAgent ────────────────────────────────────────────────────────


class ClawAgent:
    """CLAW skills agent — external connections, always gated.

    Usage:
        claw = ClawAgent()

        action = claw.plan("send 'hello' to telegram chat 42", state)
        # action.require_approval is always True
        # action.authority is always False

        gate_verdict = claw.gate(action)  # "PENDING" or "DENY"

        proposal = action.to_kernel_proposal()
        # hand to kernel governor for final routing
    """

    def plan(self, user_input: str, state: dict) -> ClawAction:
        """Parse user_input into a ClawAction proposal.

        Parsing is intentionally simple (keyword matching).
        Unknown or ambiguous inputs produce a `ping` action to localhost
        as a safe no-op placeholder that still goes through approval.
        """
        text = user_input.strip().lower()

        if "telegram" in text and ("read" in text or "fetch" in text or "get" in text):
            return ClawAction(
                skill="telegram_read",
                payload={"chat_id": "unknown", "limit": 10},
                rationale="Telegram read detected in user input",
            )

        if "telegram" in text and ("send" in text or "message" in text or "post" in text):
            return ClawAction(
                skill="telegram_send",
                payload={"chat_id": "unknown", "text": user_input},
                rationale="Telegram message detected in user input",
            )

        if any(kw in text for kw in ("http://", "https://", "fetch", "download", "curl", "url")):
            # Extract URL if present
            words = user_input.split()
            url = next((w for w in words if w.startswith("http")), "unknown://")
            return ClawAction(
                skill="web_fetch",
                payload={"url": url, "method": "GET"},
                rationale="Web fetch detected in user input",
            )

        if any(kw in text for kw in ("notify", "notification", "alert", "ping me")):
            return ClawAction(
                skill="notify",
                payload={"title": "HELEN", "body": user_input, "channel": "local"},
                rationale="Notification detected in user input",
            )

        if "ping" in text:
            words = user_input.split()
            host = words[-1] if len(words) > 1 else "localhost"
            return ClawAction(
                skill="ping",
                payload={"host": host},
                rationale="Ping detected in user input",
            )

        # Unknown — safe no-op ping to localhost (will go through PENDING → approval)
        return ClawAction(
            skill="ping",
            payload={"host": "localhost"},
            rationale=f"Unrecognized CLAW input; defaulted to safe ping: {user_input[:80]}",
        )

    def gate(self, action: ClawAction) -> str:
        """Apply the CLAW governor gate. Returns PENDING or DENY."""
        return claw_governor_gate(action)

    def plan_description(self, action: ClawAction) -> dict:
        """Return a human-readable plan dict for the action (no execution)."""
        planner = _SKILL_PLANNERS.get(action.skill)
        if planner is None:
            return {
                "type": action.skill,
                "status": "UNKNOWN_SKILL",
                "payload": action.payload,
            }
        try:
            return planner(**action.payload)
        except TypeError as exc:
            return {
                "type": action.skill,
                "status": "PLAN_ERROR",
                "error": str(exc),
                "payload": action.payload,
            }


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "ClawAgent",
    "ClawAction",
    "claw_governor_gate",
    "CLAW_KNOWN_SKILLS",
    "CLAW_WRITE_SKILLS",
]
