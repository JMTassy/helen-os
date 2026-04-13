"""Tests for helensh/memory.py — Governed Memory Disclosure.

Core law: If it cannot be reconstructed from receipts, it does not exist.

Tests verify:
  - reconstruct_memory rebuilds only from APPLIED EXECUTION receipts
  - disclose() returns only receipt-derivable memory
  - hidden state injected into working_memory is invisible via disclose()
  - verify_memory detects divergence (tampering / hidden writes)
  - deny/pending paths do NOT produce memory
  - memory_provenance traces key back to originating receipt
"""
import copy
import pytest

from helensh.kernel import (
    init_session,
    step,
    replay,
    revoke_capability,
)
from helensh.memory import (
    reconstruct_memory,
    disclose,
    verify_memory,
    memory_provenance,
)
from helensh.state import governed_state_hash


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return init_session(session_id="S-memory-test", user="tester", root="/test")


# ── Reconstruction ───────────────────────────────────────────────────


class TestReconstructMemory:
    def test_empty_receipts(self):
        assert reconstruct_memory([]) == {}

    def test_chat_reconstructs_last_message(self, s0):
        s = replay(s0, ["hello", "world"])
        mem = reconstruct_memory(s["receipts"])
        assert mem["last_message"] == "world"

    def test_memory_write_reconstructs_keyed(self, s0):
        s = replay(s0, ["#remember important thing"])
        mem = reconstruct_memory(s["receipts"])
        # Key is mem_{turn} where turn is the turn when APPLIED
        assert any("important thing" in str(v) for v in mem.values())

    def test_multiple_writes_accumulate(self, s0):
        s = replay(s0, ["#remember first", "#remember second", "#remember third"])
        mem = reconstruct_memory(s["receipts"])
        values = list(mem.values())
        assert "first" in str(values)
        assert "second" in str(values)
        assert "third" in str(values)

    def test_chat_overwrites_last_message(self, s0):
        s = replay(s0, ["alpha", "beta", "gamma"])
        mem = reconstruct_memory(s["receipts"])
        assert mem["last_message"] == "gamma"

    def test_deny_does_not_write_memory(self, s0):
        s = revoke_capability(s0, "chat")
        s, r = step(s, "hello")
        assert r["verdict"] == "DENY"
        mem = reconstruct_memory(s["receipts"])
        assert "last_message" not in mem

    def test_pending_does_not_write_memory(self, s0):
        s, r = step(s0, "#write secret content")
        assert r["verdict"] == "PENDING"
        mem = reconstruct_memory(s["receipts"])
        assert mem == {} or "last_message" not in mem

    def test_read_actions_no_memory_mutation(self, s0):
        s = replay(s0, ["#recall"])
        mem = reconstruct_memory(s["receipts"])
        # memory_read is ALLOW but produces no working_memory mutation
        assert "last_message" not in mem

    def test_ignores_proposal_receipts(self, s0):
        s = replay(s0, ["hello"])
        # Only EXECUTION receipts with APPLIED contribute
        proposals = [r for r in s["receipts"] if r["type"] == "PROPOSAL"]
        mem = reconstruct_memory(proposals)
        assert mem == {}


# ── Disclosure ───────────────────────────────────────────────────────


class TestDisclose:
    def test_disclose_matches_reconstruction(self, s0):
        s = replay(s0, ["hello", "#remember test"])
        disclosed = disclose(s)
        reconstructed = reconstruct_memory(s["receipts"])
        assert disclosed == reconstructed

    def test_hidden_state_not_visible(self, s0):
        s = replay(s0, ["hello"])
        # Inject hidden state (constitutional violation)
        s["working_memory"]["secret"] = "hidden value"
        disclosed = disclose(s)
        assert "secret" not in disclosed

    def test_disclose_empty_state(self, s0):
        disclosed = disclose(s0)
        assert disclosed == {}

    def test_disclose_shows_only_receipted_memory(self, s0):
        s = replay(s0, ["alpha"])
        # State has last_message from step
        assert s["working_memory"]["last_message"] == "alpha"
        # Disclose should show the same
        disclosed = disclose(s)
        assert disclosed["last_message"] == "alpha"


# ── Verification ─────────────────────────────────────────────────────


class TestVerifyMemory:
    def test_clean_state_passes(self, s0):
        s = replay(s0, ["hello", "#remember data"])
        ok, errors = verify_memory(s)
        assert ok, f"Errors: {errors}"
        assert errors == []

    def test_hidden_key_detected(self, s0):
        s = replay(s0, ["hello"])
        s["working_memory"]["injected"] = "hidden"
        ok, errors = verify_memory(s)
        assert not ok
        assert any("injected" in e for e in errors)

    def test_tampered_value_detected(self, s0):
        s = replay(s0, ["hello"])
        s["working_memory"]["last_message"] = "TAMPERED"
        ok, errors = verify_memory(s)
        assert not ok
        assert any("diverged" in e for e in errors)

    def test_deleted_key_detected(self, s0):
        s = replay(s0, ["hello"])
        assert "last_message" in s["working_memory"]
        del s["working_memory"]["last_message"]
        ok, errors = verify_memory(s)
        assert not ok
        assert any("missing" in e for e in errors)

    def test_genesis_state_passes(self, s0):
        ok, errors = verify_memory(s0)
        assert ok

    def test_multi_step_passes(self, s0):
        s = replay(s0, ["a", "b", "#remember c", "#recall", "d"])
        ok, errors = verify_memory(s)
        assert ok, f"Errors: {errors}"

    def test_deny_path_passes(self, s0):
        s = revoke_capability(s0, "chat")
        s, _ = step(s, "hello")
        ok, errors = verify_memory(s)
        assert ok, f"Errors: {errors}"


# ── Provenance ───────────────────────────────────────────────────────


class TestMemoryProvenance:
    def test_last_message_provenance(self, s0):
        s = replay(s0, ["hello"])
        receipt = memory_provenance(s, "last_message")
        assert receipt is not None
        assert receipt["type"] == "EXECUTION"
        assert receipt["effect_status"] == "APPLIED"

    def test_memory_write_provenance(self, s0):
        s = replay(s0, ["#remember important"])
        # The key is mem_0 (turn 0)
        receipt = memory_provenance(s, "mem_0")
        assert receipt is not None
        assert receipt["proposal"]["action"] == "memory_write"

    def test_missing_key_returns_none(self, s0):
        s = replay(s0, ["hello"])
        assert memory_provenance(s, "nonexistent") is None

    def test_provenance_returns_last_writer(self, s0):
        s = replay(s0, ["alpha", "beta"])
        receipt = memory_provenance(s, "last_message")
        assert receipt is not None
        # Should be from the "beta" step, not "alpha"
        assert receipt["proposal"]["payload"]["message"] == "beta"


# ── Integration with kernel step ─────────────────────────────────────


class TestMemoryKernelIntegration:
    def test_every_step_maintains_memory_integrity(self, s0):
        """After every step, verify_memory must pass."""
        inputs = ["hello", "#remember x", "#recall", "world", "#remember y"]
        s = copy.deepcopy(s0)
        for u in inputs:
            s, _ = step(s, u)
            ok, errors = verify_memory(s)
            assert ok, f"Memory integrity failed after '{u}': {errors}"

    def test_replayed_state_passes_verification(self, s0):
        inputs = ["a", "b", "#remember c"]
        s = replay(s0, inputs)
        ok, errors = verify_memory(s)
        assert ok, f"Errors: {errors}"

    def test_memory_reconstruction_is_deterministic(self, s0):
        inputs = ["hello", "#remember data", "world"]
        s1 = replay(s0, inputs)
        s2 = replay(s0, inputs)
        m1 = reconstruct_memory(s1["receipts"])
        m2 = reconstruct_memory(s2["receipts"])
        assert m1 == m2
