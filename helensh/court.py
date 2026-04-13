"""HELEN OS — The Court (Unified Kernel v2).

The irreducible core:

    Decision = f(evidence, obligations, kill_flag)
    and nothing else.

Architecture:
    Kernel = Court
    Tools  = Witnesses
    Artifacts = Evidence
    Ledger = Law Record

Pipeline:
    CLAIM → ORACLE (obligations) → ATTESTATIONS → LEGORACLE (check)
    → REDUCER (decision) → LEDGER (receipt) → ARTIFACT (evidence)

Persistence: SQLite (single file, ACID, portable).
Hash chain: H_n = hash(payload_n + H_{n-1})
Replay: state = replay(ledger). If replay ≠ current → invalid.

Non-negotiables:
    - NO RECEIPT = NO SHIP
    - SHIP ⟹ (missing = ∅) ∧ (kill = false)
    - Every decision is deterministic and replayable
    - Attestations must come from real execution, not injection
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical, canonical_hash
from helensh.tools import ToolResult
from helensh.tools.python_exec import python_exec


# ── Types ───────────────────────────────────────────────────────────

GENESIS_HASH = "court_genesis_0000000000000000000000000000000000000000000000000000000000"


@dataclass(frozen=True)
class Claim:
    """A claim submitted to the court for adjudication."""
    claim_id: str
    text: str
    payload: Dict[str, Any] = field(default_factory=dict)
    requires_receipts: bool = True


@dataclass(frozen=True)
class Obligation:
    """An obligation that must be satisfied before SHIP."""
    name: str
    description: str = ""
    attestable: bool = True       # can be satisfied by attestation
    requires_execution: bool = False  # must come from real tool execution


@dataclass(frozen=True)
class Attestation:
    """Evidence that an obligation is satisfied.

    If produced by tool execution, includes tool_result hash.
    If injected manually, tool_result_hash is None (weaker).
    """
    claim_id: str
    obligation_name: str
    evidence: Any = None
    tool_result_hash: Optional[str] = None  # hash of ToolResult if from execution
    valid: bool = True


@dataclass(frozen=True)
class CourtDecision:
    """The reducer's output. The only authority."""
    claim_id: str
    decision: str              # SHIP | NO_SHIP
    required: Tuple[str, ...]
    satisfied: Tuple[str, ...]
    missing: Tuple[str, ...]
    kill_flag: bool
    receipt_hash: str
    authority: bool = False    # always False — non-sovereign


# ── ORACLE: Obligation Generator (non-sovereign) ──────────────────


def oracle_obligations(claim: Claim) -> List[Obligation]:
    """Generate obligations for a claim.

    The ORACLE is a signal generator — it proposes obligations
    but cannot decide anything. The reducer decides.

    In production, this delegates to EGREGOR streets.
    For now, it uses simple heuristics.
    """
    obligations = []

    text_lower = claim.text.lower()

    # Code claims require execution proof
    if any(kw in text_lower for kw in ("code", "function", "compute", "calculate", "eval")):
        obligations.append(Obligation(
            name="code_execution",
            description="Code must execute without error",
            requires_execution=True,
        ))
        obligations.append(Obligation(
            name="output_verification",
            description="Output must match expected result",
        ))

    # All claims require basic proof
    obligations.append(Obligation(
        name="basic_proof",
        description="Minimal evidence that the claim is grounded",
    ))

    return obligations


# ── Tool Execution: Real Witness ──────────────────────────────────


def execute_witness(code: str) -> Tuple[ToolResult, str]:
    """Execute code and produce a witnessed attestation.

    Returns (tool_result, result_hash).
    The hash binds the execution output to the evidence chain.
    """
    result = python_exec({"code": code}, {})
    result_hash = canonical_hash(result.to_dict())
    return result, result_hash


def attest_from_execution(
    claim_id: str,
    obligation_name: str,
    code: str,
) -> Tuple[Attestation, ToolResult]:
    """Create an attestation backed by real tool execution.

    This is the ONLY way to produce a strong attestation.
    Manual attestations are weaker (no tool_result_hash).
    """
    result, result_hash = execute_witness(code)

    attestation = Attestation(
        claim_id=claim_id,
        obligation_name=obligation_name,
        evidence=result.output,
        tool_result_hash=result_hash,
        valid=result.success,
    )

    return attestation, result


# ── LEGORACLE: Obligation Checker (hard gate) ────────────────────


def check_obligations(
    claim: Claim,
    obligations: List[Obligation],
    attestations: List[Attestation],
) -> Dict[str, Any]:
    """Check which obligations are satisfied.

    Returns {required, satisfied, missing}.
    Strictly binary: missing ≠ [] → NO_SHIP.
    """
    required = [o.name for o in obligations if o.attestable]

    satisfied = []
    missing = []

    for obl in obligations:
        if not obl.attestable:
            continue
        # Find matching attestation
        matching = [
            a for a in attestations
            if a.claim_id == claim.claim_id
            and a.obligation_name == obl.name
            and a.valid
        ]
        if matching:
            # If obligation requires execution, check for tool_result_hash
            if obl.requires_execution:
                has_execution = any(a.tool_result_hash is not None for a in matching)
                if has_execution:
                    satisfied.append(obl.name)
                else:
                    missing.append(obl.name)
            else:
                satisfied.append(obl.name)
        else:
            missing.append(obl.name)

    return {
        "required": required,
        "satisfied": satisfied,
        "missing": missing,
    }


# ── HELEN REDUCER: Sole Authority ────────────────────────────────


def reducer(kill_flag: bool, missing: List[str]) -> str:
    """The only decision function. No exceptions.

    SHIP ⟹ (missing = ∅) ∧ (kill = false)
    """
    if kill_flag:
        return "NO_SHIP"
    if len(missing) > 0:
        return "NO_SHIP"
    return "SHIP"


# ── Full Pipeline ────────────────────────────────────────────────


def run_pipeline(
    claim: Claim,
    attestations: List[Attestation],
    kill_flag: bool = False,
) -> CourtDecision:
    """Run the complete court pipeline.

    CLAIM → ORACLE → LEGORACLE → REDUCER → DECISION
    """
    # ORACLE: generate obligations
    obligations = oracle_obligations(claim)

    # LEGORACLE: check obligations
    check = check_obligations(claim, obligations, attestations)

    # REDUCER: decide
    decision = reducer(kill_flag, check["missing"])

    # Receipt
    receipt_payload = {
        "claim_id": claim.claim_id,
        "decision": decision,
        "required": check["required"],
        "satisfied": check["satisfied"],
        "missing": check["missing"],
        "kill_flag": kill_flag,
    }
    receipt_hash = canonical_hash(receipt_payload)

    return CourtDecision(
        claim_id=claim.claim_id,
        decision=decision,
        required=tuple(check["required"]),
        satisfied=tuple(check["satisfied"]),
        missing=tuple(check["missing"]),
        kill_flag=kill_flag,
        receipt_hash=receipt_hash,
    )


# ── SQLite Ledger ────────────────────────────────────────────────


class CourtLedger:
    """Append-only SQLite ledger with hash chain.

    H_n = hash(payload_n + H_{n-1})

    Every step becomes a receipt:
        type: CLAIM | ATTESTATION | DECISION
        hash: H_n
        previous_hash: H_{n-1}
        payload: canonical JSON
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                seq       INTEGER PRIMARY KEY AUTOINCREMENT,
                type      TEXT NOT NULL,
                hash      TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                payload   TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ledger_hash ON ledger(hash)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ledger_type ON ledger(type)
        """)
        self._conn.commit()

    def _last_hash(self) -> str:
        row = self._conn.execute(
            "SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else GENESIS_HASH

    def _append(self, receipt_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Append a receipt to the ledger. Returns the receipt dict."""
        prev_hash = self._last_hash()
        payload_str = canonical(payload)
        chain_hash = canonical_hash({"payload": payload_str, "previous_hash": prev_hash})

        now = time.time()
        self._conn.execute(
            "INSERT INTO ledger (type, hash, prev_hash, payload, timestamp) VALUES (?,?,?,?,?)",
            (receipt_type, chain_hash, prev_hash, payload_str, now),
        )
        self._conn.commit()

        return {
            "type": receipt_type,
            "hash": chain_hash,
            "previous_hash": prev_hash,
            "payload": payload,
            "timestamp": now,
        }

    def record_claim(self, claim: Claim) -> Dict[str, Any]:
        """Record a claim in the ledger."""
        return self._append("CLAIM", {
            "claim_id": claim.claim_id,
            "text": claim.text,
            "payload": claim.payload,
            "requires_receipts": claim.requires_receipts,
        })

    def record_attestation(self, attestation: Attestation) -> Dict[str, Any]:
        """Record an attestation in the ledger."""
        return self._append("ATTESTATION", {
            "claim_id": attestation.claim_id,
            "obligation_name": attestation.obligation_name,
            "evidence": attestation.evidence if isinstance(attestation.evidence, (str, int, float, bool, type(None))) else str(attestation.evidence),
            "tool_result_hash": attestation.tool_result_hash,
            "valid": attestation.valid,
        })

    def record_decision(self, decision: CourtDecision) -> Dict[str, Any]:
        """Record a decision in the ledger."""
        return self._append("DECISION", {
            "claim_id": decision.claim_id,
            "decision": decision.decision,
            "required": list(decision.required),
            "satisfied": list(decision.satisfied),
            "missing": list(decision.missing),
            "kill_flag": decision.kill_flag,
            "receipt_hash": decision.receipt_hash,
            "authority": decision.authority,
        })

    def get_all(self) -> List[Dict[str, Any]]:
        """Read all ledger entries in order."""
        rows = self._conn.execute(
            "SELECT seq, type, hash, prev_hash, payload, timestamp FROM ledger ORDER BY seq"
        ).fetchall()
        return [
            {
                "seq": r[0],
                "type": r[1],
                "hash": r[2],
                "previous_hash": r[3],
                "payload": json.loads(r[4]),
                "timestamp": r[5],
            }
            for r in rows
        ]

    def get_by_type(self, receipt_type: str) -> List[Dict[str, Any]]:
        """Read ledger entries of a specific type."""
        rows = self._conn.execute(
            "SELECT seq, type, hash, prev_hash, payload, timestamp FROM ledger WHERE type=? ORDER BY seq",
            (receipt_type,),
        ).fetchall()
        return [
            {
                "seq": r[0],
                "type": r[1],
                "hash": r[2],
                "previous_hash": r[3],
                "payload": json.loads(r[4]),
                "timestamp": r[5],
            }
            for r in rows
        ]

    def count(self) -> int:
        """Total ledger entries."""
        row = self._conn.execute("SELECT COUNT(*) FROM ledger").fetchone()
        return row[0]

    def verify_chain(self) -> Tuple[bool, List[str]]:
        """Verify the hash chain integrity.

        H_n = hash(payload_n + H_{n-1})
        """
        errors = []
        rows = self._conn.execute(
            "SELECT seq, hash, prev_hash, payload FROM ledger ORDER BY seq"
        ).fetchall()

        expected_prev = GENESIS_HASH
        for seq, stored_hash, prev_hash, payload_str in rows:
            if prev_hash != expected_prev:
                errors.append(f"seq {seq}: prev_hash mismatch (expected {expected_prev[:16]}..., got {prev_hash[:16]}...)")

            computed = canonical_hash({"payload": payload_str, "previous_hash": prev_hash})
            if computed != stored_hash:
                errors.append(f"seq {seq}: hash mismatch (computed {computed[:16]}..., stored {stored_hash[:16]}...)")

            expected_prev = stored_hash

        return len(errors) == 0, errors

    def replay_decisions(self) -> List[Dict[str, Any]]:
        """Replay all decisions from the ledger.

        Returns the sequence of decisions for verification.
        """
        return self.get_by_type("DECISION")

    def get_attestations_for(self, claim_id: str) -> List[Attestation]:
        """Get all attestations for a specific claim."""
        entries = self.get_by_type("ATTESTATION")
        result = []
        for e in entries:
            p = e["payload"]
            if p.get("claim_id") == claim_id:
                result.append(Attestation(
                    claim_id=p["claim_id"],
                    obligation_name=p["obligation_name"],
                    evidence=p.get("evidence"),
                    tool_result_hash=p.get("tool_result_hash"),
                    valid=p.get("valid", True),
                ))
        return result

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ── Exports ──────────────────────────────────────────────────────

__all__ = [
    "Claim",
    "Obligation",
    "Attestation",
    "CourtDecision",
    "CourtLedger",
    "oracle_obligations",
    "check_obligations",
    "reducer",
    "run_pipeline",
    "execute_witness",
    "attest_from_execution",
    "GENESIS_HASH",
]
