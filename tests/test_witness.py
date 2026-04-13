"""Tests for helensh/witness.py — Witness Layer.

Tests verify:
  - WitnessRecord construction from TempleSession
  - witness_temple() receipts through the governed kernel
  - Witness data stored in working_memory as witness_{turn}
  - Witness receipt exists in receipt chain
  - verify_witness detects stored/missing witnesses
  - witness_and_run full lifecycle (brainstorm + witness)
  - Revoked capability → DENY → no witness in memory
  - Memory reconstruction includes witness data
  - Memory provenance traces witness keys
  - Base state isolation (TEMPLE sandbox never mutates state)
  - WitnessRecord is frozen and deterministic
"""
import copy
import json
import pytest
from unittest.mock import MagicMock

from helensh.kernel import init_session, step, replay, revoke_capability
from helensh.state import canonical_hash
from helensh.memory import reconstruct_memory, verify_memory, memory_provenance
from helensh.sandbox.temple import TempleSandbox, TempleSession, Claim
from helensh.agents.her_coder import HerCoder
from helensh.agents.hal_reviewer import HalReviewer, VERDICT_TO_KERNEL
from helensh.witness import (
    WitnessRecord,
    build_witness_record,
    witness_temple,
    witness_and_run,
    verify_witness,
)


# ── Agent stubs (no Ollama required) ─────────────────────────────────


def _make_her_stub(action="write_code", confidence=0.8) -> HerCoder:
    """HerCoder stub that always returns the same proposal."""
    her = MagicMock(spec=HerCoder)
    her.propose.return_value = {
        "action": action,
        "target": "test_module.py",
        "payload": {
            "description": f"Stub proposal for {action}",
            "code": "def stub(): pass",
            "rationale": "Stub",
        },
        "confidence": confidence,
        "authority": False,
        "model": "stub",
        "fallback": False,
    }
    return her


def _make_hal_stub(verdict="APPROVE", confidence=0.85) -> HalReviewer:
    """HalReviewer stub that always returns the same review."""
    hal = MagicMock(spec=HalReviewer)
    hal.review.return_value = {
        "verdict": verdict,
        "kernel_verdict": VERDICT_TO_KERNEL.get(verdict, "DENY"),
        "rationale": f"Stub review: {verdict}",
        "issues": [],
        "confidence": confidence,
        "authority": False,
        "model": "stub",
        "fallback": False,
    }
    return hal


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return init_session(session_id="S-witness-test")


@pytest.fixture
def temple_session():
    """Run a TEMPLE brainstorm with stubs and return the session."""
    her = _make_her_stub()
    hal = _make_hal_stub(verdict="APPROVE", confidence=0.9)
    temple = TempleSandbox(her, hal, approval_threshold=0.7)
    return temple.brainstorm("design a governor gate", iterations=3)


@pytest.fixture
def temple_session_rejected():
    """TEMPLE session where all claims are rejected."""
    her = _make_her_stub()
    hal = _make_hal_stub(verdict="REJECT", confidence=0.9)
    temple = TempleSandbox(her, hal, approval_threshold=0.7)
    return temple.brainstorm("rejected task", iterations=3)


# ── WitnessRecord Construction ───────────────────────────────────────


class TestBuildWitnessRecord:
    def test_returns_witness_record(self, temple_session):
        record = build_witness_record(temple_session)
        assert isinstance(record, WitnessRecord)

    def test_session_hash_matches(self, temple_session):
        record = build_witness_record(temple_session)
        assert record.session_hash == temple_session.session_hash

    def test_task_matches(self, temple_session):
        record = build_witness_record(temple_session)
        assert record.task == "design a governor gate"

    def test_iterations_matches(self, temple_session):
        record = build_witness_record(temple_session)
        assert record.iterations == 3

    def test_total_claims_matches(self, temple_session):
        record = build_witness_record(temple_session)
        assert record.total_claims == 3

    def test_eligible_count(self, temple_session):
        record = build_witness_record(temple_session)
        assert record.eligible_count == 3  # all APPROVE with confidence >= threshold

    def test_rejected_session_zero_eligible(self, temple_session_rejected):
        record = build_witness_record(temple_session_rejected)
        assert record.eligible_count == 0
        assert record.approved_summaries == ()

    def test_receipt_chain_length(self, temple_session):
        record = build_witness_record(temple_session)
        assert record.receipt_chain_length == 6  # 3 iterations * 2

    def test_witness_hash_is_hex(self, temple_session):
        record = build_witness_record(temple_session)
        assert len(record.witness_hash) == 64
        assert all(c in "0123456789abcdef" for c in record.witness_hash)

    def test_deterministic(self, temple_session):
        r1 = build_witness_record(temple_session)
        r2 = build_witness_record(temple_session)
        assert r1.witness_hash == r2.witness_hash

    def test_frozen(self, temple_session):
        record = build_witness_record(temple_session)
        with pytest.raises(AttributeError):
            record.task = "tampered"

    def test_approved_summaries_populated(self, temple_session):
        record = build_witness_record(temple_session)
        assert len(record.approved_summaries) == 3
        for s in record.approved_summaries:
            assert isinstance(s, str)
            assert len(s) > 0


# ── witness_temple ───────────────────────────────────────────────────


class TestWitnessTemple:
    def test_returns_triple(self, s0, temple_session):
        result = witness_temple(s0, temple_session)
        assert len(result) == 3
        new_state, record, receipt = result

    def test_state_has_witness_in_memory(self, s0, temple_session):
        new_state, record, receipt = witness_temple(s0, temple_session)
        wm = new_state["working_memory"]
        assert "witness_0" in wm

    def test_witness_content_contains_hash(self, s0, temple_session):
        new_state, record, receipt = witness_temple(s0, temple_session)
        content = new_state["working_memory"]["witness_0"]
        assert record.witness_hash in content

    def test_receipt_is_proposal(self, s0, temple_session):
        _, _, receipt = witness_temple(s0, temple_session)
        assert receipt["type"] == "PROPOSAL"
        assert receipt["verdict"] == "ALLOW"

    def test_receipt_action_is_witness(self, s0, temple_session):
        _, _, receipt = witness_temple(s0, temple_session)
        assert receipt["proposal"]["action"] == "witness"

    def test_authority_false(self, s0, temple_session):
        _, _, receipt = witness_temple(s0, temple_session)
        assert receipt["authority"] is False

    def test_two_receipts_appended(self, s0, temple_session):
        new_state, _, _ = witness_temple(s0, temple_session)
        assert len(new_state["receipts"]) == 2  # proposal + execution

    def test_turn_incremented(self, s0, temple_session):
        new_state, _, _ = witness_temple(s0, temple_session)
        assert new_state["turn"] == 1

    def test_base_state_not_mutated(self, s0, temple_session):
        s0_copy = copy.deepcopy(s0)
        witness_temple(s0, temple_session)
        # step() deep-copies internally, so s0 is NOT mutated
        assert s0["receipts"] == s0_copy["receipts"]
        assert s0["working_memory"] == s0_copy["working_memory"]

    def test_witness_record_matches(self, s0, temple_session):
        _, record, _ = witness_temple(s0, temple_session)
        assert record.session_hash == temple_session.session_hash
        assert record.task == temple_session.task


# ── verify_witness ───────────────────────────────────────────────────


class TestVerifyWitness:
    def test_valid_witness_passes(self, s0, temple_session):
        new_state, record, _ = witness_temple(s0, temple_session)
        ok, errors = verify_witness(new_state, record)
        assert ok, f"Errors: {errors}"

    def test_missing_witness_fails(self, s0, temple_session):
        # Verify against state that was never witnessed
        record = build_witness_record(temple_session)
        ok, errors = verify_witness(s0, record)
        assert not ok
        assert len(errors) >= 1

    def test_detects_missing_memory(self, s0, temple_session):
        new_state, record, _ = witness_temple(s0, temple_session)
        # Delete the witness from memory
        del new_state["working_memory"]["witness_0"]
        ok, errors = verify_witness(new_state, record)
        assert not ok
        assert any("not found in working_memory" in e for e in errors)

    def test_detects_missing_receipt(self, s0, temple_session):
        new_state, record, _ = witness_temple(s0, temple_session)
        # Clear receipts
        new_state["receipts"] = []
        ok, errors = verify_witness(new_state, record)
        assert not ok
        assert any("No witness PROPOSAL" in e for e in errors)


# ── witness_and_run ──────────────────────────────────────────────────


class TestWitnessAndRun:
    def test_returns_quadruple(self, s0):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.9)
        result = witness_and_run(
            s0, her, hal, "test task", iterations=2, approval_threshold=0.7,
        )
        assert len(result) == 4

    def test_full_lifecycle(self, s0):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.9)
        new_state, session, record, receipt = witness_and_run(
            s0, her, hal, "design a new gate", iterations=3,
        )
        # TEMPLE session produced
        assert isinstance(session, TempleSession)
        assert session.iterations == 3
        assert len(session.eligible_claims) == 3

        # Witness record matches
        assert record.session_hash == session.session_hash

        # Kernel receipted
        assert len(new_state["receipts"]) == 2
        assert receipt["verdict"] == "ALLOW"

        # Verify witness
        ok, errors = verify_witness(new_state, record)
        assert ok, f"Errors: {errors}"

    def test_rejected_session_still_witnessed(self, s0):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="REJECT", confidence=0.9)
        new_state, session, record, receipt = witness_and_run(
            s0, her, hal, "rejected task", iterations=2,
        )
        assert record.eligible_count == 0
        assert receipt["verdict"] == "ALLOW"  # witness itself is ALLOW
        ok, errors = verify_witness(new_state, record)
        assert ok, f"Errors: {errors}"


# ── Governance Integration ───────────────────────────────────────────


class TestWitnessGovernance:
    def test_revoked_witness_is_denied(self, s0, temple_session):
        s = revoke_capability(s0, "witness")
        new_state, record, receipt = witness_temple(s, temple_session)
        assert receipt["verdict"] == "DENY"
        # Witness should NOT be in memory
        assert "witness_0" not in new_state["working_memory"]

    def test_denied_witness_still_receipted(self, s0, temple_session):
        s = revoke_capability(s0, "witness")
        new_state, _, receipt = witness_temple(s, temple_session)
        assert len(new_state["receipts"]) == 2  # still produces receipts

    def test_witness_after_other_steps(self, s0, temple_session):
        # Run some chat steps first
        s = replay(s0, ["hello", "world"])
        new_state, record, receipt = witness_temple(s, temple_session)
        # Witness at turn 2 (after 2 chat steps)
        assert "witness_2" in new_state["working_memory"]
        assert receipt["verdict"] == "ALLOW"

    def test_multiple_witnesses(self, s0):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.9)

        # First witness
        temple1 = TempleSandbox(her, hal)
        session1 = temple1.brainstorm("task one", iterations=2)
        s1, record1, _ = witness_temple(s0, session1)

        # Second witness
        session2 = temple1.brainstorm("task two", iterations=2)
        s2, record2, _ = witness_temple(s1, session2)

        assert "witness_0" in s2["working_memory"]
        assert "witness_1" in s2["working_memory"]
        assert record1.witness_hash != record2.witness_hash


# ── Memory Integration ───────────────────────────────────────────────


class TestWitnessMemoryIntegration:
    def test_memory_reconstruction_includes_witness(self, s0, temple_session):
        new_state, record, _ = witness_temple(s0, temple_session)
        mem = reconstruct_memory(new_state["receipts"])
        assert "witness_0" in mem
        assert record.witness_hash in mem["witness_0"]

    def test_verify_memory_passes_after_witness(self, s0, temple_session):
        new_state, _, _ = witness_temple(s0, temple_session)
        ok, errors = verify_memory(new_state)
        assert ok, f"Errors: {errors}"

    def test_memory_provenance_for_witness(self, s0, temple_session):
        new_state, _, _ = witness_temple(s0, temple_session)
        receipt = memory_provenance(new_state, "witness_0")
        assert receipt is not None
        assert receipt["type"] == "EXECUTION"
        assert receipt["effect_status"] == "APPLIED"
        assert receipt["proposal"]["action"] == "witness"

    def test_mixed_operations_memory_integrity(self, s0, temple_session):
        """Chat + witness + remember — all memory operations pass."""
        s = replay(s0, ["hello"])
        s, record, _ = witness_temple(s, temple_session)
        s = replay(s, ["#remember post-witness note"])

        ok, errors = verify_memory(s)
        assert ok, f"Errors: {errors}"

        mem = reconstruct_memory(s["receipts"])
        assert "last_message" in mem    # from chat
        assert "witness_1" in mem       # from witness (turn 1)
        assert any("post-witness" in str(v) for v in mem.values())  # from remember


# ── Receipt Chain Integration ────────────────────────────────────────


class TestWitnessReceiptChain:
    def test_chain_integrity_after_witness(self, s0, temple_session):
        new_state, _, _ = witness_temple(s0, temple_session)
        receipts = new_state["receipts"]
        # Check chain links
        from helensh.kernel import GENESIS_HASH
        assert receipts[0]["previous_hash"] == GENESIS_HASH
        assert receipts[1]["previous_hash"] == receipts[0]["hash"]

    def test_replay_produces_same_state(self, s0, temple_session):
        """Witness input is deterministic and replayable."""
        record = build_witness_record(temple_session)
        from helensh.witness import _format_witness_input
        witness_input = _format_witness_input(record)

        # Step manually
        s1, _ = step(s0, witness_input)
        s2, _ = step(copy.deepcopy(s0), witness_input)

        assert s1["working_memory"] == s2["working_memory"]
        assert s1["receipts"][0]["hash"] == s2["receipts"][0]["hash"]

    def test_merkle_tree_over_witnessed_state(self, s0, temple_session):
        from helensh.merkle import MerkleTree
        new_state, _, _ = witness_temple(s0, temple_session)
        tree = MerkleTree(new_state["receipts"])
        assert tree.size == 2
        # Proof works for both receipts
        for i in range(2):
            proof = tree.get_proof(i)
            assert MerkleTree.verify_proof(tree.leaves[i], proof, tree.root)
