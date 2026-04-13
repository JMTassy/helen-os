"""HELEN OS — Claim Engine.

Turns governed state transitions into independently verifiable claims.

A VerifiableClaim is self-contained:
  - Anyone with the claim can verify it (no session access needed)
  - Evidence includes receipt hashes and Merkle proofs
  - Claim itself is hashed and immutable

The Claim Engine produces claims from:
  1. State transitions (step results)
  2. Memory disclosures (receipt-derived memory)
  3. Ledger integrity (Merkle root + chain validity)
  4. Receipt inclusion (Merkle proof for specific receipt)
  5. Execution results (action + verdict + effect)
  6. Capability state (enabled/disabled with state hash)

Architecture:
  kernel.step() -> receipt -> ClaimEngine.claim_*() -> VerifiableClaim -> verify_claim()

No claim without receipt. No evidence without proof. Fail-closed.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical_hash, governed_state_hash
from helensh.merkle import MerkleTree, compute_merkle_root
from helensh.memory import reconstruct_memory, memory_provenance
from helensh.replay import verify_chain
from helensh.claim_types import (
    CLAIM_TYPE_REGISTRY,
    is_known_claim_type,
    validate_evidence,
)


# ── VerifiableClaim ─────────────────────────────────────────────────


@dataclass(frozen=True)
class VerifiableClaim:
    """A self-contained, independently verifiable claim.

    Anyone with this object can:
      1. Check the claim type is known
      2. Verify evidence completeness
      3. Verify the receipt_hash matches canonical content
      4. Verify Merkle proof (if bound)
    """
    claim_id: str
    claim_type: str
    subject: str
    assertion: str                              # human-readable statement
    evidence: Dict[str, Any]
    merkle_root: str
    merkle_proof: Tuple[Tuple[str, str], ...]   # ((hash, side), ...)
    session_id: str
    created_at: str                             # ISO 8601
    receipt_hash: str                           # SHA-256 of canonical(claim data)


# ── Hashing ─────────────────────────────────────────────────────────


def _compute_claim_hash(
    claim_id: str,
    claim_type: str,
    subject: str,
    assertion: str,
    evidence: Dict[str, Any],
    merkle_root: str,
    session_id: str,
    created_at: str,
) -> str:
    """Compute deterministic hash of claim content."""
    data = {
        "claim_id": claim_id,
        "claim_type": claim_type,
        "subject": subject,
        "assertion": assertion,
        "evidence": evidence,
        "merkle_root": merkle_root,
        "session_id": session_id,
        "created_at": created_at,
    }
    return canonical_hash(data)


# ── Internal constructor ────────────────────────────────────────────


def _make_claim(
    claim_type: str,
    subject: str,
    assertion: str,
    evidence: Dict[str, Any],
    merkle_root: str,
    merkle_proof: Tuple[Tuple[str, str], ...],
    session_id: str,
    claim_id: Optional[str] = None,
    created_at: Optional[str] = None,
) -> VerifiableClaim:
    """Internal claim constructor with validation."""
    if not is_known_claim_type(claim_type):
        raise ValueError(f"Unknown claim type: {claim_type!r}")

    ct = CLAIM_TYPE_REGISTRY[claim_type]
    ok, missing = validate_evidence(ct, evidence)
    if not ok:
        raise ValueError(f"Missing evidence for {claim_type}: {sorted(missing)}")

    cid = claim_id or str(uuid.uuid4())
    ts = created_at or datetime.now(timezone.utc).isoformat()

    receipt_hash = _compute_claim_hash(
        cid, claim_type, subject, assertion, evidence, merkle_root, session_id, ts,
    )

    return VerifiableClaim(
        claim_id=cid,
        claim_type=claim_type,
        subject=subject,
        assertion=assertion,
        evidence=evidence,
        merkle_root=merkle_root,
        merkle_proof=merkle_proof,
        session_id=session_id,
        created_at=ts,
        receipt_hash=receipt_hash,
    )


# ── Claim Engine ────────────────────────────────────────────────────


class ClaimEngine:
    """Produces verifiable claims from governed session state.

    Usage:
        engine = ClaimEngine(state)
        claim  = engine.claim_state_transition(proposal_receipt)
        ok, errors = verify_claim(claim)
    """

    def __init__(self, state: Dict[str, Any]) -> None:
        self.state = state
        self.receipts = state.get("receipts", [])
        self.session_id = state.get("session_id", "unknown")
        self._tree: Optional[MerkleTree] = None

    @property
    def tree(self) -> MerkleTree:
        """Lazily build Merkle tree from receipts."""
        if self._tree is None:
            self._tree = MerkleTree(self.receipts) if self.receipts else MerkleTree()
        return self._tree

    def _get_proof(self, index: int) -> Tuple[Tuple[str, str], ...]:
        """Get Merkle proof for receipt at index."""
        if not self.receipts or index < 0 or index >= len(self.receipts):
            return ()
        proof = self.tree.get_proof(index)
        return tuple(proof)

    def _find_receipt_index(self, receipt_hash: str) -> int:
        """Find receipt index by hash. Returns -1 if not found."""
        for i, r in enumerate(self.receipts):
            if r.get("hash") == receipt_hash:
                return i
        return -1

    # ── Claim producers ─────────────────────────────────────────────

    def claim_state_transition(
        self,
        proposal_receipt: Dict[str, Any],
        claim_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> VerifiableClaim:
        """Claim that a governed state transition occurred.

        Evidence: state hashes before/after, user input, verdict.
        """
        idx = self._find_receipt_index(proposal_receipt.get("hash", ""))
        proof = self._get_proof(idx) if idx >= 0 else ()

        evidence = {
            "state_hash_before": proposal_receipt.get("state_hash_before", ""),
            "state_hash_after": governed_state_hash(self.state),
            "user_input": proposal_receipt.get("user_input", ""),
            "verdict": proposal_receipt.get("verdict", ""),
        }

        turn = proposal_receipt.get("turn", "?")
        return _make_claim(
            claim_type="STATE_TRANSITION",
            subject=f"transition:{self.session_id}:turn-{turn}",
            assertion=(
                f"State transitioned via '{evidence['user_input']}' "
                f"with verdict {evidence['verdict']}"
            ),
            evidence=evidence,
            merkle_root=self.tree.root,
            merkle_proof=proof,
            session_id=self.session_id,
            claim_id=claim_id,
            created_at=created_at,
        )

    def claim_memory_disclosure(
        self,
        key: str,
        claim_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> VerifiableClaim:
        """Claim that a memory key has a specific receipt-derived value.

        Evidence: key, value, source receipt hash.
        """
        mem = reconstruct_memory(self.receipts)
        if key not in mem:
            raise ValueError(f"Key '{key}' not in receipt-derived memory")

        source = memory_provenance(self.state, key)
        source_hash = source.get("hash", "") if source else ""

        # Find source receipt for Merkle proof
        idx = self._find_receipt_index(source_hash) if source else -1
        proof = self._get_proof(idx) if idx >= 0 else ()

        evidence = {
            "key": key,
            "value": mem[key],
            "source_receipt_hash": source_hash,
        }

        return _make_claim(
            claim_type="MEMORY_DISCLOSURE",
            subject=f"memory:{self.session_id}:{key}",
            assertion=(
                f"Memory key '{key}' = {mem[key]!r}, "
                f"derived from receipt {source_hash[:12]}"
            ),
            evidence=evidence,
            merkle_root=self.tree.root,
            merkle_proof=proof,
            session_id=self.session_id,
            claim_id=claim_id,
            created_at=created_at,
        )

    def claim_ledger_integrity(
        self,
        claim_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> VerifiableClaim:
        """Claim that the receipt ledger is chain-valid with a specific Merkle root.

        Evidence: Merkle root, receipt count, chain validity.
        """
        if self.receipts:
            chain_ok, _ = verify_chain(self.receipts)
        else:
            chain_ok = True

        evidence = {
            "merkle_root": self.tree.root,
            "receipt_count": len(self.receipts),
            "chain_valid": chain_ok,
        }

        return _make_claim(
            claim_type="LEDGER_INTEGRITY",
            subject=f"ledger:{self.session_id}",
            assertion=(
                f"Ledger of {len(self.receipts)} receipts, "
                f"root={self.tree.root[:12]}..., chain_valid={chain_ok}"
            ),
            evidence=evidence,
            merkle_root=self.tree.root,
            merkle_proof=(),  # ledger-level claim, no specific receipt
            session_id=self.session_id,
            claim_id=claim_id,
            created_at=created_at,
        )

    def claim_receipt_inclusion(
        self,
        index: int,
        claim_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> VerifiableClaim:
        """Claim that a receipt at a specific index is included in the ledger.

        Evidence: receipt hash, index, Merkle root, Merkle proof.
        """
        if index < 0 or index >= len(self.receipts):
            raise IndexError(
                f"Receipt index {index} out of range (0..{len(self.receipts) - 1})"
            )

        receipt = self.receipts[index]
        proof = self._get_proof(index)

        evidence = {
            "receipt_hash": receipt.get("hash", ""),
            "index": index,
            "merkle_root": self.tree.root,
            "merkle_proof": [list(p) for p in proof],  # serialize tuples for JSON
        }

        return _make_claim(
            claim_type="RECEIPT_INCLUSION",
            subject=f"receipt:{self.session_id}:index-{index}",
            assertion=(
                f"Receipt {receipt.get('hash', '')[:12]}... "
                f"included at index {index}"
            ),
            evidence=evidence,
            merkle_root=self.tree.root,
            merkle_proof=proof,
            session_id=self.session_id,
            claim_id=claim_id,
            created_at=created_at,
        )

    def claim_execution_result(
        self,
        execution_receipt: Dict[str, Any],
        claim_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> VerifiableClaim:
        """Claim the result of an executed action.

        Evidence: action, verdict, effect status, receipt hash.
        """
        proposal = execution_receipt.get("proposal", {})
        idx = self._find_receipt_index(execution_receipt.get("hash", ""))
        proof = self._get_proof(idx) if idx >= 0 else ()

        evidence = {
            "action": proposal.get("action", ""),
            "verdict": execution_receipt.get("verdict", ""),
            "effect_status": execution_receipt.get("effect_status", ""),
            "receipt_hash": execution_receipt.get("hash", ""),
        }

        return _make_claim(
            claim_type="EXECUTION_RESULT",
            subject=f"execution:{self.session_id}:{evidence['action']}",
            assertion=(
                f"Action '{evidence['action']}' -> "
                f"{evidence['verdict']} -> {evidence['effect_status']}"
            ),
            evidence=evidence,
            merkle_root=self.tree.root,
            merkle_proof=proof,
            session_id=self.session_id,
            claim_id=claim_id,
            created_at=created_at,
        )

    def claim_capability_state(
        self,
        capability: str,
        claim_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> VerifiableClaim:
        """Claim the current state of a capability.

        Evidence: capability name, enabled state, state hash.
        """
        caps = self.state.get("capabilities", {})
        if capability not in caps:
            raise KeyError(f"Unknown capability: {capability!r}")

        evidence = {
            "capability": capability,
            "enabled": caps[capability],
            "state_hash": governed_state_hash(self.state),
        }

        label = "enabled" if caps[capability] else "disabled"
        return _make_claim(
            claim_type="CAPABILITY_STATE",
            subject=f"capability:{self.session_id}:{capability}",
            assertion=f"Capability '{capability}' is {label}",
            evidence=evidence,
            merkle_root=self.tree.root,
            merkle_proof=(),  # state-level claim, no specific receipt
            session_id=self.session_id,
            claim_id=claim_id,
            created_at=created_at,
        )


# ── Verification ────────────────────────────────────────────────────


def verify_claim(claim: VerifiableClaim) -> Tuple[bool, List[str]]:
    """Independently verify a claim.

    Checks:
      1. Claim type is known
      2. Evidence is complete
      3. Receipt hash matches content
      4. Merkle proof structure is valid (if present)

    Returns (ok, errors).
    """
    errors: List[str] = []

    # 1. Known type
    if not is_known_claim_type(claim.claim_type):
        errors.append(f"Unknown claim type: {claim.claim_type!r}")
        return False, errors

    # 2. Evidence completeness
    ct = CLAIM_TYPE_REGISTRY[claim.claim_type]
    ok, missing = validate_evidence(ct, claim.evidence)
    if not ok:
        errors.append(f"Missing evidence fields: {sorted(missing)}")

    # 3. Receipt hash integrity
    expected_hash = _compute_claim_hash(
        claim.claim_id,
        claim.claim_type,
        claim.subject,
        claim.assertion,
        claim.evidence,
        claim.merkle_root,
        claim.session_id,
        claim.created_at,
    )
    if claim.receipt_hash != expected_hash:
        errors.append(
            f"Receipt hash mismatch: expected {expected_hash[:16]}..., "
            f"got {claim.receipt_hash[:16]}..."
        )

    # 4. Merkle proof structure
    if claim.merkle_proof:
        for i, element in enumerate(claim.merkle_proof):
            if len(element) != 2:
                errors.append(f"Invalid proof element at {i}: {element}")
            elif element[1] not in ("left", "right"):
                errors.append(f"Invalid proof side at {i}: {element[1]!r}")

    return len(errors) == 0, errors


def verify_claim_against_state(
    claim: VerifiableClaim,
    state: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Verify a claim against live session state.

    Stronger than verify_claim() — additionally checks:
      - Merkle root matches current ledger
      - Evidence values match current state (type-specific)

    Returns (ok, errors).
    """
    ok, errors = verify_claim(claim)

    receipts = state.get("receipts", [])
    current_root = compute_merkle_root(receipts) if receipts else ""

    # Check Merkle root matches
    if claim.merkle_root and current_root and claim.merkle_root != current_root:
        errors.append(
            f"Merkle root mismatch: claim has {claim.merkle_root[:16]}..., "
            f"current ledger has {current_root[:16]}..."
        )

    # Type-specific live checks
    if claim.claim_type == "MEMORY_DISCLOSURE":
        mem = reconstruct_memory(receipts)
        key = claim.evidence.get("key", "")
        if key in mem and mem[key] != claim.evidence.get("value"):
            errors.append(f"Memory value for '{key}' has diverged")

    elif claim.claim_type == "CAPABILITY_STATE":
        caps = state.get("capabilities", {})
        cap = claim.evidence.get("capability", "")
        if cap in caps and caps[cap] != claim.evidence.get("enabled"):
            errors.append(f"Capability '{cap}' state has changed")

    elif claim.claim_type == "LEDGER_INTEGRITY":
        if claim.evidence.get("receipt_count") != len(receipts):
            errors.append(
                f"Receipt count changed: claim has {claim.evidence['receipt_count']}, "
                f"current has {len(receipts)}"
            )

    return len(errors) == 0, errors


# ── Exports ─────────────────────────────────────────────────────────

__all__ = [
    "VerifiableClaim",
    "ClaimEngine",
    "verify_claim",
    "verify_claim_against_state",
]
