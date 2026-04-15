"""
HELEN OS Mandatory Intent Gateway

Enforcement layer: no raw input reaches the kernel.
Every user message must become a typed intent before execution.

Flow:
    user_input → classify → extract → envelope → govern → execute → receipt

Kill switch: kernel rejects anything without proposal_type == "INTENT_EXECUTION_REQUEST"

authority = NONE on everything.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from helen_os.intents.schemas import (
    IntentEnvelope, IntentResult, IntentReceipt,
    make_envelope, make_receipt, validate_payload,
    INTENT_REGISTRY, ALL_INTENT_TYPES, MEMORY_WRITABLE_INTENTS,
    _hash,
)
from helen_os.intents.classifier import classify_intent, extract_payload, route_input
from helen_os.intents.governor import govern_intent


# ---------------------------------------------------------------------------
# Gateway records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GatewayLog:
    """Observability record for every gateway invocation."""
    input_text: str
    intent_type: str
    intent_id: str
    valid: bool
    governed: bool
    executed: bool
    receipt_id: Optional[str]
    rejection_reason: Optional[str]
    duration_ms: int
    timestamp: str
    authority: str = "NONE"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GatewayMetrics:
    """Running metrics for the gateway."""
    total_requests: int = 0
    classified: int = 0
    validated: int = 0
    executed: int = 0
    rejected: int = 0
    receipts_emitted: int = 0

    @property
    def classification_rate(self) -> float:
        return self.classified / max(1, self.total_requests)

    @property
    def validation_rate(self) -> float:
        return self.validated / max(1, self.total_requests)

    @property
    def receipt_rate(self) -> float:
        return self.receipts_emitted / max(1, self.total_requests)

    def to_dict(self) -> dict:
        return {
            "total_requests": self.total_requests,
            "classified": self.classified,
            "validated": self.validated,
            "executed": self.executed,
            "rejected": self.rejected,
            "receipts_emitted": self.receipts_emitted,
            "classification_rate": round(self.classification_rate, 3),
            "validation_rate": round(self.validation_rate, 3),
            "receipt_rate": round(self.receipt_rate, 3),
        }


# ---------------------------------------------------------------------------
# Proposal envelope — the typed contract between gateway and kernel
# ---------------------------------------------------------------------------

PROPOSAL_TYPE = "INTENT_EXECUTION_REQUEST"


def make_proposal(envelope: IntentEnvelope) -> dict:
    """Wrap an intent envelope into a kernel-ready proposal."""
    return {
        "proposal_type": PROPOSAL_TYPE,
        "intent_ref": envelope.intent_id,
        "intent_type": envelope.intent_type,
        "payload": envelope.payload,
        "payload_hash": _hash(envelope.payload),
        "source_input": envelope.source_input,
        "confidence": envelope.confidence,
        "authority": False,
    }


# ---------------------------------------------------------------------------
# Kill switch — kernel enforcement
# ---------------------------------------------------------------------------

def enforce_proposal_type(proposal: dict) -> Tuple[bool, Optional[str]]:
    """Kill switch: reject anything without valid proposal_type.

    This is the hard enforcement. Without this, the system is porous.
    """
    if not isinstance(proposal, dict):
        return False, "Proposal must be a dict"
    if proposal.get("proposal_type") != PROPOSAL_TYPE:
        return False, "raw_input_forbidden — must be INTENT_EXECUTION_REQUEST"
    if proposal.get("authority") is not False:
        return False, "authority must be False"
    if not proposal.get("intent_type"):
        return False, "missing intent_type"
    if not proposal.get("payload_hash"):
        return False, "missing payload_hash"
    return True, None


# ---------------------------------------------------------------------------
# The Gateway
# ---------------------------------------------------------------------------

class IntentGateway:
    """Mandatory Intent Gateway — the single entry point for all user input.

    No raw input reaches execution. Every message is:
    1. Classified into an intent type
    2. Payload extracted into typed fields
    3. Wrapped in an IntentEnvelope
    4. Governed (5-gate check)
    5. Wrapped in a proposal (INTENT_EXECUTION_REQUEST)
    6. Kill-switch verified
    7. Executed via the provided executor
    8. Receipted

    authority = NONE on every record.
    """

    def __init__(
        self,
        executor: Optional[Callable[[dict, dict], Tuple[dict, Optional[str]]]] = None,
        on_log: Optional[Callable[[GatewayLog], None]] = None,
    ):
        """
        Args:
            executor: Function(proposal, payload) -> (result_dict, error_or_none)
                      If None, gateway validates but does not execute.
            on_log: Callback for every gateway invocation log entry.
        """
        self.executor = executor
        self.on_log = on_log
        self.metrics = GatewayMetrics()
        self.logs: List[GatewayLog] = []

    def process(self, user_input: str, state: Optional[dict] = None) -> dict:
        """Process user input through the mandatory gateway.

        Returns a structured result dict with:
        - type: INTENT_EXECUTED | INTENT_REJECTED | INTENT_VALIDATED
        - intent_type, intent_id
        - result (if executed)
        - receipt (if executed)
        - rejection_reason (if rejected)
        - authority: NONE (always)
        """
        t0 = time.monotonic()
        self.metrics.total_requests += 1

        # Stage 1: Classify
        intent_type = classify_intent(user_input)
        self.metrics.classified += 1

        # Stage 2: Extract payload
        payload = extract_payload(intent_type, user_input)

        # Stage 3: Build envelope
        envelope = make_envelope(intent_type, user_input, payload)

        # Stage 4: Governor check
        governed_ok, rejection_reason = govern_intent(envelope)
        if not governed_ok:
            self.metrics.rejected += 1
            return self._reject(envelope, rejection_reason, t0)

        self.metrics.validated += 1

        # Stage 5: Build proposal
        proposal = make_proposal(envelope)

        # Stage 6: Kill switch
        kill_ok, kill_reason = enforce_proposal_type(proposal)
        if not kill_ok:
            self.metrics.rejected += 1
            return self._reject(envelope, kill_reason, t0)

        # Stage 7: Execute (if executor provided)
        if self.executor is None:
            # Validation-only mode
            log = self._log(envelope, valid=True, governed=True,
                           executed=False, receipt_id=None,
                           rejection=None, t0=t0)
            return {
                "type": "INTENT_VALIDATED",
                "intent_type": envelope.intent_type,
                "intent_id": envelope.intent_id,
                "proposal": proposal,
                "authority": "NONE",
                "log": log.to_dict(),
            }

        try:
            result, error = self.executor(proposal, envelope.payload)
        except Exception as e:
            error = str(e)
            result = None

        if error:
            self.metrics.rejected += 1
            return self._reject(envelope, f"execution_error: {error}", t0)

        self.metrics.executed += 1

        # Stage 8: Receipt
        output_hash = _hash(result) if result else "empty"
        intent_result = IntentResult(
            intent_id=envelope.intent_id,
            intent_type=envelope.intent_type,
            status="COMPLETED",
            output=result if isinstance(result, dict) else {"text": str(result)},
            output_hash=output_hash,
        )
        receipt = make_receipt(envelope, intent_result)
        self.metrics.receipts_emitted += 1

        # Log
        log = self._log(envelope, valid=True, governed=True,
                       executed=True, receipt_id=receipt.intent_id,
                       rejection=None, t0=t0)

        # Memory candidate (only for writable intents)
        memory_candidate = None
        if envelope.intent_type in MEMORY_WRITABLE_INTENTS and result:
            memory_candidate = {
                "kind": f"INTENT_RESULT:{envelope.intent_type}",
                "key": f"intent_{envelope.intent_id}",
                "value": result,
                "source": "gateway",
            }

        return {
            "type": "INTENT_EXECUTED",
            "intent_type": envelope.intent_type,
            "intent_id": envelope.intent_id,
            "result": result,
            "receipt": receipt.to_dict(),
            "memory_candidate": memory_candidate,
            "authority": "NONE",
            "log": log.to_dict(),
        }

    def _reject(self, envelope: IntentEnvelope, reason: str, t0: float) -> dict:
        log = self._log(envelope, valid=False, governed=False,
                       executed=False, receipt_id=None,
                       rejection=reason, t0=t0)
        return {
            "type": "INTENT_REJECTED",
            "intent_type": envelope.intent_type,
            "intent_id": envelope.intent_id,
            "reason": reason,
            "authority": "NONE",
            "log": log.to_dict(),
        }

    def _log(self, envelope, valid, governed, executed, receipt_id, rejection, t0):
        duration = int((time.monotonic() - t0) * 1000)
        log = GatewayLog(
            input_text=envelope.source_input[:200],
            intent_type=envelope.intent_type,
            intent_id=envelope.intent_id,
            valid=valid,
            governed=governed,
            executed=executed,
            receipt_id=receipt_id,
            rejection_reason=rejection,
            duration_ms=duration,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        self.logs.append(log)
        if self.on_log:
            self.on_log(log)
        return log
