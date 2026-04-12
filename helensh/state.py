"""State utilities: canonical serialization, hashing, footprint extraction."""
import hashlib
import json
from typing import Any, Dict, Tuple


def canonical(data: Any) -> str:
    """Canonical JSON encoding. Deterministic key order, minimal whitespace, UTF-8."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(data: Any) -> str:
    """SHA-256 of canonical JSON encoding."""
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


def governed_state_hash(state: dict) -> str:
    """Hash of the governed state surface (everything except receipts and history metadata).

    This captures the effect-relevant state: env, capabilities, working_memory, turn.
    Receipts are excluded because they are the audit trail, not the governed surface.
    """
    payload = {
        "session_id": state["session_id"],
        "turn": state["turn"],
        "env": state["env"],
        "capabilities": state["capabilities"],
        "working_memory": state["working_memory"],
    }
    return canonical_hash(payload)


def effect_footprint(state: dict) -> dict:
    """The mutable effect surface. If verdict != ALLOW, this must not change."""
    return {
        "env": state["env"],
        "capabilities": state["capabilities"],
    }
