"""HELEN OS — Witness Layer.

Observes and receipts sandbox sessions into the main kernel's governance chain.

Witnessing is the bridge between:
  - The TEMPLE sandbox (isolated, experimental, no base state mutation)
  - The main kernel (governed, receipted, durable)

A witnessed TEMPLE session:
  1. Runs in isolation (sandbox receipts, sandbox claims)
  2. Summary is receipted into main kernel via governed step (#witness)
  3. Eligible claims are recorded but NOT promoted to VerifiableClaims
  4. The witness record is immutable and auditable

Architecture:
  TEMPLE sandbox -> TempleSession -> witness_temple() -> kernel.step() -> receipted

No claim without receipt. No witness without governance. No promotion without approval.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from helensh.kernel import step
from helensh.state import canonical, canonical_hash
from helensh.sandbox.temple import TempleSession, TempleSandbox


# ── WitnessRecord ───────────────────────────────────────────────────


@dataclass(frozen=True)
class WitnessRecord:
    """Immutable summary of a witnessed TEMPLE session.

    Fields:
      session_hash         — hash of the original TEMPLE session
      task                 — the brainstorming task
      iterations           — number of HER->HAL cycles
      total_claims         — total claims produced
      eligible_count       — claims that passed threshold
      approved_summaries   — text of eligible claims
      receipt_chain_length — number of TEMPLE receipts
      witness_hash         — canonical hash of this record
    """
    session_hash: str
    task: str
    iterations: int
    total_claims: int
    eligible_count: int
    approved_summaries: Tuple[str, ...]
    receipt_chain_length: int
    witness_hash: str


# ── Record construction ─────────────────────────────────────────────


def build_witness_record(session: TempleSession) -> WitnessRecord:
    """Build a WitnessRecord from a completed TEMPLE session.

    The record is a deterministic summary — same session always produces
    the same witness_hash.
    """
    approved = tuple(c.text for c in session.eligible_claims)
    record_data = {
        "session_hash": session.session_hash,
        "task": session.task,
        "iterations": session.iterations,
        "total_claims": len(session.claims),
        "eligible_count": len(session.eligible_claims),
        "approved_summaries": list(approved),
        "receipt_chain_length": len(session.receipt_chain),
    }
    return WitnessRecord(
        session_hash=session.session_hash,
        task=session.task,
        iterations=session.iterations,
        total_claims=len(session.claims),
        eligible_count=len(session.eligible_claims),
        approved_summaries=approved,
        receipt_chain_length=len(session.receipt_chain),
        witness_hash=canonical_hash(record_data),
    )


# ── Kernel input formatting ─────────────────────────────────────────


def _format_witness_input(record: WitnessRecord) -> str:
    """Format a WitnessRecord as kernel input for the #witness command."""
    content = canonical({
        "type": "TEMPLE_WITNESS",
        "witness_hash": record.witness_hash,
        "session_hash": record.session_hash,
        "task": record.task,
        "iterations": record.iterations,
        "total_claims": record.total_claims,
        "eligible_count": record.eligible_count,
        "approved_summaries": list(record.approved_summaries),
    })
    return f"#witness {content}"


# ── Witness functions ───────────────────────────────────────────────


def witness_temple(
    state: Dict[str, Any],
    temple_session: TempleSession,
) -> Tuple[Dict[str, Any], WitnessRecord, Dict[str, Any]]:
    """Witness a TEMPLE session by recording it through the governed kernel.

    Flow:
      1. Build WitnessRecord from TempleSession
      2. Format as #witness <canonical_json>
      3. Step through kernel (C -> G -> E)
      4. Return (new_state, witness_record, proposal_receipt)

    The witness is:
      - Governed (goes through governor gates)
      - Receipted (produces proposal + execution receipts)
      - Stored in working_memory as witness_{turn}
      - NOT promoted to VerifiableClaim (that's the Claim Engine's domain)
    """
    record = build_witness_record(temple_session)
    user_input = _format_witness_input(record)
    new_state, receipt = step(state, user_input)
    return new_state, record, receipt


def witness_and_run(
    state: Dict[str, Any],
    her: Any,
    hal: Any,
    task: str,
    iterations: int = 5,
    approval_threshold: float = 0.7,
) -> Tuple[Dict[str, Any], TempleSession, WitnessRecord, Dict[str, Any]]:
    """Run a TEMPLE brainstorm and witness it in one call.

    Convenience function that:
      1. Creates a TempleSandbox
      2. Runs brainstorm() (isolated — base state never mutated)
      3. Witnesses the result through the kernel

    Returns (new_state, temple_session, witness_record, proposal_receipt).
    """
    temple = TempleSandbox(her, hal, approval_threshold=approval_threshold)
    session = temple.brainstorm(task, state=state, iterations=iterations)
    new_state, record, receipt = witness_temple(state, session)
    return new_state, session, record, receipt


# ── Verification ────────────────────────────────────────────────────


def verify_witness(
    state: Dict[str, Any],
    record: WitnessRecord,
) -> Tuple[bool, List[str]]:
    """Verify that a witness record is stored in governed memory.

    Checks:
      1. A witness_{turn} key exists in working_memory
      2. The stored content contains the witness_hash
      3. A witness PROPOSAL receipt exists in the receipt chain

    Returns (ok, errors).
    """
    errors: List[str] = []
    wm = state.get("working_memory", {})

    # 1. Find the witness key in working_memory
    found_in_memory = False
    for key, value in wm.items():
        if key.startswith("witness_") and record.witness_hash in str(value):
            found_in_memory = True
            break

    if not found_in_memory:
        errors.append(
            f"Witness hash {record.witness_hash[:16]}... "
            f"not found in working_memory"
        )

    # 2. Find witness receipt in chain
    receipts = state.get("receipts", [])
    found_in_receipts = False
    for r in receipts:
        if r.get("type") == "PROPOSAL":
            proposal = r.get("proposal", {})
            if proposal.get("action") == "witness":
                content = proposal.get("payload", {}).get("content", "")
                if record.witness_hash in content:
                    found_in_receipts = True
                    break

    if not found_in_receipts:
        errors.append(
            f"No witness PROPOSAL receipt found for hash "
            f"{record.witness_hash[:16]}..."
        )

    return len(errors) == 0, errors


# ── Exports ─────────────────────────────────────────────────────────

__all__ = [
    "WitnessRecord",
    "build_witness_record",
    "witness_temple",
    "witness_and_run",
    "verify_witness",
]
