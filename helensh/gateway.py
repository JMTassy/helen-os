"""HELEN OS — Gateway Layer.

The ingress point for governed intent processing.

Gateway accepts intents (user input strings) and returns
GatewayResponses containing:
  - The kernel's step result (receipt)
  - A VerifiableClaim for the transition
  - Ledger state (Merkle root, receipt count)

Architecture:
  Intent -> Gateway.submit() -> kernel.step() -> ClaimEngine -> GatewayResponse

The Gateway is the publishing surface of the kernel.
It does not add governance — that's the kernel's job.
It packages governed results into verifiable, publishable form.

No HTTP here — this is the logical layer.
HTTP binding lives in helen-ui/airi/api/ or a future FastAPI wrapper.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from helensh.kernel import init_session, step
from helensh.state import governed_state_hash
from helensh.merkle import compute_merkle_root
from helensh.claims import ClaimEngine, VerifiableClaim, verify_claim


# ── Response Types ──────────────────────────────────────────────────


@dataclass(frozen=True)
class GatewayResponse:
    """Response from the Gateway for a single intent submission."""
    request_id: str
    status: str                         # OK | DENIED | PENDING | ERROR
    verdict: str                        # ALLOW | DENY | PENDING
    action: str                         # kernel action that was proposed
    claim: Optional[VerifiableClaim]    # verifiable claim for this transition
    receipt_hash: str                   # hash of proposal receipt
    state_hash: str                     # governed state hash after
    merkle_root: str                    # ledger Merkle root after
    receipt_count: int                  # total receipts after
    error: Optional[str]               # error message if status == ERROR


@dataclass(frozen=True)
class InspectResponse:
    """Response from Gateway.inspect() — claim details with verification."""
    claim: VerifiableClaim
    verified: bool
    errors: List[str]
    receipt_count: int
    merkle_root: str


# ── Status mapping ──────────────────────────────────────────────────

_VERDICT_TO_STATUS = {
    "ALLOW": "OK",
    "DENY": "DENIED",
    "PENDING": "PENDING",
}


# ── Gateway ─────────────────────────────────────────────────────────


class Gateway:
    """Governed intent ingress and claim publishing surface.

    Usage:
        gw = Gateway()                    # fresh session
        gw = Gateway(state=existing)      # from existing state
        resp = gw.submit("hello")         # process intent
        claim = resp.claim                # get verifiable claim
        ok, errors = gw.verify(claim)     # verify independently
    """

    def __init__(
        self,
        state: Optional[Dict[str, Any]] = None,
        session_id: str = "gateway",
    ) -> None:
        if state is not None:
            self.state = copy.deepcopy(state)
        else:
            self.state = init_session(session_id=session_id)
        self._claims: Dict[str, VerifiableClaim] = {}  # claim_id -> claim

    @property
    def session_id(self) -> str:
        return self.state.get("session_id", "unknown")

    @property
    def receipt_count(self) -> int:
        return len(self.state.get("receipts", []))

    @property
    def merkle_root(self) -> str:
        receipts = self.state.get("receipts", [])
        return compute_merkle_root(receipts) if receipts else ""

    @property
    def state_hash(self) -> str:
        return governed_state_hash(self.state)

    # ── Submit ──────────────────────────────────────────────────────

    def submit(self, intent: str) -> GatewayResponse:
        """Submit an intent for governed processing.

        Routes through kernel.step(), packages result as GatewayResponse
        with a VerifiableClaim for the state transition.
        """
        request_id = str(uuid.uuid4())

        try:
            self.state, proposal_receipt = step(self.state, intent)
        except Exception as e:
            return GatewayResponse(
                request_id=request_id,
                status="ERROR",
                verdict="",
                action="",
                claim=None,
                receipt_hash="",
                state_hash=governed_state_hash(self.state),
                merkle_root=self.merkle_root,
                receipt_count=self.receipt_count,
                error=str(e),
            )

        verdict = proposal_receipt.get("verdict", "")
        action = proposal_receipt.get("proposal", {}).get("action", "")
        status = _VERDICT_TO_STATUS.get(verdict, "ERROR")

        # Build claim for this transition
        engine = ClaimEngine(self.state)
        claim = engine.claim_state_transition(proposal_receipt)
        self._claims[claim.claim_id] = claim

        return GatewayResponse(
            request_id=request_id,
            status=status,
            verdict=verdict,
            action=action,
            claim=claim,
            receipt_hash=proposal_receipt.get("hash", ""),
            state_hash=self.state_hash,
            merkle_root=self.merkle_root,
            receipt_count=self.receipt_count,
            error=None,
        )

    def submit_batch(self, intents: List[str]) -> List[GatewayResponse]:
        """Submit multiple intents sequentially.

        Each intent sees the state from the previous step.
        Returns list of responses in order.
        """
        return [self.submit(intent) for intent in intents]

    # ── Inspect & verify ────────────────────────────────────────────

    def inspect(self, claim_id: str) -> Optional[InspectResponse]:
        """Inspect and verify a previously issued claim."""
        claim = self._claims.get(claim_id)
        if claim is None:
            return None

        ok, errors = verify_claim(claim)

        return InspectResponse(
            claim=claim,
            verified=ok,
            errors=errors,
            receipt_count=self.receipt_count,
            merkle_root=self.merkle_root,
        )

    def verify(self, claim: VerifiableClaim) -> Tuple[bool, List[str]]:
        """Verify any claim (not just ones from this gateway)."""
        return verify_claim(claim)

    # ── Specialized claim producers ─────────────────────────────────

    def claim_ledger_integrity(self) -> VerifiableClaim:
        """Produce a ledger integrity claim for the current state."""
        engine = ClaimEngine(self.state)
        claim = engine.claim_ledger_integrity()
        self._claims[claim.claim_id] = claim
        return claim

    def claim_memory(self, key: str) -> VerifiableClaim:
        """Produce a memory disclosure claim for a specific key."""
        engine = ClaimEngine(self.state)
        claim = engine.claim_memory_disclosure(key)
        self._claims[claim.claim_id] = claim
        return claim

    def claim_receipt(self, index: int) -> VerifiableClaim:
        """Produce a receipt inclusion claim for a specific index."""
        engine = ClaimEngine(self.state)
        claim = engine.claim_receipt_inclusion(index)
        self._claims[claim.claim_id] = claim
        return claim

    # ── Retrieval ───────────────────────────────────────────────────

    def list_claims(self) -> List[VerifiableClaim]:
        """List all claims issued by this gateway."""
        return list(self._claims.values())

    def get_claim(self, claim_id: str) -> Optional[VerifiableClaim]:
        """Get a specific claim by ID."""
        return self._claims.get(claim_id)


# ── Exports ─────────────────────────────────────────────────────────

__all__ = [
    "Gateway",
    "GatewayResponse",
    "InspectResponse",
]
