"""Tests for helensh/continuity.py — PR #3 Project Continuity V1 (STRICT).

Core law:
  Continuity = ledger-derived, not state-held.
  Tasks = h(R_{1..n})

Tests verify:
  - Tasks derived from receipts only (no ambient state)
  - task_create produces OPEN task
  - task_update transitions status
  - Determinism: same receipts -> same tasks
  - ContinuityPacket construction and Merkle sealing
  - Packet tamper detection
  - No task state outside ledger
  - Task provenance: audit trail from receipts
  - DENY path: revoked task_create -> no task in ledger
  - Memory integrity preserved across task operations
"""
import copy
import pytest

from helensh.kernel import init_session, step, replay, revoke_capability
from helensh.memory import reconstruct_memory, verify_memory
from helensh.continuity import (
    Task,
    TASK_STATUSES,
    derive_tasks,
    task_provenance,
    ContinuityPacket,
    build_continuity_packet,
    verify_continuity_packet,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return init_session(session_id="S-continuity-test")


@pytest.fixture
def s_with_tasks(s0):
    return replay(s0, [
        "#task design governor gate",
        "#task implement merkle sealing",
        "#task write continuity tests",
    ])


@pytest.fixture
def s_with_updates(s_with_tasks):
    return replay(s_with_tasks, [
        "#task-update T-0 IN_PROGRESS",
        "#task-update T-0 DONE",
    ])


# ── Task Derivation ─────────────────────────────────────────────────


class TestDeriveTask:
    def test_empty_receipts(self):
        assert derive_tasks([]) == {}

    def test_single_task_created(self, s0):
        s = replay(s0, ["#task design governor gate"])
        tasks = derive_tasks(s["receipts"])
        assert "T-0" in tasks
        assert tasks["T-0"].goal == "design governor gate"
        assert tasks["T-0"].status == "OPEN"

    def test_multiple_tasks(self, s_with_tasks):
        tasks = derive_tasks(s_with_tasks["receipts"])
        assert len(tasks) == 3
        assert "T-0" in tasks
        assert "T-1" in tasks
        assert "T-2" in tasks

    def test_task_ids_are_deterministic(self, s_with_tasks):
        tasks = derive_tasks(s_with_tasks["receipts"])
        assert tasks["T-0"].goal == "design governor gate"
        assert tasks["T-1"].goal == "implement merkle sealing"
        assert tasks["T-2"].goal == "write continuity tests"

    def test_all_tasks_open_initially(self, s_with_tasks):
        tasks = derive_tasks(s_with_tasks["receipts"])
        for t in tasks.values():
            assert t.status == "OPEN"

    def test_task_has_created_from(self, s_with_tasks):
        tasks = derive_tasks(s_with_tasks["receipts"])
        for t in tasks.values():
            assert len(t.created_from) == 64

    def test_task_is_frozen(self, s_with_tasks):
        tasks = derive_tasks(s_with_tasks["receipts"])
        with pytest.raises(AttributeError):
            tasks["T-0"].status = "TAMPERED"


# ── Task Updates ─────────────────────────────────────────────────────


class TestTaskUpdate:
    def test_update_changes_status(self, s_with_updates):
        tasks = derive_tasks(s_with_updates["receipts"])
        assert tasks["T-0"].status == "DONE"

    def test_update_preserves_goal(self, s_with_updates):
        tasks = derive_tasks(s_with_updates["receipts"])
        assert tasks["T-0"].goal == "design governor gate"

    def test_update_preserves_created_from(self, s_with_updates):
        tasks = derive_tasks(s_with_updates["receipts"])
        # created_from should point to original creation receipt
        assert tasks["T-0"].created_from != tasks["T-0"].updated_from

    def test_update_changes_updated_from(self, s_with_updates):
        tasks = derive_tasks(s_with_updates["receipts"])
        # updated_from should point to the last update receipt
        assert len(tasks["T-0"].updated_from) == 64

    def test_other_tasks_unaffected(self, s_with_updates):
        tasks = derive_tasks(s_with_updates["receipts"])
        assert tasks["T-1"].status == "OPEN"
        assert tasks["T-2"].status == "OPEN"

    def test_update_nonexistent_task_ignored(self, s0):
        s = replay(s0, ["#task-update T-999 DONE"])
        tasks = derive_tasks(s["receipts"])
        assert "T-999" not in tasks

    def test_intermediate_status(self, s0):
        s = replay(s0, [
            "#task build kernel",
            "#task-update T-0 IN_PROGRESS",
        ])
        tasks = derive_tasks(s["receipts"])
        assert tasks["T-0"].status == "IN_PROGRESS"

    def test_blocked_status(self, s0):
        s = replay(s0, [
            "#task external dependency",
            "#task-update T-0 BLOCKED",
        ])
        tasks = derive_tasks(s["receipts"])
        assert tasks["T-0"].status == "BLOCKED"


# ── Determinism ──────────────────────────────────────────────────────


class TestContinuityDeterminism:
    def test_same_receipts_same_tasks(self, s0):
        inputs = ["#task a", "#task b", "#task-update T-0 DONE"]
        s1 = replay(s0, inputs)
        s2 = replay(s0, inputs)
        t1 = derive_tasks(s1["receipts"])
        t2 = derive_tasks(s2["receipts"])
        assert t1 == t2

    def test_packet_determinism(self, s0):
        inputs = ["#task a", "#task b"]
        s1 = replay(s0, inputs)
        s2 = replay(s0, inputs)
        p1 = build_continuity_packet(s1["receipts"])
        p2 = build_continuity_packet(s2["receipts"])
        assert p1.packet_hash == p2.packet_hash


# ── ContinuityPacket ────────────────────────────────────────────────


class TestContinuityPacket:
    def test_returns_packet(self, s_with_tasks):
        p = build_continuity_packet(s_with_tasks["receipts"])
        assert isinstance(p, ContinuityPacket)

    def test_task_count(self, s_with_tasks):
        p = build_continuity_packet(s_with_tasks["receipts"])
        assert p.task_count == 3

    def test_open_count(self, s_with_tasks):
        p = build_continuity_packet(s_with_tasks["receipts"])
        assert p.open_count == 3

    def test_done_count_after_updates(self, s_with_updates):
        p = build_continuity_packet(s_with_updates["receipts"])
        assert p.done_count == 1
        assert p.open_count == 2

    def test_merkle_root_is_hex(self, s_with_tasks):
        p = build_continuity_packet(s_with_tasks["receipts"])
        assert len(p.merkle_root) == 64

    def test_packet_hash_is_hex(self, s_with_tasks):
        p = build_continuity_packet(s_with_tasks["receipts"])
        assert len(p.packet_hash) == 64

    def test_empty_receipts(self):
        p = build_continuity_packet([])
        assert p.task_count == 0
        assert p.tasks == {}

    def test_packet_verifies(self, s_with_tasks):
        p = build_continuity_packet(s_with_tasks["receipts"])
        assert verify_continuity_packet(p) is True

    def test_packet_after_updates_verifies(self, s_with_updates):
        p = build_continuity_packet(s_with_updates["receipts"])
        assert verify_continuity_packet(p) is True


# ── Packet Tamper Detection ──────────────────────────────────────────


class TestContinuityTamperDetection:
    def test_tampered_receipt_hash_fails(self, s_with_tasks):
        p = build_continuity_packet(s_with_tasks["receipts"])
        tampered = ContinuityPacket(
            tasks=p.tasks,
            receipt_hashes=("fake_hash",),
            merkle_root=p.merkle_root,
            packet_hash=p.packet_hash,
            task_count=p.task_count,
            open_count=p.open_count,
            done_count=p.done_count,
        )
        assert verify_continuity_packet(tampered) is False

    def test_tampered_merkle_root_fails(self, s_with_tasks):
        p = build_continuity_packet(s_with_tasks["receipts"])
        tampered = ContinuityPacket(
            tasks=p.tasks,
            receipt_hashes=p.receipt_hashes,
            merkle_root="0" * 64,
            packet_hash=p.packet_hash,
            task_count=p.task_count,
            open_count=p.open_count,
            done_count=p.done_count,
        )
        assert verify_continuity_packet(tampered) is False

    def test_tampered_task_count_fails(self, s_with_tasks):
        p = build_continuity_packet(s_with_tasks["receipts"])
        tampered = ContinuityPacket(
            tasks=p.tasks,
            receipt_hashes=p.receipt_hashes,
            merkle_root=p.merkle_root,
            packet_hash=p.packet_hash,
            task_count=999,
            open_count=p.open_count,
            done_count=p.done_count,
        )
        assert verify_continuity_packet(tampered) is False


# ── Task Provenance ─────────────────────────────────────────��────────


class TestTaskProvenance:
    def test_creation_receipt(self, s_with_tasks):
        trail = task_provenance(s_with_tasks["receipts"], "T-0")
        assert len(trail) >= 1
        assert trail[0]["proposal"]["action"] == "task_create"

    def test_update_trail(self, s_with_updates):
        trail = task_provenance(s_with_updates["receipts"], "T-0")
        assert len(trail) == 3  # 1 create + 2 updates
        assert trail[0]["proposal"]["action"] == "task_create"
        assert trail[1]["proposal"]["action"] == "task_update"
        assert trail[2]["proposal"]["action"] == "task_update"

    def test_nonexistent_task_empty_trail(self, s_with_tasks):
        trail = task_provenance(s_with_tasks["receipts"], "T-999")
        assert trail == []


# ── Governance Integration ───────────────────────────────────────────


class TestContinuityGovernance:
    def test_revoked_task_create_denied(self, s0):
        s = revoke_capability(s0, "task_create")
        s, receipt = step(s, "#task blocked goal")
        assert receipt["verdict"] == "DENY"
        tasks = derive_tasks(s["receipts"])
        assert len(tasks) == 0

    def test_revoked_task_update_denied(self, s0):
        s = replay(s0, ["#task build something"])
        s = revoke_capability(s, "task_update")
        s, receipt = step(s, "#task-update T-0 DONE")
        assert receipt["verdict"] == "DENY"
        tasks = derive_tasks(s["receipts"])
        assert tasks["T-0"].status == "OPEN"  # unchanged


# ── Memory Integration ───────────────────────────────────────────────


class TestContinuityMemoryIntegration:
    def test_memory_integrity_with_tasks(self, s_with_tasks):
        ok, errors = verify_memory(s_with_tasks)
        assert ok, f"Errors: {errors}"

    def test_memory_integrity_with_updates(self, s_with_updates):
        ok, errors = verify_memory(s_with_updates)
        assert ok, f"Errors: {errors}"

    def test_memory_reconstruction_includes_tasks(self, s_with_tasks):
        mem = reconstruct_memory(s_with_tasks["receipts"])
        assert "task_0" in mem
        assert "task_1" in mem
        assert "task_2" in mem

    def test_mixed_operations(self, s0):
        """Chat + tasks + memory writes — all operations pass."""
        s = replay(s0, [
            "hello",
            "#task build kernel",
            "#remember important note",
            "#task-update T-1 IN_PROGRESS",
            "goodbye",
        ])
        ok, errors = verify_memory(s)
        assert ok, f"Errors: {errors}"
        tasks = derive_tasks(s["receipts"])
        assert len(tasks) == 1
        assert tasks["T-1"].status == "IN_PROGRESS"
