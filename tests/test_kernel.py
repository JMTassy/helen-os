"""Tests for HELENSH kernel invariants.

Invariant map:
  I1  Determinism           step(S, u) == step(S, u)
  I2  NoSilentEffect        verdict != ALLOW => footprint unchanged
  I3  ReceiptCompleteness   2 receipts per step (proposal + execution)
  I4  ChainIntegrity        previous_hash links unbroken, genesis → p1 → e1 → ...
  I5  ByteStableReplay      same inputs => same final state + same receipt hashes
  I6  AuthorityFalse        every receipt has authority == False
  I7  GovernorGates         capability revoke => DENY, write action => PENDING
  I8  StructuralAuthGuard   authority=True proposals never mutate state
  I9  ReplayVerification    rebuild_and_verify passes on valid chains
  I10 DenyPath              revoked cap => DENY => chained => no effect
"""
import copy
import pytest

from helensh.kernel import (
    GENESIS_HASH,
    KNOWN_ACTIONS,
    RECEIPT_TYPE_EXECUTION,
    RECEIPT_TYPE_PROPOSAL,
    WRITE_ACTIONS,
    cognition,
    governor,
    grant_capability,
    init_session,
    replay,
    revoke_capability,
    step,
)
from helensh.replay import (
    rebuild_and_verify,
    replay_from_receipts,
    verify_chain,
    verify_receipt_hashes,
)
from helensh.state import canonical, effect_footprint, governed_state_hash


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def s0():
    return init_session(session_id="S-test", user="tester", root="/test")


# ── I1: Determinism ──────────────────────────────────────────────────

class TestDeterminism:
    def test_single_step_determinism(self, s0):
        s1a, r1a = step(s0, "hello")
        s1b, r1b = step(s0, "hello")
        assert r1a["hash"] == r1b["hash"]
        assert s1a["receipts"] == s1b["receipts"]
        assert governed_state_hash(s1a) == governed_state_hash(s1b)

    def test_multi_step_determinism(self, s0):
        inputs = ["hello", "how are you", "#recall"]
        sa = replay(s0, inputs)
        sb = replay(s0, inputs)
        assert len(sa["receipts"]) == len(sb["receipts"])
        for a, b in zip(sa["receipts"], sb["receipts"]):
            assert a["hash"] == b["hash"]
        assert governed_state_hash(sa) == governed_state_hash(sb)


# ── I2: NoSilentEffect ──────────────────────────────────────────────

class TestNoSilentEffect:
    def test_deny_preserves_footprint(self, s0):
        s_revoked = revoke_capability(s0, "chat")
        fp_before = effect_footprint(s_revoked)
        s1, r = step(s_revoked, "hello")
        # The proposal receipt should show DENY
        assert r["verdict"] == "DENY"
        # Effect footprint must not change
        assert effect_footprint(s1)["env"] == fp_before["env"]

    def test_pending_preserves_footprint(self, s0):
        fp_before = effect_footprint(s0)
        s1, r = step(s0, "#write test content")
        assert r["verdict"] == "PENDING"
        assert effect_footprint(s1)["env"] == fp_before["env"]

    def test_allow_may_change_footprint(self, s0):
        s1, r = step(s0, "hello")
        assert r["verdict"] == "ALLOW"
        # chat updates working_memory (not env/caps footprint per se, but state changes)
        assert s1["working_memory"].get("last_message") == "hello"


# ── I3: ReceiptCompleteness ──────────────────────────────────────────

class TestReceiptCompleteness:
    def test_one_step_two_receipts(self, s0):
        s1, _ = step(s0, "hello")
        assert len(s1["receipts"]) == 2

    def test_n_steps_2n_receipts(self, s0):
        inputs = ["a", "b", "c"]
        s = replay(s0, inputs)
        assert len(s["receipts"]) == 2 * len(inputs)

    def test_receipt_types_alternate(self, s0):
        s1, _ = step(s0, "hello")
        assert s1["receipts"][0]["type"] == RECEIPT_TYPE_PROPOSAL
        assert s1["receipts"][1]["type"] == RECEIPT_TYPE_EXECUTION

    def test_multi_step_alternation(self, s0):
        s = replay(s0, ["a", "b", "c"])
        for i, r in enumerate(s["receipts"]):
            expected = RECEIPT_TYPE_PROPOSAL if i % 2 == 0 else RECEIPT_TYPE_EXECUTION
            assert r["type"] == expected, f"receipt[{i}] type={r['type']}, expected={expected}"


# ── I4: ChainIntegrity ──────────────────────────────────────────────

class TestChainIntegrity:
    def test_genesis_link(self, s0):
        s1, _ = step(s0, "hello")
        assert s1["receipts"][0]["previous_hash"] == GENESIS_HASH

    def test_proposal_to_execution_link(self, s0):
        s1, _ = step(s0, "hello")
        p_receipt = s1["receipts"][0]
        e_receipt = s1["receipts"][1]
        assert e_receipt["previous_hash"] == p_receipt["hash"]

    def test_multi_step_chain(self, s0):
        s = replay(s0, ["a", "b", "c"])
        ok, errors = verify_chain(s["receipts"])
        assert ok, f"Chain errors: {errors}"

    def test_chain_across_steps(self, s0):
        s = replay(s0, ["x", "y"])
        # e1 → p2 link
        e1 = s["receipts"][1]  # first execution receipt
        p2 = s["receipts"][2]  # second proposal receipt
        assert p2["previous_hash"] == e1["hash"]


# ── I5: ByteStableReplay ─────────────────────────────────────────────

class TestByteStableReplay:
    def test_replay_matches_sequential(self, s0):
        inputs = ["hello", "world", "#recall"]
        # Sequential
        s = copy.deepcopy(s0)
        for u in inputs:
            s, _ = step(s, u)
        # Replay
        sr = replay(s0, inputs)
        # Must match
        assert len(s["receipts"]) == len(sr["receipts"])
        for a, b in zip(s["receipts"], sr["receipts"]):
            assert a["hash"] == b["hash"]

    def test_replay_from_receipts_matches(self, s0):
        inputs = ["hello", "world"]
        s = replay(s0, inputs)
        sr = replay_from_receipts(s0, s["receipts"])
        assert governed_state_hash(s) == governed_state_hash(sr)


# ── I6: AuthorityFalse ───────────────────────────────────────────────

class TestAuthorityFalse:
    def test_all_receipts_authority_false(self, s0):
        s = replay(s0, ["a", "b", "#write c", "#recall"])
        for i, r in enumerate(s["receipts"]):
            assert r["authority"] is False, f"receipt[{i}] authority={r['authority']}"


# ── I7: GovernorGates ─────────────────────────────────────────────────

class TestGovernorGates:
    def test_unknown_action_deny(self, s0):
        proposal = {"action": "destroy_world", "payload": {}, "authority": False}
        assert governor(proposal, s0) == "DENY"

    def test_authority_claim_deny(self, s0):
        proposal = {"action": "chat", "payload": {}, "authority": True}
        assert governor(proposal, s0) == "DENY"

    def test_missing_capability_deny(self, s0):
        s = revoke_capability(s0, "chat")
        proposal = {"action": "chat", "payload": {}, "authority": False}
        assert governor(proposal, s) == "DENY"

    def test_write_action_pending(self, s0):
        for action in WRITE_ACTIONS:
            proposal = {"action": action, "payload": {}, "authority": False}
            assert governor(proposal, s0) == "PENDING", f"{action} should be PENDING"

    def test_normal_action_allow(self, s0):
        from helensh.kernel import GATED_ACTIONS
        for action in KNOWN_ACTIONS - WRITE_ACTIONS - GATED_ACTIONS:
            proposal = {"action": action, "payload": {}, "authority": False}
            assert governor(proposal, s0) == "ALLOW", f"{action} should be ALLOW"


# ── I8: StructuralAuthorityGuard ──────────────────────────────────────

class TestStructuralAuthorityGuard:
    def test_authority_proposal_no_mutation(self, s0):
        # Force a proposal with authority=True
        proposal = {"action": "chat", "payload": {"message": "hi"}, "authority": True}
        fp_before = effect_footprint(s0)
        from helensh.kernel import apply_receipt
        s1 = apply_receipt(copy.deepcopy(s0), proposal, "ALLOW")
        assert effect_footprint(s1) == fp_before


# ── I9: ReplayVerification ────────────────────────────────────────────

class TestReplayVerification:
    def test_rebuild_and_verify_passes(self, s0):
        s = replay(s0, ["hello", "world", "#recall"])
        ok, errors = rebuild_and_verify(s0, s["receipts"])
        assert ok, f"Rebuild errors: {errors}"

    def test_verify_receipt_hashes(self, s0):
        s = replay(s0, ["a", "b"])
        ok, errors = verify_receipt_hashes(s["receipts"])
        assert ok, f"Hash errors: {errors}"

    def test_tampered_receipt_detected(self, s0):
        s = replay(s0, ["hello"])
        s["receipts"][0]["verdict"] = "TAMPERED"
        ok, errors = verify_receipt_hashes(s["receipts"])
        assert not ok


# ── I10: DenyPath ─────────────────────────────────────────────────────

class TestDenyPath:
    def test_deny_chained_no_effect(self, s0):
        """Revoke capability → step → DENY → receipt chained → no env change."""
        s = revoke_capability(s0, "chat")
        fp_before = effect_footprint(s)
        s1, r = step(s, "hello")

        # Verdict is DENY
        assert r["verdict"] == "DENY"

        # Chain is intact
        ok, errors = verify_chain(s1["receipts"])
        assert ok, f"Chain errors: {errors}"

        # No effect
        assert effect_footprint(s1)["env"] == fp_before["env"]

        # Execution receipt shows DENIED
        e_receipt = s1["receipts"][1]
        assert e_receipt["effect_status"] == "DENIED"

    def test_mixed_allow_deny_pending(self, s0):
        """Multiple steps with different verdicts, all chained correctly."""
        s = copy.deepcopy(s0)

        # Step 1: ALLOW (chat)
        s, r1 = step(s, "hello")
        assert r1["verdict"] == "ALLOW"

        # Step 2: DENY (revoked capability)
        s = revoke_capability(s, "search")
        s, r2 = step(s, "#search test")
        assert r2["verdict"] == "DENY"

        # Step 3: PENDING (write action)
        s = grant_capability(s, "search")  # restore for later
        s, r3 = step(s, "#write something")
        assert r3["verdict"] == "PENDING"

        # Chain intact across all 6 receipts
        ok, errors = verify_chain(s["receipts"])
        assert ok, f"Chain errors: {errors}"


# ── Cognition tests ──────────────────────────────────────────────────

class TestCognition:
    def test_default_is_chat(self, s0):
        p = cognition(s0, "hello world")
        assert p["action"] == "chat"
        assert p["authority"] is False

    def test_read_prefix(self, s0):
        p = cognition(s0, "#read /etc/hosts")
        assert p["action"] == "read_file"

    def test_write_prefix(self, s0):
        p = cognition(s0, "#write test content")
        assert p["action"] == "write_file"

    def test_run_prefix(self, s0):
        p = cognition(s0, "#run ls -la")
        assert p["action"] == "run_command"

    def test_remember_prefix(self, s0):
        p = cognition(s0, "#remember important thing")
        assert p["action"] == "memory_write"

    def test_recall_prefix(self, s0):
        p = cognition(s0, "#recall")
        assert p["action"] == "memory_read"

    def test_empty_input(self, s0):
        p = cognition(s0, "")
        assert p["action"] == "chat"
        assert p["authority"] is False


# ── State utilities tests ─────────────────────────────────────────────

class TestStateUtilities:
    def test_canonical_deterministic(self):
        a = canonical({"b": 1, "a": 2})
        b = canonical({"a": 2, "b": 1})
        assert a == b

    def test_governed_state_hash_stable(self, s0):
        h1 = governed_state_hash(s0)
        h2 = governed_state_hash(s0)
        assert h1 == h2

    def test_init_session_defaults(self):
        s = init_session()
        assert s["turn"] == 0
        assert s["receipts"] == []
        assert s["history"] == []
        assert len(s["capabilities"]) == len(KNOWN_ACTIONS)
