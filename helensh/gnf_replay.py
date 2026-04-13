"""HELEN OS — GNF Full-Trace Replay Engine.

Replays the 5-layer governance trace from receipts:

    (S_t, P_t, V_t, T_t, E_t)_{t=1..n}

Canonical form:
    ReplayGNF(R_<=t) -> (X_t, Theta_<=t)

Where:
    X_t     = final governed state (reconstructed from effects)
    Theta_t = full decision history (reconstructed from traces)

This goes beyond state reconstruction (helensh/replay.py).
It reconstructs both WHAT happened and WHY.

Architecture:
    receipts → replay_gnf() → (state, trace_log)     [canonical]
    receipts → replay_gnf_trace() → TraceEntry[]       [trace only]
    receipts → replay_gnf_decisions() → DecisionSummary[] [compressed]
    receipts → verify_gnf_trace() → trace integrity check

Hard constraint:
    Trace is observability. It does NOT influence execution.
    Trace is NOT included in receipt_hash.
    Trace MUST NOT be mutated during replay.
    Trace may explain a decision, never alter one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from helensh.memory import reconstruct_memory


# ── Trace Entry ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class TraceEntry:
    """One turn's full 5-layer governance trace.

    Reconstructed from a single EXECUTION receipt's trace capsule.
    """
    turn: int
    signal: Optional[Dict[str, Any]]
    proposal: Optional[Dict[str, Any]]
    validation: Optional[Dict[str, Any]]
    stress: Optional[Dict[str, Any]]
    effect_status: str
    final_verdict: Optional[str]
    receipt_hash: str
    has_trace: bool


@dataclass(frozen=True)
class DecisionSummary:
    """Compressed decision record for a single turn."""
    turn: int
    action: str
    validation_verdict: str
    stress_verdict: str
    final_verdict: str
    effect_status: str
    stress_failures: Tuple[str, ...]
    receipt_hash: str


# ── Replay Functions ─────────────────────────────────────────────────


def replay_gnf(
    receipts: List[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[TraceEntry]]:
    """Canonical GNF replay: ReplayGNF(R_<=t) -> (X_t, Theta_<=t).

    Returns:
        state: Final governed state reconstructed from effects.
               Contains working_memory derived only from APPLIED receipts.
        trace_log: Full decision history (S_t, P_t, V_t, T_t, E_t) per turn.

    This is the unified replay function — it produces both
    the effect witness (state) and the causal trace (decisions).
    """
    # X_t: reconstruct state from effects
    state = {
        "working_memory": reconstruct_memory(receipts),
    }

    # Theta_<=t: reconstruct causal log from traces
    trace_log = replay_gnf_trace(receipts)

    return state, trace_log


def replay_gnf_trace(receipts: List[Dict[str, Any]]) -> List[TraceEntry]:
    """Replay full 5-layer trace from receipt chain.

    Extracts (S_t, P_t, V_t, T_t, E_t) for each EXECUTION receipt.

    Receipts without trace (from kernel.step()) produce entries
    with has_trace=False and None layers.

    Returns ordered list of TraceEntry.
    """
    entries = []

    for r in receipts:
        if r.get("type") != "EXECUTION":
            continue

        trace = r.get("trace")
        turn = r.get("turn", 0)
        receipt_hash = r.get("hash", "")
        effect_status = r.get("effect_status", "UNKNOWN")

        if trace is not None and isinstance(trace, dict):
            entries.append(TraceEntry(
                turn=turn,
                signal=trace.get("signal"),
                proposal=trace.get("proposal"),
                validation=trace.get("validation"),
                stress=trace.get("stress"),
                effect_status=effect_status,
                final_verdict=trace.get("final_verdict"),
                receipt_hash=receipt_hash,
                has_trace=True,
            ))
        else:
            # kernel.step() receipt — no trace available
            proposal = r.get("proposal", {})
            entries.append(TraceEntry(
                turn=turn,
                signal=None,
                proposal={
                    "action": proposal.get("action", ""),
                    "payload": proposal.get("payload", {}),
                    "authority": proposal.get("authority", False),
                } if proposal else None,
                validation=None,
                stress=None,
                effect_status=effect_status,
                final_verdict=None,
                receipt_hash=receipt_hash,
                has_trace=False,
            ))

    return entries


def replay_gnf_decisions(receipts: List[Dict[str, Any]]) -> List[DecisionSummary]:
    """Replay compressed decision log from receipt chain.

    Returns one DecisionSummary per turn with action, verdicts,
    stress failures, and effect status.

    Only includes receipts that have full trace.
    """
    summaries = []

    for r in receipts:
        if r.get("type") != "EXECUTION":
            continue
        trace = r.get("trace")
        if trace is None:
            continue

        proposal_trace = trace.get("proposal", {})
        validation_trace = trace.get("validation", {})
        stress_trace = trace.get("stress", {})

        summaries.append(DecisionSummary(
            turn=r.get("turn", 0),
            action=proposal_trace.get("action", ""),
            validation_verdict=validation_trace.get("verdict", "UNKNOWN"),
            stress_verdict=stress_trace.get("verdict", "UNKNOWN"),
            final_verdict=trace.get("final_verdict", "UNKNOWN"),
            effect_status=r.get("effect_status", "UNKNOWN"),
            stress_failures=tuple(stress_trace.get("failures", [])),
            receipt_hash=r.get("hash", ""),
        ))

    return summaries


def verify_gnf_trace(receipts: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """Verify trace integrity across receipt chain.

    Checks:
      1. TraceCompleteness: all 4 layers present in every traced receipt
      2. Consistency: trace.final_verdict matches receipt effect_status
      3. Stress accountability: if stress FAIL → effect must be DENIED
      4. Authority: all trace proposals have authority == False

    Returns (ok, errors).
    """
    errors = []

    for i, r in enumerate(receipts):
        if r.get("type") != "EXECUTION":
            continue
        trace = r.get("trace")
        if trace is None:
            continue  # backward compat — no trace is OK

        turn = r.get("turn", "?")

        # 1. TraceCompleteness
        for layer in ("signal", "proposal", "validation", "stress"):
            if layer not in trace or trace[layer] is None:
                errors.append(f"turn {turn}: missing trace.{layer}")

        # 2. Consistency: final_verdict → effect_status alignment
        final_v = trace.get("final_verdict", "")
        effect = r.get("effect_status", "")
        if final_v == "ALLOW" and effect != "APPLIED":
            errors.append(f"turn {turn}: final_verdict=ALLOW but effect={effect}")
        if final_v == "PREVENT" and effect != "DENIED":
            errors.append(f"turn {turn}: final_verdict=PREVENT but effect={effect}")
        if final_v == "DEFER" and effect != "DEFERRED":
            errors.append(f"turn {turn}: final_verdict=DEFER but effect={effect}")

        # 3. Stress accountability
        stress_trace = trace.get("stress", {})
        if stress_trace.get("verdict") == "FAIL" and effect != "DENIED":
            errors.append(
                f"turn {turn}: stress FAIL but effect={effect} (should be DENIED)"
            )

        # 4. Authority check
        proposal_trace = trace.get("proposal", {})
        if proposal_trace.get("authority", False):
            errors.append(f"turn {turn}: trace.proposal has authority=True")

    return len(errors) == 0, errors


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "TraceEntry",
    "DecisionSummary",
    "replay_gnf",
    "replay_gnf_trace",
    "replay_gnf_decisions",
    "verify_gnf_trace",
]
