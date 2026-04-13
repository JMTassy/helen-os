"""HELEN OS — Governed Memory Disclosure.

Step 5 in the build order. Enforces:

    Memory(state) = f(receipts)

If it cannot be reconstructed from receipts, it does not exist.

Three functions:
  reconstruct_memory(receipts)  — rebuild working_memory from EXECUTION receipts only
  disclose(state)               — the ONLY allowed memory surface
  verify_memory(state)          — hard invariant: working_memory == reconstructed

This eliminates:
  - implicit memory
  - hallucinated recall
  - hidden state mutation

What is forbidden:
  "I remember that x = 1"
What is allowed:
  "Receipts show that memory_write(x=1) was APPLIED at turn 3"
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical, canonical_hash


# ── Constants ─────────────────────────────────────────────────────────

# These are the actions that can mutate working_memory when APPLIED
MEMORY_MUTATING_ACTIONS = frozenset({
    "chat", "memory_write", "witness", "task_create", "task_update",
})


# ── Core reconstruction ──────────────────────────────────────────────


def reconstruct_memory(receipts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Rebuild working_memory strictly from execution receipts.

    Only EXECUTION receipts with effect_status == "APPLIED" can contribute.
    This mirrors the exact mutations in kernel.apply_receipt().

    Memory reconstruction rules (matching kernel.py apply_receipt):
      - chat:         working_memory["last_message"] = payload.message
      - memory_write: working_memory["mem_{turn}"]   = payload.content

    All other actions produce no memory mutation.
    """
    memory: Dict[str, Any] = {}

    for r in receipts:
        if r.get("type") != "EXECUTION":
            continue
        if r.get("effect_status") != "APPLIED":
            continue

        proposal = r.get("proposal", {})
        action = proposal.get("action", "")
        payload = proposal.get("payload", {})
        turn = r.get("turn", 0)

        if action == "chat":
            message = payload.get("message", "")
            memory["last_message"] = message

        elif action == "memory_write":
            content = payload.get("content", "")
            key = f"mem_{turn}"
            memory[key] = content

        elif action == "witness":
            content = payload.get("content", "")
            key = f"witness_{turn}"
            memory[key] = content

        elif action == "task_create":
            task_id = payload.get("task_id", "")
            goal = payload.get("goal", "")
            key = f"task_{turn}"
            memory[key] = canonical(
                {"task_id": task_id, "goal": goal, "status": "OPEN"}
            )

        elif action == "task_update":
            task_id = payload.get("task_id", "")
            status = payload.get("status", "")
            key = f"task_update_{turn}"
            memory[key] = canonical(
                {"task_id": task_id, "status": status}
            )

        # read_file, list_files, search, memory_read → no working_memory mutation
        # write_file, run_command, claw_external → PENDING, never APPLIED

    return memory


# ── Disclosure ───────────────────────────────────────────────────────


def disclose(state: Dict[str, Any]) -> Dict[str, Any]:
    """The ONLY allowed memory surface.

    Returns memory reconstructed from receipts.
    If state has no receipts, returns empty dict.
    Hidden state in working_memory that cannot be derived from receipts
    is invisible through this interface.
    """
    receipts = state.get("receipts", [])
    return reconstruct_memory(receipts)


# ── Verification ─────────────────────────────────────────────────────


def verify_memory(state: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Hard invariant: working_memory must equal receipt-reconstructed memory.

    Returns (ok, errors).

    If this fails, something mutated working_memory outside the kernel's
    governed execution path — a constitutional violation.
    """
    reconstructed = reconstruct_memory(state.get("receipts", []))
    current = state.get("working_memory", {})

    errors: List[str] = []

    # Check for keys present in current but not reconstructable
    for key in current:
        if key not in reconstructed:
            errors.append(f"hidden key '{key}' in working_memory not derivable from receipts")

    # Check for value mismatches
    for key in reconstructed:
        if key not in current:
            errors.append(f"reconstructed key '{key}' missing from working_memory")
        elif current[key] != reconstructed[key]:
            errors.append(
                f"key '{key}' diverged: current={current[key]!r}, "
                f"reconstructed={reconstructed[key]!r}"
            )

    return len(errors) == 0, errors


def memory_provenance(
    state: Dict[str, Any],
    key: str,
) -> Optional[Dict[str, Any]]:
    """Find the receipt that last wrote to a given memory key.

    Returns the EXECUTION receipt that produced this key, or None.
    This is the proof that a memory entry exists.
    """
    receipts = state.get("receipts", [])
    result = None

    for r in receipts:
        if r.get("type") != "EXECUTION":
            continue
        if r.get("effect_status") != "APPLIED":
            continue

        proposal = r.get("proposal", {})
        action = proposal.get("action", "")
        payload = proposal.get("payload", {})
        turn = r.get("turn", 0)

        if action == "chat" and key == "last_message":
            result = r
        elif action == "memory_write" and key == f"mem_{turn}":
            result = r
        elif action == "witness" and key == f"witness_{turn}":
            result = r
        elif action == "task_create" and key == f"task_{turn}":
            result = r
        elif action == "task_update" and key == f"task_update_{turn}":
            result = r

    return result


# ── MemoryPacket (PR #2 — receipt-gated reads) ──────────────────────


@dataclass(frozen=True)
class MemoryPacket:
    """Self-contained, verifiable read packet.

    A MemoryPacket proves that specific keys have specific values,
    derived only from receipts, sealed with a Merkle root.

    READ => receipt-bound ∧ explicit ∧ verifiable.
    """
    packet_id: str
    scope: Tuple[str, ...]
    receipt_hashes: Tuple[str, ...]
    data: Dict[str, Any]
    merkle_root: str
    packet_hash: str


def _find_contributing_receipts(
    receipts: List[Dict[str, Any]], keys: List[str],
) -> List[str]:
    """Find receipt hashes that contributed to the given memory keys."""
    contributing: List[str] = []
    for r in receipts:
        if r.get("type") != "EXECUTION":
            continue
        if r.get("effect_status") != "APPLIED":
            continue

        proposal = r.get("proposal", {})
        action = proposal.get("action", "")
        turn = r.get("turn", 0)

        # Determine which key this receipt writes to
        writes_to: Optional[str] = None
        if action == "chat":
            writes_to = "last_message"
        elif action == "memory_write":
            writes_to = f"mem_{turn}"
        elif action == "witness":
            writes_to = f"witness_{turn}"
        elif action == "task_create":
            writes_to = f"task_{turn}"
        elif action == "task_update":
            writes_to = f"task_update_{turn}"

        if writes_to and writes_to in keys:
            contributing.append(r.get("hash", ""))

    return contributing


def build_memory_packet(
    receipts: List[Dict[str, Any]], keys: List[str],
) -> MemoryPacket:
    """Build a receipt-gated memory packet.

    Reconstructs data from execution receipts only.
    Seals with a Merkle root of the contributing receipt hashes.

    Args:
        receipts: Full receipt ledger.
        keys: Memory keys to include in the packet.

    Returns a frozen MemoryPacket.
    """
    from helensh.merkle import compute_hash_root

    full_mem = reconstruct_memory(receipts)
    data = {k: full_mem[k] for k in keys if k in full_mem}

    contributing = _find_contributing_receipts(receipts, keys)
    root = compute_hash_root(contributing)

    packet_id = contributing[-1][:8] if contributing else "GENESIS"

    packet_data = {
        "packet_id": packet_id,
        "scope": sorted(keys),
        "receipt_hashes": contributing,
        "data": data,
        "merkle_root": root,
    }
    packet_hash = canonical_hash(packet_data)

    return MemoryPacket(
        packet_id=packet_id,
        scope=tuple(sorted(keys)),
        receipt_hashes=tuple(contributing),
        data=data,
        merkle_root=root,
        packet_hash=packet_hash,
    )


def verify_memory_packet(packet: MemoryPacket) -> bool:
    """Stateless verification of a MemoryPacket.

    Checks:
      1. Merkle root matches contributing receipt hashes.
      2. Packet hash matches canonical content.

    No state required — anyone with the packet can verify.
    """
    from helensh.merkle import compute_hash_root

    expected_root = compute_hash_root(list(packet.receipt_hashes))
    if expected_root != packet.merkle_root:
        return False

    packet_data = {
        "packet_id": packet.packet_id,
        "scope": sorted(packet.scope),
        "receipt_hashes": list(packet.receipt_hashes),
        "data": packet.data,
        "merkle_root": packet.merkle_root,
    }
    expected_hash = canonical_hash(packet_data)
    return expected_hash == packet.packet_hash


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "reconstruct_memory",
    "disclose",
    "verify_memory",
    "memory_provenance",
    "MEMORY_MUTATING_ACTIONS",
    "MemoryPacket",
    "build_memory_packet",
    "verify_memory_packet",
]
