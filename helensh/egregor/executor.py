"""EGREGOR Executor — Where reality happens.

The governed path:

    task → classify() → pick street → call model → hal_review()
      → if reject: fallback model → hal_review()
      → return result + attempts trace

hal_review is NOT intelligent. NOT semantic. JUST A GUARDRAIL.
Criteria today: non-empty, non-broken. That's it.

Later: syntax checks, schema checks, invariants, real hal_reviewer.

If this works:
    THEN you add receipts
    THEN you add consensus
    THEN you add FACT-style tools

If this breaks:
    everything else is fake
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from helensh.egregor.router import classify
from helensh.egregor.registry import get_models_for_street


# ── Ollama call ──────────────────────────────────────────────────────────────

def ollama_call(model: str, prompt: str) -> str:
    """Call a model via Ollama. Replace with real client."""
    from helensh.adapters.ollama import OllamaClient, OllamaError
    client = OllamaClient()
    try:
        return client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
    except OllamaError:
        return ""


# ── HAL review stub ──────────────────────────────────────────────────────────

def hal_review(output: str) -> Dict[str, Any]:
    """Minimal HAL stub. Replace later with real HAL agent.

    NOT intelligent. NOT semantic. JUST A GUARDRAIL.
    Criteria: non-empty, non-broken. That's it.
    """
    if not output or len(output.strip()) < 5:
        return {"verdict": "REJECT", "reason": "empty_or_short"}

    return {"verdict": "APPROVE", "reason": "basic_pass"}


# ── Executor ─────────────────────────────────────────────────────────────────

def run_task(task: str) -> Dict[str, Any]:
    """Execute one task through the governed Egregor pipeline.

    Returns:
        street:   which street was selected
        model:    which model produced the approved output (None if all rejected)
        output:   the approved output text (None if all rejected)
        approved: bool — did any model pass HAL review?
        attempts: full trace of every model tried + HAL verdict
    """
    street = classify(task)
    models = get_models_for_street(street)

    attempts: List[Dict[str, Any]] = []

    for model in models:
        result = ollama_call(model, task)

        review = hal_review(result)

        attempts.append({
            "model": model,
            "result": result,
            "review": review,
        })

        if review["verdict"] == "APPROVE":
            return {
                "street": street,
                "model": model,
                "output": result,
                "approved": True,
                "attempts": attempts,
            }

    return {
        "street": street,
        "model": None,
        "output": None,
        "approved": False,
        "attempts": attempts,
    }


# ── Receipted executor ───────────────────────────────────────────────────────

def run_task_receipted(task: str, ledger: "CourtLedger") -> Dict[str, Any]:
    """run_task + every attempt becomes a receipt in CourtLedger.

    This is Egregor wired into the court.
    No receipt = no reality.
    """
    from helensh.court import CourtLedger  # avoid circular at module level

    result = run_task(task)

    # Record every attempt
    for attempt in result["attempts"]:
        ledger.record_egregor_attempt(
            task=task,
            street=result["street"],
            model=attempt["model"],
            result=attempt["result"],
            verdict=attempt["review"]["verdict"],
            reason=attempt["review"]["reason"],
        )

    # Record final decision
    ledger.record_egregor_result(
        task=task,
        street=result["street"],
        model=result["model"],
        approved=result["approved"],
        attempt_count=len(result["attempts"]),
    )

    return result


__all__ = [
    "ollama_call",
    "hal_review",
    "run_task",
    "run_task_receipted",
]
