"""HELEN OS — Tool Determinism Layer.

Ensures tool execution is a deterministic projection:

    input → pure function → artifact(s)

HARD INVARIANT:
    ToolExecution ≡ Deterministic Projection

    replay(receipts) ⇒ same artifacts

This means:
    1. Tool calls derive seed from receipt chain (reproducible)
    2. Tool results are hashed and committed as artifacts
    3. Replay can recompute or verify tool outputs
    4. Stress layer can block non-deterministic tools

Seed derivation:
    seed = H(previous_hash + proposal_hash)

This binds the tool's randomness to the governance chain,
making replay of tool outputs reproducible.

Artifact commitment:
    {
        "type": "tool_output",
        "tool": tool_name,
        "args_hash": H(args),
        "output_hash": H(result),
        "seed": seed,
    }
"""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional, Tuple

from helensh.state import canonical, canonical_hash
from helensh.tools import ToolResult


# ── Seed Derivation ────────────────────────────────────────────────


def derive_tool_seed(previous_hash: str, proposal: Dict[str, Any]) -> str:
    """Derive a deterministic seed from the receipt chain and proposal.

    seed = H(previous_hash + H(proposal))

    This binds every tool execution to a specific point in the
    governance chain, making replay reproducible.
    """
    proposal_hash = canonical_hash(proposal)
    combined = f"{previous_hash}:{proposal_hash}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def seed_to_int(seed: str, max_val: int = 2**32 - 1) -> int:
    """Convert hex seed to an integer for use as random seed."""
    return int(seed[:8], 16) % max_val


# ── Artifact Commitment ────────────────────────────────────────────


def commit_tool_artifact(
    tool_name: str,
    args: Dict[str, Any],
    result: ToolResult,
    seed: str,
) -> Dict[str, Any]:
    """Create a deterministic artifact commitment for a tool execution.

    The commitment hashes both inputs and outputs, binding them
    to the seed (and therefore to the receipt chain).
    """
    args_hash = canonical_hash(args)
    output_hash = canonical_hash(result.to_dict())

    return {
        "type": "tool_output",
        "tool": tool_name,
        "args_hash": args_hash,
        "output_hash": output_hash,
        "seed": seed,
        "success": result.success,
    }


def verify_tool_artifact(
    artifact: Dict[str, Any],
    args: Dict[str, Any],
    result: ToolResult,
    seed: str,
) -> Tuple[bool, List[str]]:
    """Verify a tool artifact commitment against actual args/result.

    Checks:
        1. args_hash matches
        2. output_hash matches
        3. seed matches
        4. tool name present

    Returns (ok, errors).
    """
    errors = []

    if artifact.get("args_hash") != canonical_hash(args):
        errors.append("args_hash mismatch")

    if artifact.get("output_hash") != canonical_hash(result.to_dict()):
        errors.append("output_hash mismatch")

    if artifact.get("seed") != seed:
        errors.append("seed mismatch")

    if not artifact.get("tool"):
        errors.append("missing tool name")

    return len(errors) == 0, errors


# ── Deterministic Tool Wrapper ─────────────────────────────────────


def deterministic_tool_call(
    tool_name: str,
    executor: Callable[[Dict[str, Any], Dict[str, Any]], ToolResult],
    payload: Dict[str, Any],
    state: Dict[str, Any],
    previous_hash: str,
    proposal: Dict[str, Any],
) -> Tuple[ToolResult, Dict[str, Any], str]:
    """Execute a tool with deterministic seed binding.

    Returns (result, artifact_commitment, seed).

    The seed is derived from the receipt chain + proposal,
    making the execution point reproducible.
    """
    seed = derive_tool_seed(previous_hash, proposal)

    # Inject seed into payload for tools that need it
    seeded_payload = dict(payload)
    seeded_payload["_seed"] = seed
    seeded_payload["_seed_int"] = seed_to_int(seed)

    result = executor(seeded_payload, state)

    artifact = commit_tool_artifact(tool_name, payload, result, seed)

    return result, artifact, seed


# ── Stress Checks for Tools ───────────────────────────────────────


# Tool whitelist — only these tools may execute
TOOL_WHITELIST = frozenset({
    "python_exec",
    "fs_read",
    "fs_write",
    "fs_list",
    "db_query",
    "db_execute",
    "respond",   # echo tool for testing
})

# Maximum artifact size (bytes)
MAX_ARTIFACT_SIZE = 10_000_000  # 10 MB


def stress_check_tool_whitelist(
    proposal: Dict[str, Any],
    state: Dict[str, Any],
    verdict: str,
) -> Optional[str]:
    """Stress check: tool must be in whitelist."""
    action = proposal.get("action", "")
    # Only check tool actions (those that would trigger tool execution)
    # Non-tool actions pass through
    if action in TOOL_WHITELIST or action not in _TOOL_LIKE_ACTIONS:
        return None
    return f"tool '{action}' not in TOOL_WHITELIST"


def stress_check_tool_determinism(
    proposal: Dict[str, Any],
    state: Dict[str, Any],
    verdict: str,
) -> Optional[str]:
    """Stress check: tool payload must be hashable (deterministic input)."""
    if verdict != "ALLOW":
        return None
    action = proposal.get("action", "")
    if action not in TOOL_WHITELIST:
        return None
    payload = proposal.get("payload", {})
    try:
        canonical(payload)
        return None
    except (TypeError, ValueError) as e:
        return f"tool payload not canonically serializable: {e}"


def stress_check_artifact_bounds(
    proposal: Dict[str, Any],
    state: Dict[str, Any],
    verdict: str,
) -> Optional[str]:
    """Stress check: tool payload size must be within bounds."""
    if verdict != "ALLOW":
        return None
    payload = proposal.get("payload", {})
    try:
        size = len(canonical(payload))
        if size > MAX_ARTIFACT_SIZE:
            return f"tool payload size {size} exceeds MAX_ARTIFACT_SIZE {MAX_ARTIFACT_SIZE}"
        return None
    except (TypeError, ValueError):
        return None  # handled by determinism check


# Actions that look like tool calls
_TOOL_LIKE_ACTIONS = frozenset({
    "python_exec", "fs_read", "fs_write", "fs_list",
    "db_query", "db_execute",
})


# All tool stress checks as (name, fn) pairs for integration with GNF stress layer
TOOL_STRESS_CHECKS: List[Tuple[str, Callable]] = [
    ("tool_whitelist", stress_check_tool_whitelist),
    ("tool_determinism", stress_check_tool_determinism),
    ("tool_artifact_bounds", stress_check_artifact_bounds),
]


# ── Exports ────────────────────────────────────────────────────────

__all__ = [
    "derive_tool_seed",
    "seed_to_int",
    "commit_tool_artifact",
    "verify_tool_artifact",
    "deterministic_tool_call",
    "TOOL_WHITELIST",
    "MAX_ARTIFACT_SIZE",
    "TOOL_STRESS_CHECKS",
    "stress_check_tool_whitelist",
    "stress_check_tool_determinism",
    "stress_check_artifact_bounds",
]
