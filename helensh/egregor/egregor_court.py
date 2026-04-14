"""HELEN OS — Egregor × Court: Governed Intelligence.

Egregor (intelligence) + Court (truth).

run_task_governed:
    TASK → CLAIM → ROUTE → EXECUTE → REVIEW → ATTESTATION → DECISION → RECEIPTS

Every step is a receipt in the CourtLedger.
If it's not in the ledger, it didn't happen.

run_task_with_tools:
    Same as above, but code tasks get python_exec attestations.
    tool_result_hash != None. Strong evidence. Not vibes.

Limitation (intentional):
    run_task_governed uses tool_result_hash=None (weak evidence).
    run_task_with_tools uses python_exec (strong evidence).
    The system stops trusting LLMs and starts trusting execution.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from helensh.state import canonical_hash
from helensh.court import (
    Claim,
    Attestation,
    CourtDecision,
    CourtLedger,
    run_pipeline,
    attest_from_execution,
)
from helensh.egregor.executor import run_task


# ── Governed Executor (weak attestations) ────────────────────────────────────

def run_task_governed(task: str, ledger: CourtLedger) -> Dict[str, Any]:
    """Run Egregor task through the Court.

    1. Create claim → receipt
    2. Run egregor (routing + execution + HAL review)
    3. If approved → attestation (weak: no tool_result_hash) → receipt
    4. Run court pipeline → SHIP or NO_SHIP → receipt

    Returns:
        claim:    the Claim object
        egregor:  the raw Egregor result (street, model, output, attempts)
        decision: CourtDecision (SHIP or NO_SHIP)
    """
    # 1. Claim
    claim = Claim(
        claim_id=f"egregor_{ledger.count() + 1}",
        text=task,
    )
    ledger.record_claim(claim)

    # 2. Egregor
    result = run_task(task)

    # 3. Attestations
    attestations: List[Attestation] = []

    if result["approved"]:
        att = Attestation(
            claim_id=claim.claim_id,
            obligation_name="basic_proof",
            evidence=result["output"],
            tool_result_hash=None,  # weak — LLM output, not execution
            valid=True,
        )
        attestations.append(att)
        ledger.record_attestation(att)

    # 4. Court decision
    decision = run_pipeline(
        claim,
        attestations,
        kill_flag=False,
    )
    ledger.record_decision(decision)

    return {
        "claim": claim,
        "egregor": result,
        "decision": decision,
    }


# ── Tool-Bound Executor (strong attestations) ───────────────────────────────

def run_task_with_tools(task: str, ledger: CourtLedger) -> Dict[str, Any]:
    """Run Egregor task with real tool execution for code tasks.

    If the code street produces code → execute it via python_exec
    → attestation has tool_result_hash (strong evidence).

    Non-code tasks get weak attestations (same as run_task_governed).

    This is where HELEN stops trusting LLMs and starts trusting execution.
    """
    # 1. Claim
    claim = Claim(
        claim_id=f"egregor_{ledger.count() + 1}",
        text=task,
        requires_receipts=True,
    )
    ledger.record_claim(claim)

    # 2. Egregor
    result = run_task(task)

    # 3. Attestations
    attestations: List[Attestation] = []

    if result["approved"]:
        code_output = result["output"]

        # If code street → try to execute the output as Python
        if result["street"] == "code" and _looks_like_code(code_output):
            exec_att, tool_result = _attest_code(claim.claim_id, code_output)
            attestations.append(exec_att)
            ledger.record_attestation(exec_att)

            # Also add output_verification if execution succeeded
            if tool_result.success:
                verify_att = Attestation(
                    claim_id=claim.claim_id,
                    obligation_name="output_verification",
                    evidence=str(tool_result.output),
                    tool_result_hash=canonical_hash(tool_result.to_dict()),
                    valid=True,
                )
                attestations.append(verify_att)
                ledger.record_attestation(verify_att)

            # basic_proof backed by execution
            basic_att = Attestation(
                claim_id=claim.claim_id,
                obligation_name="basic_proof",
                evidence=code_output,
                tool_result_hash=canonical_hash(tool_result.to_dict()),
                valid=tool_result.success,
            )
            attestations.append(basic_att)
            ledger.record_attestation(basic_att)
        else:
            # Non-code: weak attestation (same as governed)
            att = Attestation(
                claim_id=claim.claim_id,
                obligation_name="basic_proof",
                evidence=code_output,
                tool_result_hash=None,
                valid=True,
            )
            attestations.append(att)
            ledger.record_attestation(att)

    # 4. Court decision
    decision = run_pipeline(
        claim,
        attestations,
        kill_flag=False,
    )
    ledger.record_decision(decision)

    return {
        "claim": claim,
        "egregor": result,
        "decision": decision,
        "tool_bound": result["street"] == "code" and result["approved"],
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _looks_like_code(text: str) -> bool:
    """Quick check: does this look like executable Python?"""
    if not text or not text.strip():
        return False
    t = text.strip()
    # Has def/class/print/assignment — good enough
    return any(kw in t for kw in ("def ", "class ", "print(", "= ", "for ", "if ", "while "))


def _attest_code(claim_id: str, code: str):
    """Execute code via python_exec and return (Attestation, ToolResult)."""
    return attest_from_execution(
        claim_id=claim_id,
        obligation_name="code_execution",
        code=code,
    )


__all__ = [
    "run_task_governed",
    "run_task_with_tools",
]
