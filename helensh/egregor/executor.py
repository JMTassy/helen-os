"""HELEN OS — Egregor v0 Executor.

The full governed path:

    task
    → classify()
    → pick street
    → call primary model
    → HAL review
    → if reject: fallback model
    → return result + receipt

One task enters, one street selected deterministically,
one model answer reviewed by HAL, one clean governed result exits.

That is Egregor v0.

Non-negotiables:
    - authority: False on every result
    - HAL reviews every answer — no bypass
    - receipt hash on every result — no receipt = no reality
    - fail-closed: all models fail → governed failure, not silent pass
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from helensh.state import canonical_hash
from helensh.adapters.ollama import OllamaClient, OllamaError
from helensh.agents.hal_reviewer import HalReviewer
from helensh.egregor.registry import EGREGOR_ROUTES, get_chain
from helensh.egregor.router import classify


# ── Result type ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EgregorResult:
    """One governed result from Egregor v0.

    authority is always False. Structurally.
    """
    street: str
    model: Optional[str]
    result: Optional[str]
    review: Dict[str, Any]
    receipt_hash: str
    authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "authority", False)

    @property
    def allowed(self) -> bool:
        return self.review.get("verdict") == "APPROVE"


# ── Executor ─────────────────────────────────────────────────────────────────

def run_task(
    task: str,
    client: Optional[OllamaClient] = None,
    hal: Optional[HalReviewer] = None,
    state: Optional[dict] = None,
) -> EgregorResult:
    """Execute one task through the governed Egregor pipeline.

    1. classify(task) → street
    2. For each model in street's fallback chain:
       a. Call model with task
       b. HAL reviews the result
       c. If APPROVE → return governed result with receipt
       d. If REJECT → try next model in chain
    3. All models rejected → return governed failure

    authority: False on every output. No exceptions.
    """
    if client is None:
        client = OllamaClient()
    if hal is None:
        hal = HalReviewer(client=client)
    if state is None:
        state = {"session_id": "egregor-v0", "turn": 0}

    street = classify(task)
    chain = get_chain(street)

    last_review: Dict[str, Any] = {"verdict": "REJECT", "rationale": "no models attempted"}

    for model in chain:
        # Call model
        model_output = _call_model(client, model, task)
        if model_output is None:
            continue

        # HAL reviews
        proposal = {
            "action": "egregor_response",
            "street": street,
            "model": model,
            "payload": {"task": task, "response": model_output},
            "confidence": 0.5,
            "authority": False,
        }
        review = hal.review(proposal, state)
        last_review = review

        if review.get("verdict") == "APPROVE":
            receipt = _make_receipt(street, model, task, model_output, review)
            return EgregorResult(
                street=street,
                model=model,
                result=model_output,
                review=review,
                receipt_hash=receipt,
            )

    # All models in chain failed or rejected
    receipt = _make_receipt(street, None, task, None, last_review)
    return EgregorResult(
        street=street,
        model=None,
        result=None,
        review=last_review,
        receipt_hash=receipt,
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def _call_model(client: OllamaClient, model: str, task: str) -> Optional[str]:
    """Call one Ollama model. Returns text or None on failure."""
    try:
        return client.chat(
            model=model,
            messages=[{"role": "user", "content": task}],
            temperature=0.7,
        )
    except OllamaError:
        return None


def _make_receipt(
    street: str,
    model: Optional[str],
    task: str,
    result: Optional[str],
    review: Dict[str, Any],
) -> str:
    """Create a receipt hash for this execution."""
    return canonical_hash({
        "street": street,
        "model": model,
        "task": task,
        "result_hash": canonical_hash({"text": result}) if result else None,
        "verdict": review.get("verdict", "REJECT"),
        "authority": False,
    })


__all__ = [
    "EgregorResult",
    "run_task",
]
