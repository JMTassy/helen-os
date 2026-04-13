"""HELEN OS — Street Template Schemas.

Typed schemas only. No logic.

These define the structural vocabulary for the city architecture:
    Egregor → Streets → Shops → Ledger/Gates

Universal role categories map domain-specific roles onto a common base:
    producer  — creates artifacts (coder, copywriter, scout)
    critic    — reviews artifacts (reviewer, brand critic, refutation agent)
    tester    — verifies artifacts (test writer, channel checker, citation checker)
    archivist — records and documents (release notes, content archivist)
    gate      — final pass/fail at street exit (validator, quality gate)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple


# ── Allowed Message Types ───────────────────────────────────────────

ALLOWED_MESSAGE_TYPES = frozenset({
    "TASK",
    "PROPOSAL",
    "PATCH",
    "REVIEW",
    "TEST_RESULT",
    "RISK_NOTE",
    "GATE_PACKET",
    "LEDGER_WRITE",
})

# ── Universal Role Categories ───────────────────────────────────────

UNIVERSAL_ROLES = frozenset({
    "producer",
    "critic",
    "tester",
    "archivist",
    "gate",
})


# ── Street Charter ──────────────────────────────────────────────────

@dataclass(frozen=True)
class StreetCharter:
    """Identity and constraints of a street.

    A street is a domain cluster — not a general intelligence.
    The charter defines what the street may do and what it must not.
    """
    street_id: str
    name: str
    mandate: str
    allowed_domains: Tuple[str, ...]
    forbidden_actions: Tuple[str, ...]
    output_types: Tuple[str, ...]
    success_metrics: Tuple[str, ...]
    risk_profile: str  # "low" | "medium" | "high"


# ── Shop Specification ──────────────────────────────────────────────

@dataclass(frozen=True)
class ShopSpec:
    """Specification for a single shop (agent worker).

    One mandate. One schema. One budget. One pass/fail metric.
    non_sovereign is structural — shops never claim authority.
    """
    shop_id: str
    role: str                      # must be in UNIVERSAL_ROLES
    mandate: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    model: str
    system_prompt: str
    temperature: float
    max_steps: int
    non_sovereign: bool = True     # structural invariant


# ── Message Envelope ────────────────────────────────────────────────

@dataclass(frozen=True)
class MessageEnvelope:
    """Typed message for inter-shop communication.

    Only ALLOWED_MESSAGE_TYPES can transit the bus.
    Free-form chatter is rejected.
    """
    envelope_id: str
    street_id: str
    task_id: str
    sender: str                    # shop_id or "street" or "gate"
    recipient: str                 # shop_id or "gate" or "ledger"
    message_type: str              # must be in ALLOWED_MESSAGE_TYPES
    payload: Dict[str, Any]
    receipts: Tuple[str, ...]      # hash chain references
    parents: Tuple[str, ...]       # parent envelope IDs


# ── Gate Result ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class StreetGateResult:
    """Result of the exit gate check.

    Verdict vocabulary: PASS | WARN | BLOCK.
    Not 'good'. Not 'promising'. Verdicts only.
    """
    verdict: str                   # PASS | WARN | BLOCK
    reasons: Tuple[str, ...]
    required_fixes: Tuple[str, ...]
    receipts: Tuple[str, ...]
    replay_hash: str


# ── Ledger Entry ────────────────────────────────────────────────────

@dataclass(frozen=True)
class StreetLedgerEntry:
    """Append-only ledger entry for a street.

    Every task completion produces at least one ledger entry.
    The city ledger aggregates street ledger entries.
    """
    entry_id: str
    street_id: str
    task_id: str
    phase: str
    artifact_refs: Tuple[str, ...]
    receipts: Tuple[str, ...]
    hash: str


# ── Exports ─────────────────────────────────────────────────────────

__all__ = [
    "ALLOWED_MESSAGE_TYPES",
    "UNIVERSAL_ROLES",
    "StreetCharter",
    "ShopSpec",
    "MessageEnvelope",
    "StreetGateResult",
    "StreetLedgerEntry",
]
