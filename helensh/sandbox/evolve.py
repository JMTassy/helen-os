"""HELEN OS — Receipted Self-Evolution Loop.

This clones what MiniMax M2.7 calls "model self-evolution" in terms
HELEN can prove and replay.

MiniMax's loop (opaque):
    propose → evaluate → update weights → repeat 100+ rounds

HELEN's loop (receipted):
    propose → HAL review → feed rejection rationale back to HER → repeat

The difference:
  - MiniMax: weights mutate silently. No receipt. No replay. No revert.
  - HELEN:   no weight mutation. Every iteration receipted. Full revert available.
             Improvement = eligible claims promoted to an evidence ledger.

Architecture:
    EvolveSession extends TempleSession with:
      - trajectory: per-iteration (proposal, review, feedback) tuples
      - best_claim: highest-confidence eligible claim across all iterations
      - promoted_claims: subset crossing both confidence AND score thresholds
      - failure_analysis: what HAL rejected and why (audit surface)

Usage:
    her = HerCoder()
    hal = HalReviewer()
    loop = EvolutionLoop(her, hal, iterations=100, approval_threshold=0.7)
    result = loop.run("optimise the governor chain")

    # Inspect what improved
    for turn in result.trajectory:
        print(turn.turn, turn.verdict, turn.confidence, turn.feedback_used)

    # Promote eligible claims
    for claim in result.promoted_claims:
        # claim is ready to be written to a knowledge ledger
        pass
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from helensh.agents.her_coder import HerCoder
from helensh.agents.hal_reviewer import HalReviewer
from helensh.sandbox.temple import (
    TempleSandbox,
    TempleSession,
    Claim,
    GENESIS_HASH,
    _make_proposal_receipt,
    _make_review_receipt,
)
from helensh.state import canonical_hash

# ── Constants ─────────────────────────────────────────────────────────

DEFAULT_EVOLVE_ITERATIONS = 20
DEFAULT_APPROVAL_THRESHOLD = 0.7
DEFAULT_PROMOTION_THRESHOLD = 0.85  # higher bar for promoted claims

RECEIPT_FEEDBACK = "EVOLVE_FEEDBACK"


# ── Data structures ───────────────────────────────────────────────────


@dataclass(frozen=True)
class EvolveTurn:
    """One iteration of the evolution loop.

    Fields:
      turn         — iteration index (0-based)
      proposal     — HER's proposal at this turn
      review       — HAL's review at this turn
      verdict      — HAL's verdict
      confidence   — HAL's confidence score
      feedback     — rejection rationale fed back to HER (None on APPROVE)
      feedback_used — True if HER received rejection context from previous turn
      receipt_hash  — hash of the review receipt
    """
    turn: int
    proposal: dict
    review: dict
    verdict: str
    confidence: float
    feedback: Optional[str]
    feedback_used: bool
    receipt_hash: str


@dataclass(frozen=True)
class EvolveSession:
    """Result of one complete evolution run.

    Extends TempleSession concept with feedback trajectory.

    Fields:
      task               — original task
      iterations_run     — actual iterations completed
      threshold          — approval threshold used
      promotion_threshold — threshold for promoted claims
      trajectory         — per-turn EvolveTurn records
      all_claims         — all Claim objects from the run
      eligible_claims    — claims crossing approval threshold
      promoted_claims    — claims crossing promotion threshold
      best_claim         — highest-confidence eligible claim (None if none eligible)
      failure_analysis   — list of (turn, rationale) for DENY/REJECT turns
      receipt_chain      — full audit chain
      session_hash       — hash of the full session
    """
    task: str
    iterations_run: int
    threshold: float
    promotion_threshold: float
    trajectory: Tuple[EvolveTurn, ...]
    all_claims: Tuple[Claim, ...]
    eligible_claims: Tuple[Claim, ...]
    promoted_claims: Tuple[Claim, ...]
    best_claim: Optional[Claim]
    failure_analysis: Tuple[Tuple[int, str], ...]  # (turn, rationale)
    receipt_chain: Tuple[dict, ...]
    session_hash: str


# ── Feedback receipt ──────────────────────────────────────────────────


def _make_feedback_receipt(
    turn: int,
    task: str,
    rationale: str,
    previous_hash: str,
) -> dict:
    """Receipt documenting that feedback was sent from HAL back to HER."""
    body = {
        "type": RECEIPT_FEEDBACK,
        "turn": turn,
        "task": task,
        "feedback_rationale": rationale[:500],  # cap
        "authority": False,
        "previous_hash": previous_hash,
    }
    receipt_hash = canonical_hash(body)
    return {**body, "receipt_hash": receipt_hash}


# ── EvolutionLoop ─────────────────────────────────────────────────────


class EvolutionLoop:
    """Receipted self-evolution loop — HELEN's answer to model self-improvement.

    Each iteration:
      1. HER proposes (with optional rejection feedback from previous HAL)
      2. HAL reviews
      3. If REJECT/REQUEST_CHANGES: extract rationale, feed to next HER call
      4. If APPROVE + confidence ≥ threshold: record eligible claim
      5. Emit 3 receipts: TEMPLE_PROPOSAL, TEMPLE_REVIEW, [EVOLVE_FEEDBACK if rejected]

    Termination:
      - After `iterations` cycles
      - OR after `early_stop_on_n_consecutive_approvals` consecutive approvals
        (default None — run full loop)

    Base state is NEVER mutated.
    """

    def __init__(
        self,
        her: HerCoder,
        hal: HalReviewer,
        iterations: int = DEFAULT_EVOLVE_ITERATIONS,
        approval_threshold: float = DEFAULT_APPROVAL_THRESHOLD,
        promotion_threshold: float = DEFAULT_PROMOTION_THRESHOLD,
        early_stop_on_n_consecutive_approvals: Optional[int] = None,
    ) -> None:
        self.her = her
        self.hal = hal
        self.iterations = max(1, iterations)
        self.approval_threshold = max(0.0, min(1.0, approval_threshold))
        self.promotion_threshold = max(0.0, min(1.0, promotion_threshold))
        self.early_stop = early_stop_on_n_consecutive_approvals

    def run(
        self,
        task: str,
        state: Optional[dict] = None,
    ) -> EvolveSession:
        """Run the evolution loop. Returns an EvolveSession with full audit trail."""
        if state is None:
            state = _minimal_state()

        sandbox_state = copy.deepcopy(state)  # isolation: base_state never touched

        receipt_chain: List[dict] = []
        evolve_turns: List[EvolveTurn] = []
        all_claims: List[Claim] = []
        failure_analysis: List[Tuple[int, str]] = []

        previous_hash = GENESIS_HASH
        previous_feedback: Optional[str] = None
        consecutive_approvals = 0

        for turn in range(self.iterations):
            feedback_used = previous_feedback is not None

            # ── Build HER prompt (with rejection feedback if available) ──
            prompt = _build_evolve_prompt(task, turn, self.iterations, previous_feedback)

            # ── HER: propose ──
            proposal = self.her.propose(sandbox_state, prompt)
            proposal["authority"] = False  # structural enforcement

            # ── Proposal receipt ──
            p_receipt = _make_proposal_receipt(turn, task, proposal, previous_hash)
            previous_hash = p_receipt["receipt_hash"]
            receipt_chain.append(p_receipt)

            # ── HAL: review ──
            review = self.hal.review(proposal, sandbox_state)
            review["authority"] = False

            # ── Review receipt ──
            r_receipt = _make_review_receipt(turn, task, proposal, review, previous_hash)
            previous_hash = r_receipt["receipt_hash"]
            receipt_chain.append(r_receipt)

            verdict = review.get("verdict", "REJECT")
            confidence = float(review.get("confidence", 0.0))
            eligible = (verdict == "APPROVE" and confidence >= self.approval_threshold)

            # ── Extract description for claim ──
            description = (
                proposal.get("payload", {}).get("description", "")
                or proposal.get("payload", {}).get("message", "")
                or str(proposal.get("payload", ""))
            )

            claim = Claim(
                id=turn,
                turn=turn,
                text=description[:500],
                action=proposal.get("action", "unknown"),
                confidence=confidence,
                verdict=verdict,
                eligible=eligible,
                receipt_hash=r_receipt["receipt_hash"],
            )
            all_claims.append(claim)

            # ── Feedback extraction ──
            if verdict in ("REJECT", "REQUEST_CHANGES"):
                rationale = review.get("rationale", "")
                issues = review.get("issues", [])
                feedback = _compose_feedback(rationale, issues)
                failure_analysis.append((turn, feedback))

                # Feedback receipt (documents the loop closure)
                f_receipt = _make_feedback_receipt(turn, task, feedback, previous_hash)
                previous_hash = f_receipt["receipt_hash"]
                receipt_chain.append(f_receipt)

                previous_feedback = feedback
                consecutive_approvals = 0
            else:
                previous_feedback = None
                if eligible:
                    consecutive_approvals += 1
                else:
                    consecutive_approvals = 0

            evolve_turns.append(EvolveTurn(
                turn=turn,
                proposal=proposal,
                review=review,
                verdict=verdict,
                confidence=confidence,
                feedback=previous_feedback,  # what will be sent NEXT (None if approved)
                feedback_used=feedback_used,
                receipt_hash=r_receipt["receipt_hash"],
            ))

            # Early stop
            if self.early_stop and consecutive_approvals >= self.early_stop:
                break

        # ── Aggregate results ──
        eligible_claims = tuple(c for c in all_claims if c.eligible)
        promoted_claims = tuple(
            c for c in all_claims
            if c.eligible and c.confidence >= self.promotion_threshold
        )

        best_claim: Optional[Claim] = None
        if eligible_claims:
            best_claim = max(eligible_claims, key=lambda c: c.confidence)

        # Session hash
        session_hash = canonical_hash({
            "task": task,
            "iterations_run": len(evolve_turns),
            "receipt_hashes": [r["receipt_hash"] for r in receipt_chain],
        })

        return EvolveSession(
            task=task,
            iterations_run=len(evolve_turns),
            threshold=self.approval_threshold,
            promotion_threshold=self.promotion_threshold,
            trajectory=tuple(evolve_turns),
            all_claims=tuple(all_claims),
            eligible_claims=eligible_claims,
            promoted_claims=promoted_claims,
            best_claim=best_claim,
            failure_analysis=tuple(failure_analysis),
            receipt_chain=tuple(receipt_chain),
            session_hash=session_hash,
        )

    def verify_session(self, session: EvolveSession) -> bool:
        """Verify the receipt chain integrity of an EvolveSession."""
        chain = list(session.receipt_chain)
        expected_prev = GENESIS_HASH

        for receipt in chain:
            if receipt.get("previous_hash") != expected_prev:
                return False
            body = {k: v for k, v in receipt.items() if k != "receipt_hash"}
            if canonical_hash(body) != receipt.get("receipt_hash"):
                return False
            expected_prev = receipt["receipt_hash"]

        return True


# ── Helpers ───────────────────────────────────────────────────────────


def _minimal_state() -> dict:
    return {
        "session_id": "evolve-sandbox",
        "turn": 0,
        "env": {},
        "capabilities": {},
        "working_memory": {},
        "receipts": [],
    }


def _build_evolve_prompt(
    task: str,
    turn: int,
    total: int,
    previous_feedback: Optional[str],
) -> str:
    """Build the HER prompt for this iteration, incorporating HAL's feedback."""
    base = f"[Evolution turn {turn + 1}/{total}]\n\nTask: {task}"
    if previous_feedback:
        base += (
            f"\n\nHAL review feedback from previous attempt:\n"
            f"{previous_feedback}\n\n"
            f"Revise your proposal to address the above issues."
        )
    else:
        if turn == 0:
            base += "\n\nPropose your best initial approach."
        else:
            base += "\n\nPrevious attempt was approved. Propose a refinement or alternative."
    return base


def _compose_feedback(rationale: str, issues: list) -> str:
    """Compose concise feedback from HAL's rationale + issues list."""
    parts = []
    if rationale:
        parts.append(f"Rationale: {rationale}")
    if issues:
        parts.append("Issues: " + "; ".join(str(i) for i in issues[:5]))
    return " | ".join(parts) if parts else "Review rejected without rationale."


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "EvolutionLoop",
    "EvolveSession",
    "EvolveTurn",
    "DEFAULT_EVOLVE_ITERATIONS",
    "DEFAULT_PROMOTION_THRESHOLD",
]
