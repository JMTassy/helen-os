"""HELEN OS — Project Continuity V1.

Continuity = ledger-derived, not state-held.

Tasks are reconstructed from receipts, never stored in ambient state.
No implicit goals. No hidden tasks.

    Tasks = h(R_{1..n})

Architecture:
  receipts -> derive_tasks() -> {task_id: Task}
  receipts -> build_continuity_packet() -> ContinuityPacket (Merkle-sealed)

Every task traces back to its creation receipt and last update receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical, canonical_hash
from helensh.merkle import compute_hash_root, EMPTY_LEDGER_HASH


# ── Constants ─────────────────────────────────────────────────────────

TASK_STATUSES = frozenset({"OPEN", "IN_PROGRESS", "DONE", "BLOCKED"})


# ── Task Primitive ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Task:
    """A single tracked task, derived from receipts.

    Fields:
      task_id      — deterministic ID (T-{turn} at creation)
      goal         — human-readable objective
      status       — OPEN | IN_PROGRESS | DONE | BLOCKED
      created_from — receipt hash of the task_create execution receipt
      updated_from — receipt hash of the last mutation receipt
    """
    task_id: str
    goal: str
    status: str
    created_from: str
    updated_from: str


# ── Derivation (from receipts ONLY) ─────────────────────────────────


def derive_tasks(receipts: List[Dict[str, Any]]) -> Dict[str, Task]:
    """Reconstruct task state strictly from execution receipts.

    Only EXECUTION receipts with effect_status == "APPLIED" contribute.
    task_create adds a new task (OPEN).
    task_update mutates an existing task's status.

    No state dependency. No ambient memory. Pure function of ledger.
    """
    tasks: Dict[str, Task] = {}

    for r in receipts:
        if r.get("type") != "EXECUTION":
            continue
        if r.get("effect_status") != "APPLIED":
            continue

        proposal = r.get("proposal", {})
        action = proposal.get("action", "")
        payload = proposal.get("payload", {})
        receipt_hash = r.get("hash", "")

        if action == "task_create":
            task_id = payload.get("task_id", "")
            goal = payload.get("goal", "")
            if task_id:
                tasks[task_id] = Task(
                    task_id=task_id,
                    goal=goal,
                    status="OPEN",
                    created_from=receipt_hash,
                    updated_from=receipt_hash,
                )

        elif action == "task_update":
            task_id = payload.get("task_id", "")
            status = payload.get("status", "")
            if task_id in tasks and status:
                old = tasks[task_id]
                tasks[task_id] = Task(
                    task_id=old.task_id,
                    goal=old.goal,
                    status=status,
                    created_from=old.created_from,
                    updated_from=receipt_hash,
                )

    return tasks


def task_provenance(
    receipts: List[Dict[str, Any]], task_id: str,
) -> List[Dict[str, Any]]:
    """Find all receipts that affected a given task.

    Returns the full audit trail: creation + all updates, in order.
    """
    trail: List[Dict[str, Any]] = []

    for r in receipts:
        if r.get("type") != "EXECUTION":
            continue
        if r.get("effect_status") != "APPLIED":
            continue

        proposal = r.get("proposal", {})
        action = proposal.get("action", "")
        payload = proposal.get("payload", {})

        if action in ("task_create", "task_update"):
            if payload.get("task_id") == task_id:
                trail.append(r)

    return trail


# ── ContinuityPacket ────────────────────────────────────────────────


@dataclass(frozen=True)
class ContinuityPacket:
    """Self-contained, verifiable snapshot of project continuity.

    Derived from receipts. Sealed with Merkle root.
    No state dependency — anyone can verify.
    """
    tasks: Dict[str, Task]
    receipt_hashes: Tuple[str, ...]
    merkle_root: str
    packet_hash: str
    task_count: int
    open_count: int
    done_count: int


def build_continuity_packet(
    receipts: List[Dict[str, Any]],
) -> ContinuityPacket:
    """Build a Merkle-sealed continuity packet from receipts.

    Reconstructs all tasks, seals with Merkle root of ALL receipt hashes.
    """
    tasks = derive_tasks(receipts)
    hashes = [r.get("hash", "") for r in receipts]
    root = compute_hash_root(hashes) if hashes else EMPTY_LEDGER_HASH

    open_count = sum(1 for t in tasks.values() if t.status == "OPEN")
    done_count = sum(1 for t in tasks.values() if t.status == "DONE")

    # Serialize tasks for hashing (Task -> dict)
    tasks_serializable = {
        tid: {
            "task_id": t.task_id,
            "goal": t.goal,
            "status": t.status,
            "created_from": t.created_from,
            "updated_from": t.updated_from,
        }
        for tid, t in tasks.items()
    }

    packet_data = {
        "tasks": tasks_serializable,
        "receipt_hashes": hashes,
        "merkle_root": root,
        "task_count": len(tasks),
        "open_count": open_count,
        "done_count": done_count,
    }
    packet_hash = canonical_hash(packet_data)

    return ContinuityPacket(
        tasks=tasks,
        receipt_hashes=tuple(hashes),
        merkle_root=root,
        packet_hash=packet_hash,
        task_count=len(tasks),
        open_count=open_count,
        done_count=done_count,
    )


def verify_continuity_packet(packet: ContinuityPacket) -> bool:
    """Stateless verification of a ContinuityPacket.

    Checks: Merkle root matches receipt hashes, packet hash matches content.
    """
    expected_root = compute_hash_root(list(packet.receipt_hashes))
    if expected_root != packet.merkle_root:
        return False

    tasks_serializable = {
        tid: {
            "task_id": t.task_id,
            "goal": t.goal,
            "status": t.status,
            "created_from": t.created_from,
            "updated_from": t.updated_from,
        }
        for tid, t in packet.tasks.items()
    }

    packet_data = {
        "tasks": tasks_serializable,
        "receipt_hashes": list(packet.receipt_hashes),
        "merkle_root": packet.merkle_root,
        "task_count": packet.task_count,
        "open_count": packet.open_count,
        "done_count": packet.done_count,
    }
    expected_hash = canonical_hash(packet_data)
    return expected_hash == packet.packet_hash


# ── Exports ─────────────────────────────────────────────────────────

__all__ = [
    "Task",
    "TASK_STATUSES",
    "derive_tasks",
    "task_provenance",
    "ContinuityPacket",
    "build_continuity_packet",
    "verify_continuity_packet",
]
