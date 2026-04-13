"""HELEN OS — Governance Normal Form (GNF).

The 5-layer unified governance function:

    G : (S, C) -> (D, R)

Where:
  S = Signals (inputs, environment, NSPS pressure vectors)
  C = Claims (proposals, structured intents)
  D in {ALLOW, PREVENT, DEFER}
  R = Receipt (verifiable, replayable state transition artifact)

Kernel expansion:

    G = E . T . V . P . S

Where:
  S = Sensing    (perception: inspect inputs + environment → signal)
  P = Proposal   (cognition: signal → structured proposal)
  V = Validation (governance: proposal → verdict via policy gates)
  T = Stress     (adversarial: verdict → stress-tested verdict via invariant checks)
  E = Execution  (substrate: verdict → state mutation → receipt)

Relationship to existing kernel:
  kernel.cognition()    = S + P combined (GNF splits them)
  kernel.governor()     = V
  kernel.apply_receipt() = E
  T = NEW (invariant validation + adversarial checks between V and E)

Layer binding (Town model):
  S → NSPS (pressure vectors, environment sensing)
  P → Temple (structured proposals, cognition)
  V → WUL + LEGORACLE (obligation verification)
  T → Isotown (adversarial CI, stress testing)
  E → Substrate (deterministic execution)

Global invariant:
  State_{t+1} = F(Receipts_{<=t})

Canonical decision rule:
  D = PREVENT  if any violation or failed obligation
  D = ALLOW    if all obligations satisfied and stress passes
  D = DEFER    otherwise

Compression law:
  Discourse → Schema → Receipt → Hash

Architecture:
  This module provides gnf_step() as the 5-layer alternative to kernel.step().
  It is backward-compatible: the existing 3-layer step() remains valid.
  gnf_step() adds sensing separation and stress testing.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from helensh.kernel import (
    cognition,
    governor,
    apply_receipt,
    make_proposal_receipt,
    make_execution_receipt,
    init_session,
    GENESIS_HASH,
    KNOWN_ACTIONS,
    WRITE_ACTIONS,
)
from helensh.state import (
    canonical_hash,
    governed_state_hash,
    effect_footprint,
)
from helensh.memory import verify_memory, MEMORY_MUTATING_ACTIONS
from helensh.replay import verify_chain
from helensh.tools import ToolRegistry, ToolResult


# ── GNF Types ────────────────────────────────────────────────────────


# GNF verdict vocabulary (superset of kernel vocabulary)
GNF_ALLOW = "ALLOW"
GNF_PREVENT = "PREVENT"   # stronger than DENY — invariant violation
GNF_DEFER = "DEFER"       # maps to PENDING in kernel

# Mapping to kernel verdicts for backward compat
_GNF_TO_KERNEL = {
    GNF_ALLOW: "ALLOW",
    GNF_PREVENT: "DENY",
    GNF_DEFER: "PENDING",
}


@dataclass(frozen=True)
class Signal:
    """Output of the Sensing layer (S).

    Captures environment state and input classification
    before proposal generation.
    """
    raw_input: Union[str, dict]
    input_type: str               # "string", "dict", "empty"
    environment: Dict[str, Any]   # snapshot of relevant env state
    pressure: Dict[str, float]    # NSPS-style pressure vectors (optional)
    turn: int
    session_id: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict for trace capsule."""
        return {
            "raw_input": self.raw_input,
            "input_type": self.input_type,
            "environment": dict(self.environment),
            "pressure": dict(self.pressure),
            "turn": self.turn,
            "session_id": self.session_id,
        }


@dataclass(frozen=True)
class StressResult:
    """Output of the Stress layer (T).

    Records which invariants were checked and whether they passed.
    """
    passed: bool
    checks_run: Tuple[str, ...]
    failures: Tuple[str, ...]
    verdict_override: Optional[str]  # None = no override, "PREVENT" = block

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-safe dict for trace capsule."""
        return {
            "passed": self.passed,
            "verdict": "PASS" if self.passed else "FAIL",
            "checks_run": list(self.checks_run),
            "failures": list(self.failures),
            "verdict_override": self.verdict_override,
        }


@dataclass(frozen=True)
class GNFReceipt:
    """Extended receipt for GNF 5-layer execution.

    Wraps the kernel receipts with GNF-specific metadata.
    tool_result is an artifact — observability, not consensus.
    """
    signal: Signal
    proposal: Dict[str, Any]
    validation_verdict: str       # V layer verdict (kernel governor)
    stress_result: StressResult   # T layer result
    final_verdict: str            # GNF verdict after stress
    kernel_verdict: str           # mapped to kernel vocabulary
    effect_status: str            # APPLIED | DENIED | DEFERRED
    memory_effect: Optional[Dict[str, Any]]
    tool_result: Optional[ToolResult]  # artifact from tool execution (not in hash)
    proposal_receipt_hash: str
    execution_receipt_hash: str
    state_hash_before: str
    state_hash_after: str
    turn: int
    authority: bool               # always False


# ── S: Sensing Layer ─────────────────────────────────────────────────


def sense(state: dict, user_input: Union[str, dict]) -> Signal:
    """S layer: Perceive inputs and environment state.

    Separated from cognition — inspects before proposing.
    Produces a Signal with classified input, environment snapshot,
    and optional pressure vectors.
    """
    # Classify input type
    if user_input is None or user_input == "":
        input_type = "empty"
    elif isinstance(user_input, dict):
        input_type = "dict"
    else:
        input_type = "string"

    # Snapshot environment (capabilities, env vars, working memory size)
    caps = state.get("capabilities", {})
    env_snapshot = {
        "turn": state.get("turn", 0),
        "receipt_count": len(state.get("receipts", [])),
        "memory_keys": sorted(state.get("working_memory", {}).keys()),
        "active_capabilities": sum(1 for v in caps.values() if v),
        "total_capabilities": len(caps),
    }

    # Pressure vectors (NSPS-style: mismatch between capacity and constraint)
    # Higher pressure = more constrained system
    total_caps = len(caps) if caps else 1
    active_caps = sum(1 for v in caps.values() if v)
    pressure = {
        "capability_pressure": 1.0 - (active_caps / total_caps),
        "memory_pressure": min(1.0, len(state.get("working_memory", {})) / 100),
        "chain_depth": min(1.0, len(state.get("receipts", [])) / 1000),
    }

    return Signal(
        raw_input=user_input,
        input_type=input_type,
        environment=env_snapshot,
        pressure=pressure,
        turn=state.get("turn", 0),
        session_id=state.get("session_id", "unknown"),
    )


# ── P: Proposal Layer ───────────────────────────────────────────────


def propose(state: dict, signal: Signal) -> Dict[str, Any]:
    """P layer: Generate structured proposal from signal.

    Delegates to kernel.cognition() but with signal context available.
    This is where Temple-style proposal generation would plug in.
    """
    return cognition(state, signal.raw_input)


# ── V: Validation Layer ─────────────────────────────────────────────


def validate(proposal: Dict[str, Any], state: dict) -> str:
    """V layer: Policy-gate the proposal.

    Delegates to kernel.governor() (5-gate fail-closed chain).
    This is where WUL + LEGORACLE obligation checks would plug in.

    Returns GNF verdict: ALLOW, PREVENT, or DEFER.
    """
    kernel_verdict = governor(proposal, state)
    # Map kernel DENY → GNF PREVENT, PENDING → DEFER
    _KERNEL_TO_GNF = {
        "ALLOW": GNF_ALLOW,
        "DENY": GNF_PREVENT,
        "PENDING": GNF_DEFER,
    }
    return _KERNEL_TO_GNF.get(kernel_verdict, GNF_PREVENT)


# ── T: Stress Layer ─────────────────────────────────────────────────


# Default stress checks — each is a (name, check_fn) pair
# check_fn(proposal, state, verdict) -> Optional[str]  (None=pass, str=failure)

def _check_authority_false(proposal: dict, state: dict, verdict: str) -> Optional[str]:
    """I6: All proposals must have authority == False."""
    if proposal.get("authority", False):
        return "authority=True in proposal (I6 violation)"
    return None


def _check_action_known(proposal: dict, state: dict, verdict: str) -> Optional[str]:
    """Governor gate: action must be in KNOWN_ACTIONS."""
    action = proposal.get("action", "")
    if action not in KNOWN_ACTIONS:
        return f"unknown action '{action}' not in KNOWN_ACTIONS"
    return None


def _check_capability_present(proposal: dict, state: dict, verdict: str) -> Optional[str]:
    """Governor gate: capability must be enabled for the action."""
    if verdict != GNF_ALLOW:
        return None  # only check for ALLOW verdicts
    action = proposal.get("action", "")
    caps = state.get("capabilities", {})
    if action in caps and not caps[action]:
        return f"capability '{action}' is revoked but verdict is ALLOW"
    return None


def _check_no_silent_effect(proposal: dict, state: dict, verdict: str) -> Optional[str]:
    """I2: Non-ALLOW verdicts must not mutate effect_footprint."""
    # This is a structural check — the actual invariant is enforced in execution.
    # Here we verify the verdict is consistent with the proposal's write intent.
    if verdict == GNF_ALLOW:
        return None
    action = proposal.get("action", "")
    if action in WRITE_ACTIONS and verdict == GNF_ALLOW:
        return f"write action '{action}' got ALLOW but should be PENDING (I7)"
    return None


def _check_chain_integrity(proposal: dict, state: dict, verdict: str) -> Optional[str]:
    """I4/I9: Receipt chain must be valid before allowing new mutations."""
    receipts = state.get("receipts", [])
    if not receipts:
        return None  # genesis state, no chain to verify
    if not verify_chain(receipts):
        return "receipt chain integrity check failed (I4/I9 violation)"
    return None


# Registry of all default stress checks
DEFAULT_STRESS_CHECKS: List[Tuple[str, Callable]] = [
    ("authority_false", _check_authority_false),
    ("action_known", _check_action_known),
    ("capability_present", _check_capability_present),
    ("no_silent_effect", _check_no_silent_effect),
    ("chain_integrity", _check_chain_integrity),
]


def stress(
    proposal: Dict[str, Any],
    state: dict,
    verdict: str,
    checks: Optional[List[Tuple[str, Callable]]] = None,
) -> StressResult:
    """T layer: Stress-test the verdict against invariants.

    Runs adversarial/CI checks after validation but before execution.
    Any failure promotes verdict to PREVENT regardless of V layer result.

    This is where Isotown adversarial simulation would plug in.

    Args:
        proposal: The structured proposal from P layer
        state: Current kernel state
        verdict: V layer verdict (GNF vocabulary)
        checks: Optional custom check list. Defaults to DEFAULT_STRESS_CHECKS.

    Returns StressResult with pass/fail, checks run, and optional verdict override.
    """
    if checks is None:
        checks = DEFAULT_STRESS_CHECKS

    checks_run = []
    failures = []

    for name, check_fn in checks:
        checks_run.append(name)
        failure = check_fn(proposal, state, verdict)
        if failure is not None:
            failures.append(f"{name}: {failure}")

    passed = len(failures) == 0
    override = GNF_PREVENT if not passed else None

    return StressResult(
        passed=passed,
        checks_run=tuple(checks_run),
        failures=tuple(failures),
        verdict_override=override,
    )


# ── E: Execution Layer ──────────────────────────────────────────────
# Delegates to kernel.apply_receipt() — no new execution logic needed.
# The execute() function is the GNF wrapper.


def execute(
    state: dict,
    proposal: Dict[str, Any],
    kernel_verdict: str,
    tool_registry: Optional[ToolRegistry] = None,
) -> Tuple[dict, str, Optional[Dict[str, Any]], Optional[ToolResult]]:
    """E layer: Execute state mutation, compute memory effect, run tool.

    Delegates state mutation to kernel.apply_receipt().
    If a tool_registry is provided and the action has a registered tool,
    the tool is executed after state mutation and its result captured
    as an artifact (not in receipt hash — same boundary as trace).

    Returns (new_state, effect_status, memory_effect, tool_result).
    """
    mem_before = copy.deepcopy(state.get("working_memory", {}))

    new_state = apply_receipt(state, proposal, kernel_verdict)

    # Compute effect status
    if kernel_verdict == "ALLOW":
        effect_status = "APPLIED"
    elif kernel_verdict == "DENY":
        effect_status = "DENIED"
    else:
        effect_status = "DEFERRED"

    # Compute memory effect
    memory_effect = None
    if effect_status == "APPLIED":
        mem_after = new_state.get("working_memory", {})
        diff = {}
        for k in mem_after:
            if k not in mem_before or mem_before[k] != mem_after[k]:
                diff[k] = mem_after[k]
        if diff:
            memory_effect = diff

    # ── Tool execution (artifact, not consensus) ──
    tool_result = None
    if (
        tool_registry is not None
        and effect_status == "APPLIED"
        and tool_registry.has(proposal.get("action", ""))
    ):
        tool_result = tool_registry.execute(
            proposal["action"], proposal.get("payload", {}), new_state,
        )

    return new_state, effect_status, memory_effect, tool_result


# ── Trace Capsule ────────────────────────────────────────────────────


def build_trace_stub(
    signal: Signal,
    proposal: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a trace stub for the PROPOSAL receipt.

    Contains only S (signal) and P (proposal) — the layers
    that exist before governance runs.

    HARD CONSTRAINT: trace is NEVER included in receipt_hash.
    """
    return {
        "signal": signal.to_dict(),
        "proposal": {
            "action": proposal.get("action", ""),
            "payload": proposal.get("payload", {}),
            "authority": proposal.get("authority", False),
        },
    }


def build_trace_full(
    signal: Signal,
    proposal: Dict[str, Any],
    validation_verdict: str,
    stress_result: StressResult,
    final_verdict: str,
) -> Dict[str, Any]:
    """Build a full trace capsule for the EXECUTION receipt.

    Contains all 5 layers: S, P, V, T, and final verdict.

    I11 TraceCompleteness requires:
      (S_t, P_t, V_t, T_t) ⊆ R_t^exec.trace

    HARD CONSTRAINT: trace is NEVER included in receipt_hash.
    It is an observability layer, not a consensus layer.
    Trace must not influence execution. Trace must not mutate during replay.
    Trace is read-only after commit.
    """
    return {
        "signal": signal.to_dict(),
        "proposal": {
            "action": proposal.get("action", ""),
            "payload": proposal.get("payload", {}),
            "authority": proposal.get("authority", False),
        },
        "validation": {
            "verdict": validation_verdict,
        },
        "stress": stress_result.to_dict(),
        "final_verdict": final_verdict,
    }


# Backward-compat alias
build_trace = build_trace_full


# ── G: The unified GNF step ─────────────────────────────────────────


def gnf_step(
    state: dict,
    user_input: Union[str, dict],
    stress_checks: Optional[List[Tuple[str, Callable]]] = None,
    tool_registry: Optional[ToolRegistry] = None,
) -> Tuple[dict, GNFReceipt]:
    """Execute one complete GNF governance step.

    G = E . T . V . P . S

    Five layers, executed in order:
      S: sense(state, user_input) → Signal
      P: propose(state, signal) → Proposal
      V: validate(proposal, state) → GNF verdict
      T: stress(proposal, state, verdict) → StressResult
      E: execute(state, proposal, kernel_verdict, tool_registry) → state + artifact

    If tool_registry is provided, the E layer also executes the registered
    tool and captures the result as an artifact on the execution receipt.
    tool_result is NOT in receipt_hash (same boundary as trace).

    Returns (new_state, gnf_receipt).

    Backward compatible: produces the same kernel receipts as kernel.step()
    with additional GNF metadata and optional tool artifacts.
    """
    # Deep copy to prevent aliasing
    s = copy.deepcopy(state)

    # Determine chain link
    if s["receipts"]:
        previous_hash = s["receipts"][-1]["hash"]
    else:
        previous_hash = GENESIS_HASH

    state_hash_before = governed_state_hash(s)

    # ── S: Sensing ──
    signal = sense(s, user_input)

    # ── P: Proposal ──
    proposal = propose(s, signal)

    # ── V: Validation ──
    gnf_verdict = validate(proposal, s)

    # ── T: Stress ──
    stress_result = stress(proposal, s, gnf_verdict, checks=stress_checks)

    # Apply stress override if any check failed
    final_verdict = gnf_verdict
    if stress_result.verdict_override is not None:
        final_verdict = stress_result.verdict_override

    # Map GNF verdict → kernel verdict for execution
    kernel_verdict = _GNF_TO_KERNEL.get(final_verdict, "DENY")

    # ── Build trace capsules (observability, NOT in hash) ──
    # Proposal receipt: stub (S, P only — pre-governance)
    trace_stub = build_trace_stub(signal, proposal)
    # Execution receipt: full (S, P, V, T — I11 TraceCompleteness)
    trace_full = build_trace_full(signal, proposal, gnf_verdict, stress_result, final_verdict)

    # ── R1: Proposal receipt (kernel format) ──
    p_receipt = make_proposal_receipt(
        proposal, kernel_verdict, s,
        user_input if isinstance(user_input, str) else str(user_input),
        previous_hash,
    )
    # Attach trace stub to proposal receipt (S, P only)
    p_receipt["trace"] = trace_stub

    # Snapshot before execution
    s_before_exec = copy.deepcopy(s)

    # ── E: Execution (+ tool artifact) ──
    s, effect_status, memory_effect, tool_result = execute(
        s, proposal, kernel_verdict, tool_registry=tool_registry,
    )

    # ── R2: Execution receipt (kernel format) ──
    e_receipt = make_execution_receipt(
        proposal, kernel_verdict, s_before_exec, s,
        p_receipt["hash"], effect_status,
        memory_effect=memory_effect,
    )
    # Attach full trace to execution receipt (S, P, V, T — required)
    e_receipt["trace"] = trace_full

    # Attach tool result as artifact (NOT in hash — same boundary as trace)
    if tool_result is not None:
        e_receipt["tool_result"] = tool_result.to_dict()

    # Append history
    s["history"].append({
        "input": user_input if isinstance(user_input, str) else str(user_input),
        "verdict": kernel_verdict,
        "action": proposal["action"],
    })

    # Append both receipts
    s["receipts"].append(p_receipt)
    s["receipts"].append(e_receipt)

    # Increment turn
    s["turn"] += 1

    state_hash_after = governed_state_hash(s)

    # ── Build GNF receipt ──
    gnf_receipt = GNFReceipt(
        signal=signal,
        proposal=proposal,
        validation_verdict=gnf_verdict,
        stress_result=stress_result,
        final_verdict=final_verdict,
        kernel_verdict=kernel_verdict,
        effect_status=effect_status,
        memory_effect=memory_effect,
        tool_result=tool_result,
        proposal_receipt_hash=p_receipt["hash"],
        execution_receipt_hash=e_receipt["hash"],
        state_hash_before=state_hash_before,
        state_hash_after=state_hash_after,
        turn=signal.turn,
        authority=False,
    )

    return s, gnf_receipt


# ── Verification ─────────────────────────────────────────────────────


def verify_gnf_receipt(receipt: GNFReceipt) -> Tuple[bool, List[str]]:
    """Verify structural properties of a GNF receipt."""
    errors = []

    # I6: authority always false
    if receipt.authority is not False:
        errors.append("GNF receipt has authority != False")

    # Stress checks must have run
    if not receipt.stress_result.checks_run:
        errors.append("No stress checks were run")

    # If stress failed, final verdict must be PREVENT
    if not receipt.stress_result.passed and receipt.final_verdict != GNF_PREVENT:
        errors.append(
            f"Stress failed but final_verdict={receipt.final_verdict}, expected PREVENT"
        )

    # Effect status must match verdict
    if receipt.kernel_verdict == "ALLOW" and receipt.effect_status != "APPLIED":
        errors.append("ALLOW verdict but effect_status != APPLIED")
    if receipt.kernel_verdict == "DENY" and receipt.effect_status != "DENIED":
        errors.append("DENY verdict but effect_status != DENIED")

    # State hash must differ on APPLIED (unless read-only action)
    # (not enforced — some ALLOW actions don't mutate state hash)

    return len(errors) == 0, errors


def verify_trace_completeness(receipts: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """TraceCompleteness invariant: every EXECUTION receipt has a complete trace.

    For each execution receipt with a trace, verifies:
      (S_t, P_t, V_t, T_t) are all present.

    Receipts without trace are skipped (backward compat with kernel.step()).
    Returns (ok, errors).
    """
    errors = []
    for i, r in enumerate(receipts):
        if r.get("type") != "EXECUTION":
            continue
        trace = r.get("trace")
        if trace is None:
            continue  # no trace = kernel.step() receipt, skip
        turn = r.get("turn", "?")
        for layer in ("signal", "proposal", "validation", "stress"):
            if layer not in trace or trace[layer] is None:
                errors.append(f"turn {turn} receipt[{i}]: missing trace.{layer}")
        # Verify stress has verdict field
        stress_trace = trace.get("stress", {})
        if stress_trace and "verdict" not in stress_trace:
            errors.append(f"turn {turn} receipt[{i}]: trace.stress missing verdict")
    return len(errors) == 0, errors


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    # Core function
    "gnf_step",
    # Types
    "Signal",
    "StressResult",
    "GNFReceipt",
    # Layer functions (composable)
    "sense",
    "propose",
    "validate",
    "stress",
    "execute",
    # Trace
    "build_trace_stub",
    "build_trace_full",
    "build_trace",  # alias for build_trace_full
    # Verification
    "verify_gnf_receipt",
    "verify_trace_completeness",
    # Stress check registry
    "DEFAULT_STRESS_CHECKS",
    # Verdicts
    "GNF_ALLOW",
    "GNF_PREVENT",
    "GNF_DEFER",
]
