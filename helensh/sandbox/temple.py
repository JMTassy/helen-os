"""HELEN OS — TEMPLE SANDBOX.

Infinite sandboxed HER × HAL brainstorming loop.

Architecture:
  - HER proposes N times from the same task (creative, varied)
  - HAL reviews each proposal (deterministic, fail-closed)
  - APPROVE + confidence ≥ threshold → Claim is "eligible"
  - base_state is NEVER mutated — sandbox is fully isolated
  - receipt_chain grows: 2 receipts per iteration (proposal + review)
  - no real execution; all proposals are PLANNED only

Design constraints:
  - TempleSession is immutable after brainstorm() returns
  - Claim.eligible is structural (computed from verdict + confidence)
  - receipt_chain length == n_iterations × 2
  - authority: False enforced throughout
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from helensh.agents.her_coder import HerCoder
from helensh.agents.hal_reviewer import HalReviewer
from helensh.state import canonical, canonical_hash

# ── Constants ─────────────────────────────────────────────────────────

GENESIS_HASH = "temple_genesis"
DEFAULT_ITERATIONS = 5
DEFAULT_APPROVAL_THRESHOLD = 0.7

RECEIPT_PROPOSAL = "TEMPLE_PROPOSAL"
RECEIPT_REVIEW = "TEMPLE_REVIEW"


# ── Data structures ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Claim:
    """A single brainstormed claim from the TEMPLE SANDBOX.

    Immutable after creation.

    Fields:
      id           — claim index (0-based)
      turn         — iteration number (0-based)
      text         — HER's proposal description
      action       — HER's proposed action type
      confidence   — HAL's review confidence [0.0, 1.0]
      verdict      — HAL's verdict: APPROVE | REJECT | REQUEST_CHANGES
      eligible     — True iff verdict==APPROVE and confidence≥threshold
      receipt_hash — hash of the review receipt for this claim
    """
    id: int
    turn: int
    text: str
    action: str
    confidence: float
    verdict: str
    eligible: bool
    receipt_hash: str


@dataclass(frozen=True)
class TempleSession:
    """Result of one TEMPLE SANDBOX brainstorming run.

    Immutable after creation.

    Fields:
      task            — the original task string
      iterations      — number of HER→HAL cycles run
      threshold       — approval confidence threshold used
      claims          — all Claims from this session
      eligible_claims — subset of claims where eligible==True
      receipt_chain   — full audit chain; len == iterations × 2
      session_hash    — hash of the full session for external audit
    """
    task: str
    iterations: int
    threshold: float
    claims: Tuple[Claim, ...]
    eligible_claims: Tuple[Claim, ...]
    receipt_chain: Tuple[dict, ...]
    session_hash: str


# ── Receipt construction ──────────────────────────────────────────────


def _make_proposal_receipt(
    turn: int,
    task: str,
    proposal: dict,
    previous_hash: str,
) -> dict:
    """Create a TEMPLE_PROPOSAL receipt (no real execution)."""
    body = {
        "type": RECEIPT_PROPOSAL,
        "turn": turn,
        "task": task,
        "proposal": proposal,
        "authority": False,
        "previous_hash": previous_hash,
    }
    receipt_hash = canonical_hash(body)
    return {**body, "receipt_hash": receipt_hash}


def _make_review_receipt(
    turn: int,
    task: str,
    proposal: dict,
    review: dict,
    previous_hash: str,
) -> dict:
    """Create a TEMPLE_REVIEW receipt."""
    body = {
        "type": RECEIPT_REVIEW,
        "turn": turn,
        "task": task,
        "proposal_action": proposal.get("action", "unknown"),
        "verdict": review.get("verdict", "REJECT"),
        "confidence": review.get("confidence", 0.0),
        "rationale": review.get("rationale", ""),
        "authority": False,
        "previous_hash": previous_hash,
    }
    receipt_hash = canonical_hash(body)
    return {**body, "receipt_hash": receipt_hash}


# ── Session hash ──────────────────────────────────────────────────────


def _compute_session_hash(task: str, iterations: int, receipt_chain: List[dict]) -> str:
    """Compute a canonical hash of the entire session for external audit."""
    payload = {
        "task": task,
        "iterations": iterations,
        "receipt_hashes": [r["receipt_hash"] for r in receipt_chain],
    }
    return canonical_hash(payload)


# ── TempleSandbox ─────────────────────────────────────────────────────


class TempleSandbox:
    """HER × HAL infinite brainstorming loop.

    The sandbox is fully isolated — the base_state is never mutated.
    Each iteration: HER proposes, HAL reviews, a Claim is created,
    two receipts are appended to the chain.

    Usage:
        her = HerCoder()
        hal = HalReviewer()
        temple = TempleSandbox(her, hal, approval_threshold=0.7)
        session = temple.brainstorm("design a new governor gate", iterations=5)

        for claim in session.eligible_claims:
            print(claim.text, claim.confidence)
    """

    def __init__(
        self,
        her: HerCoder,
        hal: HalReviewer,
        approval_threshold: float = DEFAULT_APPROVAL_THRESHOLD,
    ) -> None:
        self.her = her
        self.hal = hal
        self.approval_threshold = max(0.0, min(1.0, approval_threshold))

    def brainstorm(
        self,
        task: str,
        state: Optional[dict] = None,
        iterations: int = DEFAULT_ITERATIONS,
    ) -> TempleSession:
        """Run N iterations of HER→HAL brainstorming.

        Args:
          task:       The brainstorming prompt/task.
          state:      Optional read-only context state. NEVER mutated.
          iterations: Number of HER→HAL cycles to run (default 5).

        Returns a TempleSession with all claims and receipts.
        """
        if state is None:
            state = _minimal_sandbox_state()

        # Deep copy ensures base_state isolation
        sandbox_state = copy.deepcopy(state)

        receipt_chain: List[dict] = []
        claims: List[Claim] = []
        previous_hash = GENESIS_HASH

        for turn in range(iterations):
            # Build iteration-specific prompt (adds turn context for variation)
            iter_prompt = _iteration_prompt(task, turn, iterations)

            # ── HER: propose ──
            proposal = self.her.propose(sandbox_state, iter_prompt)

            # Safety: enforce authority=False regardless of what model returned
            proposal["authority"] = False

            # Create proposal receipt
            p_receipt = _make_proposal_receipt(turn, task, proposal, previous_hash)
            previous_hash = p_receipt["receipt_hash"]
            receipt_chain.append(p_receipt)

            # ── HAL: review ──
            review = self.hal.review(proposal, sandbox_state)

            # Safety: enforce authority=False
            review["authority"] = False

            # Create review receipt
            r_receipt = _make_review_receipt(turn, task, proposal, review, previous_hash)
            previous_hash = r_receipt["receipt_hash"]
            receipt_chain.append(r_receipt)

            # ── Evaluate eligibility ──
            verdict = review.get("verdict", "REJECT")
            confidence = float(review.get("confidence", 0.0))
            eligible = (verdict == "APPROVE" and confidence >= self.approval_threshold)

            description = (
                proposal.get("payload", {}).get("description", "")
                or proposal.get("payload", {}).get("message", "")
                or str(proposal.get("payload", ""))
            )

            claim = Claim(
                id=turn,
                turn=turn,
                text=description[:500],  # cap at 500 chars
                action=proposal.get("action", "unknown"),
                confidence=confidence,
                verdict=verdict,
                eligible=eligible,
                receipt_hash=r_receipt["receipt_hash"],
            )
            claims.append(claim)

            # Note: sandbox_state is NOT mutated — isolation guaranteed
            # (HER and HAL only read state; no apply_receipt called here)

        eligible_claims = tuple(c for c in claims if c.eligible)
        claims_tuple = tuple(claims)
        receipts_tuple = tuple(receipt_chain)

        session_hash = _compute_session_hash(task, iterations, receipt_chain)

        return TempleSession(
            task=task,
            iterations=iterations,
            threshold=self.approval_threshold,
            claims=claims_tuple,
            eligible_claims=eligible_claims,
            receipt_chain=receipts_tuple,
            session_hash=session_hash,
        )

    def verify_session(self, session: TempleSession) -> bool:
        """Verify a TempleSession's receipt chain integrity.

        Returns True if:
          - receipt_chain length == iterations × 2
          - previous_hash links are unbroken from GENESIS_HASH
          - each receipt_hash matches its content
        """
        chain = list(session.receipt_chain)

        if len(chain) != session.iterations * 2:
            return False

        expected_prev = GENESIS_HASH
        for receipt in chain:
            if receipt.get("previous_hash") != expected_prev:
                return False
            # Verify hash
            body = {k: v for k, v in receipt.items() if k != "receipt_hash"}
            computed = canonical_hash(body)
            if computed != receipt.get("receipt_hash"):
                return False
            expected_prev = receipt["receipt_hash"]

        return True


# ── Helpers ───────────────────────────────────────────────────────────


def _minimal_sandbox_state() -> dict:
    """Minimal read-only sandbox state when no real state provided."""
    return {
        "session_id": "temple-sandbox",
        "turn": 0,
        "env": {},
        "capabilities": {},
        "working_memory": {},
        "receipts": [],
    }


def _iteration_prompt(task: str, turn: int, total: int) -> str:
    """Build a varied prompt per iteration to encourage creative proposals."""
    angles = [
        "Propose a straightforward, minimal approach.",
        "Propose an alternative or unconventional approach.",
        "Identify the biggest risk and propose a mitigation-focused approach.",
        "Propose the most modular, testable approach.",
        "Propose the approach with the smallest code surface area.",
        "Propose an approach that prioritizes explainability.",
        "Propose an approach that maximizes determinism.",
    ]
    angle = angles[turn % len(angles)]
    return f"[Iteration {turn + 1}/{total}] Task: {task}\n\nApproach angle: {angle}"


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "TempleSandbox",
    "TempleSession",
    "Claim",
    "GENESIS_HASH",
    "DEFAULT_ITERATIONS",
    "DEFAULT_APPROVAL_THRESHOLD",
]
