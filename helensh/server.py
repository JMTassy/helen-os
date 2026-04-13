"""HELEN OS — Court API (FastAPI Surface).

POST /claim     — submit a claim for adjudication
POST /attest    — submit an attestation (manual or execution-backed)
POST /run       — run the full pipeline for a claim
GET  /ledger    — read the full ledger
GET  /ledger/verify — verify hash chain integrity
GET  /ledger/decisions — replay all decisions

Non-negotiables:
    - Every mutation writes to the ledger
    - Every response includes receipt hash
    - Hash chain never breaks
    - Authority: false on every decision
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from helensh.court import (
    Claim,
    Obligation,
    Attestation,
    CourtDecision,
    CourtLedger,
    oracle_obligations,
    check_obligations,
    reducer,
    run_pipeline,
    execute_witness,
    attest_from_execution,
    GENESIS_HASH,
)

# ── Request / Response Models ─────────────────────────────────────


class ClaimRequest(BaseModel):
    claim_id: str
    text: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    requires_receipts: bool = True


class AttestRequest(BaseModel):
    claim_id: str
    obligation_name: str
    evidence: Optional[Any] = None
    # If code is provided, execute it for a strong attestation
    code: Optional[str] = None


class RunRequest(BaseModel):
    claim_id: str
    text: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    kill_flag: bool = False
    # Optional: attestations to include (by obligation_name + evidence)
    attestations: List[Dict[str, Any]] = Field(default_factory=list)
    # Optional: code to execute for execution-backed attestation
    code: Optional[str] = None


class ClaimResponse(BaseModel):
    claim_id: str
    receipt_hash: str
    obligations: List[str]


class AttestResponse(BaseModel):
    claim_id: str
    obligation_name: str
    receipt_hash: str
    valid: bool
    execution_backed: bool


class DecisionResponse(BaseModel):
    claim_id: str
    decision: str
    required: List[str]
    satisfied: List[str]
    missing: List[str]
    kill_flag: bool
    receipt_hash: str
    authority: bool


class LedgerResponse(BaseModel):
    count: int
    entries: List[Dict[str, Any]]


class VerifyResponse(BaseModel):
    valid: bool
    errors: List[str]
    entry_count: int


# ── App Setup ─────────────────────────────────────────────────────

# Module-level ledger (overridable for testing)
_ledger: Optional[CourtLedger] = None


def get_ledger() -> CourtLedger:
    """Get the current ledger instance."""
    global _ledger
    if _ledger is None:
        _ledger = CourtLedger(":memory:")
    return _ledger


def set_ledger(ledger: CourtLedger) -> None:
    """Set the ledger instance (for testing)."""
    global _ledger
    _ledger = ledger


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: init and cleanup."""
    global _ledger
    if _ledger is None:
        _ledger = CourtLedger("helen_court.db")
    yield
    if _ledger is not None:
        _ledger.close()
        _ledger = None


app = FastAPI(
    title="HELEN OS Court",
    description="Receipted decision kernel. No receipt = no ship.",
    version="2.0.0",
    lifespan=lifespan,
)


# ── Endpoints ─────────────────────────────────────────────────────


@app.post("/claim", response_model=ClaimResponse)
async def submit_claim(req: ClaimRequest) -> ClaimResponse:
    """Submit a claim for adjudication.

    Records the claim in the ledger and returns obligations.
    """
    ledger = get_ledger()

    claim = Claim(
        claim_id=req.claim_id,
        text=req.text,
        payload=req.payload,
        requires_receipts=req.requires_receipts,
    )

    receipt = ledger.record_claim(claim)
    obligations = oracle_obligations(claim)

    return ClaimResponse(
        claim_id=claim.claim_id,
        receipt_hash=receipt["hash"],
        obligations=[o.name for o in obligations],
    )


@app.post("/attest", response_model=AttestResponse)
async def submit_attestation(req: AttestRequest) -> AttestResponse:
    """Submit an attestation for a claim.

    If `code` is provided, executes it for a strong (execution-backed) attestation.
    Otherwise, creates a manual (weaker) attestation.
    """
    ledger = get_ledger()

    if req.code is not None:
        # Execution-backed attestation
        attestation, tool_result = attest_from_execution(
            req.claim_id,
            req.obligation_name,
            req.code,
        )
        receipt = ledger.record_attestation(attestation)
        return AttestResponse(
            claim_id=req.claim_id,
            obligation_name=req.obligation_name,
            receipt_hash=receipt["hash"],
            valid=attestation.valid,
            execution_backed=True,
        )
    else:
        # Manual attestation
        attestation = Attestation(
            claim_id=req.claim_id,
            obligation_name=req.obligation_name,
            evidence=req.evidence,
        )
        receipt = ledger.record_attestation(attestation)
        return AttestResponse(
            claim_id=req.claim_id,
            obligation_name=req.obligation_name,
            receipt_hash=receipt["hash"],
            valid=True,
            execution_backed=False,
        )


@app.post("/run", response_model=DecisionResponse)
async def run_claim(req: RunRequest) -> DecisionResponse:
    """Run the full pipeline for a claim.

    1. Records the claim
    2. Processes any provided attestations
    3. Optionally executes code for execution-backed attestation
    4. Runs the pipeline
    5. Records the decision
    """
    ledger = get_ledger()

    claim = Claim(
        claim_id=req.claim_id,
        text=req.text,
        payload=req.payload,
    )

    # Record claim
    ledger.record_claim(claim)

    # Build attestation list
    attestations: List[Attestation] = []

    # Manual attestations from request
    for att_dict in req.attestations:
        a = Attestation(
            claim_id=req.claim_id,
            obligation_name=att_dict.get("obligation_name", ""),
            evidence=att_dict.get("evidence"),
        )
        attestations.append(a)
        ledger.record_attestation(a)

    # Execution-backed attestation
    if req.code is not None:
        a, _ = attest_from_execution(req.claim_id, "code_execution", req.code)
        attestations.append(a)
        ledger.record_attestation(a)

    # Run pipeline
    decision = run_pipeline(claim, attestations, kill_flag=req.kill_flag)
    ledger.record_decision(decision)

    return DecisionResponse(
        claim_id=decision.claim_id,
        decision=decision.decision,
        required=list(decision.required),
        satisfied=list(decision.satisfied),
        missing=list(decision.missing),
        kill_flag=decision.kill_flag,
        receipt_hash=decision.receipt_hash,
        authority=decision.authority,
    )


@app.get("/ledger", response_model=LedgerResponse)
async def read_ledger() -> LedgerResponse:
    """Read the full ledger."""
    ledger = get_ledger()
    entries = ledger.get_all()
    return LedgerResponse(count=len(entries), entries=entries)


@app.get("/ledger/verify", response_model=VerifyResponse)
async def verify_ledger() -> VerifyResponse:
    """Verify hash chain integrity."""
    ledger = get_ledger()
    ok, errors = ledger.verify_chain()
    return VerifyResponse(valid=ok, errors=errors, entry_count=ledger.count())


@app.get("/ledger/decisions")
async def replay_decisions() -> Dict[str, Any]:
    """Replay all decisions from the ledger."""
    ledger = get_ledger()
    decisions = ledger.replay_decisions()
    return {"count": len(decisions), "decisions": decisions}


@app.get("/health")
async def health() -> Dict[str, str]:
    """Health check."""
    return {"status": "ok", "version": "2.0.0", "kernel": "court"}


# ── Exports ───────────────────────────────────────────────────────

__all__ = [
    "app",
    "get_ledger",
    "set_ledger",
    "ClaimRequest",
    "AttestRequest",
    "RunRequest",
    "ClaimResponse",
    "AttestResponse",
    "DecisionResponse",
    "LedgerResponse",
    "VerifyResponse",
]
