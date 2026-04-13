"""HELEN OS — ACT Router.

Routes governed tasks to EGREGOR streets.

    HELEN → ACT → EGREGOR → Street → Shops → Gate → Artifact

The ACT router is the single entry point from the kernel into
the multi-street execution layer. It enforces:

    1. Only registered streets receive tasks
    2. Every routing decision produces a receipt
    3. Street output is stored as artifact
    4. Gate verdict is attached to the routing receipt
    5. Failed gates do not produce artifacts

ACT does NOT:
    - Decide policy (that is the governor)
    - Execute tools (that is the E layer)
    - Verify (that is HAL / gate)
    - Claim authority (non-sovereign)

Usage:
    router = ACTRouter()
    router.register_street("coding", create_coding_street())
    router.register_street("marketing", create_marketing_street())

    result = router.route(task={"task_id": "T-1", "domain": "code", ...})
    # result.street_id, result.artifact, result.gate_verdict, result.receipt_hash
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical_hash
from helensh.artifacts import ArtifactStore, ArtifactRef
from helensh.egregor.street_base import AbstractStreet


# ── Types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoutingReceipt:
    """Receipt for a routing decision.

    Every ACT routing produces one of these — governs the decision trail.
    """
    task_id: str
    street_id: str            # which street was selected
    routing_reason: str       # why this street
    gate_verdict: str         # PASS | WARN | BLOCK | ERROR
    artifact_ref: Optional[ArtifactRef]  # content-addressed artifact (if produced)
    receipt_hash: str         # hash of routing decision
    timestamp_ns: int
    authority: bool = False   # always False — non-sovereign

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "street_id": self.street_id,
            "routing_reason": self.routing_reason,
            "gate_verdict": self.gate_verdict,
            "artifact_ref": self.artifact_ref.to_dict() if self.artifact_ref else None,
            "receipt_hash": self.receipt_hash,
            "timestamp_ns": self.timestamp_ns,
            "authority": self.authority,
        }


@dataclass(frozen=True)
class RoutingResult:
    """Full result from ACT routing.

    Contains the street output, gate result, artifact reference,
    and the routing receipt.
    """
    street_id: str
    artifact: Optional[Dict[str, Any]]    # aggregated street output
    gate_verdict: str                      # PASS | WARN | BLOCK | ERROR
    gate_reasons: Tuple[str, ...]
    packet: Optional[Dict[str, Any]]      # exportable packet (None if BLOCK)
    artifact_ref: Optional[ArtifactRef]   # content-addressed ref
    receipt: RoutingReceipt
    success: bool                          # True if gate != BLOCK and no error
    error: Optional[str]                   # error message if failed


# ── Domain → Street Matching ──────────────────────────────────────


def _match_street(
    task: Dict[str, Any],
    streets: Dict[str, AbstractStreet],
) -> Optional[str]:
    """Match a task to a street by domain overlap.

    Matching rules (in priority order):
        1. Explicit "street_id" in task → direct match
        2. Task "domain" overlaps with street's allowed_domains → best match
        3. No match → None

    This is deliberately simple. Complex routing belongs in a
    higher-level orchestrator, not in ACT.
    """
    # Rule 1: explicit street selection
    explicit = task.get("street_id")
    if explicit and explicit in streets:
        return explicit

    # Rule 2: domain overlap
    task_domain = task.get("domain", "")
    best_match = None
    best_score = 0

    for street_id, street in streets.items():
        charter = street.charter
        for domain in charter.allowed_domains:
            if domain == task_domain:
                return street_id  # exact match
            if domain in task_domain or task_domain in domain:
                score = len(domain)
                if score > best_score:
                    best_score = score
                    best_match = street_id

    return best_match


# ── ACT Router ────────────────────────────────────────────────────


class ACTRouter:
    """Routes tasks to EGREGOR streets under governance.

    The ACT router is the bridge between the kernel and the
    multi-street execution layer. Every routing decision is
    receipted and every artifact is content-addressed.

    Usage:
        router = ACTRouter(artifact_store=store)
        router.register_street("coding", coding_street)
        result = router.route({"task_id": "T-1", "domain": "code"})
    """

    def __init__(
        self,
        artifact_store: Optional[ArtifactStore] = None,
    ) -> None:
        self._streets: Dict[str, AbstractStreet] = {}
        self._artifact_store = artifact_store
        self._routing_log: List[RoutingReceipt] = []

    # ── Registration ──

    def register_street(self, street_id: str, street: AbstractStreet) -> None:
        """Register a street for routing.

        Raises ValueError if street_id already registered.
        """
        if street_id in self._streets:
            raise ValueError(f"street '{street_id}' already registered")
        self._streets[street_id] = street

    def has_street(self, street_id: str) -> bool:
        """Check if a street is registered."""
        return street_id in self._streets

    def list_streets(self) -> List[str]:
        """List registered street IDs."""
        return sorted(self._streets.keys())

    # ── Routing ──

    def route(self, task: Dict[str, Any]) -> RoutingResult:
        """Route a task to the appropriate street and execute.

        Process:
            1. Match task to street (by domain or explicit street_id)
            2. Run the street lifecycle (route → shops → aggregate → gate)
            3. Store artifact (if gate passes and store available)
            4. Create routing receipt
            5. Return full result

        If no street matches, returns an error result with gate_verdict="ERROR".
        """
        task_id = task.get("task_id", "T-unknown")
        timestamp = time.monotonic_ns()

        # ── Match street ──
        matched = _match_street(task, self._streets)

        if matched is None:
            return self._error_result(
                task_id, timestamp,
                f"no street matches task domain '{task.get('domain', '')}'",
            )

        # ── Execute street ──
        street = self._streets[matched]
        try:
            result = street.run(task)
        except Exception as e:
            return self._error_result(
                task_id, timestamp,
                f"street '{matched}' execution failed: {e}",
                street_id=matched,
            )

        artifact = result.get("artifact")
        gate_result = result.get("gate_result")
        packet = result.get("packet")

        gate_verdict = gate_result.verdict if gate_result else "ERROR"
        gate_reasons = tuple(gate_result.reasons) if gate_result else ()

        # ── Store artifact ──
        artifact_ref = None
        if (
            self._artifact_store is not None
            and artifact is not None
            and gate_verdict != "BLOCK"
        ):
            artifact_ref = self._artifact_store.write(
                artifact,
                artifact_type="street_output",
                source=matched,
            )

        # ── Routing receipt ──
        receipt_payload = {
            "task_id": task_id,
            "street_id": matched,
            "gate_verdict": gate_verdict,
            "artifact_id": artifact_ref.artifact_id if artifact_ref else None,
            "timestamp_ns": timestamp,
        }
        receipt_hash = canonical_hash(receipt_payload)

        receipt = RoutingReceipt(
            task_id=task_id,
            street_id=matched,
            routing_reason=f"domain match: {task.get('domain', 'explicit')}",
            gate_verdict=gate_verdict,
            artifact_ref=artifact_ref,
            receipt_hash=receipt_hash,
            timestamp_ns=timestamp,
            authority=False,
        )

        self._routing_log.append(receipt)

        success = gate_verdict in ("PASS", "WARN")

        return RoutingResult(
            street_id=matched,
            artifact=artifact,
            gate_verdict=gate_verdict,
            gate_reasons=gate_reasons,
            packet=packet,
            artifact_ref=artifact_ref,
            receipt=receipt,
            success=success,
            error=None,
        )

    # ── Routing log ──

    @property
    def routing_log(self) -> List[RoutingReceipt]:
        """Read-only copy of the routing log."""
        return list(self._routing_log)

    def routing_count(self) -> int:
        """Number of routing decisions made."""
        return len(self._routing_log)

    # ── Internal ──

    def _error_result(
        self,
        task_id: str,
        timestamp: int,
        error_msg: str,
        street_id: str = "none",
    ) -> RoutingResult:
        """Create an error routing result."""
        receipt_payload = {
            "task_id": task_id,
            "street_id": street_id,
            "gate_verdict": "ERROR",
            "artifact_id": None,
            "timestamp_ns": timestamp,
        }
        receipt_hash = canonical_hash(receipt_payload)

        receipt = RoutingReceipt(
            task_id=task_id,
            street_id=street_id,
            routing_reason=f"error: {error_msg}",
            gate_verdict="ERROR",
            artifact_ref=None,
            receipt_hash=receipt_hash,
            timestamp_ns=timestamp,
            authority=False,
        )

        self._routing_log.append(receipt)

        return RoutingResult(
            street_id=street_id,
            artifact=None,
            gate_verdict="ERROR",
            gate_reasons=(error_msg,),
            packet=None,
            artifact_ref=None,
            receipt=receipt,
            success=False,
            error=error_msg,
        )


__all__ = [
    "ACTRouter",
    "RoutingReceipt",
    "RoutingResult",
]
