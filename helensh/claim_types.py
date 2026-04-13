"""HELEN OS — Closed Claim Vocabulary.

Every claim the system can publish must be of a known type.
Unknown claim types are rejected — fail-closed, like the governor.

Each ClaimType defines:
  - name: canonical string identifier
  - required_evidence: fields that must be present in evidence dict
  - description: human-readable purpose

This is the ontology of verifiable assertions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple


# ── Claim Type Definition ───────────────────────────────────────────


@dataclass(frozen=True)
class ClaimType:
    """Definition of a verifiable claim type."""
    name: str
    required_evidence: FrozenSet[str]
    description: str


# ── Known Claim Types ───────────────────────────────────────────────

STATE_TRANSITION = ClaimType(
    name="STATE_TRANSITION",
    required_evidence=frozenset({
        "state_hash_before", "state_hash_after", "user_input", "verdict",
    }),
    description="Asserts that a governed state transition occurred via the kernel.",
)

MEMORY_DISCLOSURE = ClaimType(
    name="MEMORY_DISCLOSURE",
    required_evidence=frozenset({
        "key", "value", "source_receipt_hash",
    }),
    description="Asserts that a memory key has a specific value, derived from a receipt.",
)

LEDGER_INTEGRITY = ClaimType(
    name="LEDGER_INTEGRITY",
    required_evidence=frozenset({
        "merkle_root", "receipt_count", "chain_valid",
    }),
    description="Asserts that the receipt ledger has a specific Merkle root and is chain-valid.",
)

RECEIPT_INCLUSION = ClaimType(
    name="RECEIPT_INCLUSION",
    required_evidence=frozenset({
        "receipt_hash", "index", "merkle_root", "merkle_proof",
    }),
    description="Asserts that a specific receipt is included in the ledger at a given index.",
)

EXECUTION_RESULT = ClaimType(
    name="EXECUTION_RESULT",
    required_evidence=frozenset({
        "action", "verdict", "effect_status", "receipt_hash",
    }),
    description="Asserts that an action was executed with a specific verdict and effect.",
)

CAPABILITY_STATE = ClaimType(
    name="CAPABILITY_STATE",
    required_evidence=frozenset({
        "capability", "enabled", "state_hash",
    }),
    description="Asserts that a capability is in a specific state.",
)


# ── Registry ────────────────────────────────────────────────────────

CLAIM_TYPE_REGISTRY: Dict[str, ClaimType] = {
    ct.name: ct for ct in [
        STATE_TRANSITION,
        MEMORY_DISCLOSURE,
        LEDGER_INTEGRITY,
        RECEIPT_INCLUSION,
        EXECUTION_RESULT,
        CAPABILITY_STATE,
    ]
}

KNOWN_CLAIM_TYPES: FrozenSet[str] = frozenset(CLAIM_TYPE_REGISTRY.keys())


# ── Lookup functions ────────────────────────────────────────────────


def is_known_claim_type(name: str) -> bool:
    """Check if a claim type name is in the registry."""
    return name in KNOWN_CLAIM_TYPES


def get_claim_type(name: str) -> ClaimType:
    """Get a ClaimType by name. Raises KeyError if unknown."""
    if name not in CLAIM_TYPE_REGISTRY:
        raise KeyError(
            f"Unknown claim type: {name!r}. Known: {sorted(KNOWN_CLAIM_TYPES)}"
        )
    return CLAIM_TYPE_REGISTRY[name]


def validate_evidence(
    claim_type: ClaimType, evidence: Dict,
) -> Tuple[bool, FrozenSet[str]]:
    """Check that evidence dict has all required fields.

    Returns (ok, missing_fields).
    """
    missing = claim_type.required_evidence - frozenset(evidence.keys())
    return len(missing) == 0, missing


# ── Exports ─────────────────────────────────────────────────────────

__all__ = [
    "ClaimType",
    "STATE_TRANSITION",
    "MEMORY_DISCLOSURE",
    "LEDGER_INTEGRITY",
    "RECEIPT_INCLUSION",
    "EXECUTION_RESULT",
    "CAPABILITY_STATE",
    "CLAIM_TYPE_REGISTRY",
    "KNOWN_CLAIM_TYPES",
    "is_known_claim_type",
    "get_claim_type",
    "validate_evidence",
]
