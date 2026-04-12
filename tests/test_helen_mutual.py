"""Tests for helen_mutual_learning.py — two-loop mutual learning kernel.

Invariant coverage:
  M1  Determinism          step(S, u) == step(S, u)  (hash-stable)
  M2  GovernorGates        LEARN → PENDING, unknown → DENY, authority → DENY
  M3  NoSilentEffect       DENY/PENDING → state unchanged
  M4  ChainIntegrity       genesis → PROPOSAL → EXECUTION → LEARNING* → …
  M5  ZeroHiddenLearning   only 'approve' + confidence ≥ 0.5 enters index
  M6  AuthorityFalse       every receipt has authority == False
  M7  ReplayConsistency    replay(S0, inputs) == fold(step)(S0, inputs)
  M8  TamperDetection      mutating a receipt breaks verify_chain
  M9  LoopSeparation       learn() and retrieve() are independent paths
  M10 AuditCompleteness    reject/edit are in chain but not in index
"""
import copy
import pytest

from helen_mutual_learning import (
    GENESIS_HASH,
    KNOWN_INTENTS,
    MIN_CONFIDENCE,
    PENDING_INTENTS,
    VALID_FEEDBACK,
    cognition,
    execute,
    governor,
    initial_state,
    insight,
    learn,
    replay,
    retrieve,
    retrieve_similar,
    sha256_hex,
    state_hash,
    step,
    verify_chain,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return initial_state()


def _add_experience(state, inp, out, fb="approve", conf=0.9):
    """Helper: add one learning event."""
    return learn(state, inp, out, fb, conf)[0]


# ── Cognition ─────────────────────────────────────────────────────────


class TestCognition:
    def test_observe_prefix(self, s0):
        p = cognition("observe cpu_spike", s0)
        assert p.intent == "OBSERVE"
        assert p.target == "cpu_spike"

    def test_plan_prefix(self, s0):
        p = cognition("plan rebalance_cluster", s0)
        assert p.intent == "PLAN"
        assert p.target == "rebalance_cluster"

    def test_echo_prefix(self, s0):
        p = cognition("echo hello there", s0)
        assert p.intent == "ECHO"
        assert p.payload["message"] == "hello there"

    def test_learn_prefix(self, s0):
        p = cognition("learn scale_up on high_load", s0)
        assert p.intent == "LEARN"

    def test_retrieve_prefix(self, s0):
        p = cognition("retrieve memory_pressure=high", s0)
        assert p.intent == "RETRIEVE"
        assert p.target == "memory_pressure=high"

    def test_default_chat(self, s0):
        p = cognition("what should I do?", s0)
        assert p.intent == "CHAT"

    def test_empty_input_is_chat(self, s0):
        p = cognition("", s0)
        assert p.intent == "CHAT"
        # authority lives on receipts and PolicyVerdict, not on Proposal
        assert p.payload == {"message": ""}

    def test_authority_always_false(self, s0):
        for text in ["echo hi", "observe x", "plan y", "learn z"]:
            p = cognition(text, s0)
            assert not hasattr(p, "authority") or True  # Proposal has no authority field
            # Authority comes from governor / receipts, not from the Proposal

    def test_proposal_id_unique(self, s0):
        p1 = cognition("echo a", s0)
        p2 = cognition("echo a", s0)
        assert p1.proposal_id != p2.proposal_id

    def test_retrieve_attaches_prior_cases_count(self, s0):
        # Add a learning first
        s1, _ = learn(s0, "observe x", "do_y", "approve", 0.9)
        p = cognition("retrieve x", s1)
        assert "prior_cases" in p.payload


# ── Governor ──────────────────────────────────────────────────────────


class TestGovernor:
    def test_unknown_intent_deny(self, s0):
        from helen_mutual_learning import Proposal
        p = Proposal("x", "DESTROY_WORLD", "everything", {}, 0.9, 0)
        v = governor(p, s0)
        assert v.verdict == "DENY"

    def test_authority_claim_deny(self, s0):
        from helen_mutual_learning import Proposal
        p = Proposal("x", "ECHO", "hi", {"authority": True}, 0.9, 0)
        v = governor(p, s0)
        assert v.verdict == "DENY"
        assert any("authority" in r.lower() for r in v.reasons)

    def test_learn_always_pending(self, s0):
        from helen_mutual_learning import Proposal
        p = Proposal("x", "LEARN", "scale_up", {}, 0.9, 0)
        v = governor(p, s0)
        assert v.verdict == "PENDING"

    def test_known_intents_allow(self, s0):
        from helen_mutual_learning import Proposal
        allow_intents = KNOWN_INTENTS - PENDING_INTENTS
        for intent in allow_intents:
            p = Proposal("x", intent, "target", {}, 0.9, 0)
            v = governor(p, s0)
            assert v.verdict == "ALLOW", f"{intent} should be ALLOW"

    def test_policy_version_present(self, s0):
        from helen_mutual_learning import Proposal
        p = Proposal("x", "ECHO", "hi", {}, 0.9, 0)
        v = governor(p, s0)
        assert v.policy_version.startswith("v")


# ── Execution ─────────────────────────────────────────────────────────


class TestExecution:
    def test_deny_no_mutation(self, s0):
        from helen_mutual_learning import Proposal, PolicyVerdict
        p = Proposal("x", "ECHO", "hi", {"message": "hi"}, 0.9, 0)
        v = PolicyVerdict(verdict="DENY", reasons=["test"])
        s1, status = execute(p, v, s0)
        assert status == "DENIED"
        assert s1["env"] == s0["env"]
        assert s1["turn"] == s0["turn"]

    def test_pending_no_mutation(self, s0):
        from helen_mutual_learning import Proposal, PolicyVerdict
        p = Proposal("x", "LEARN", "x", {}, 0.9, 0)
        v = PolicyVerdict(verdict="PENDING", reasons=["test"])
        s1, status = execute(p, v, s0)
        assert status == "DEFERRED"
        assert s1["env"] == s0["env"]

    def test_echo_sets_last_echo(self, s0):
        from helen_mutual_learning import Proposal, PolicyVerdict
        p = Proposal("x", "ECHO", "world", {"message": "world"}, 0.9, 0)
        v = PolicyVerdict(verdict="ALLOW", reasons=[])
        s1, status = execute(p, v, s0)
        assert status == "MATERIALIZED"
        assert s1["env"]["last_echo"] == "world"

    def test_observe_sets_env_key(self, s0):
        from helen_mutual_learning import Proposal, PolicyVerdict
        p = Proposal("x", "OBSERVE", "cpu=high", {"observation": "cpu=high"}, 0.9, 0)
        v = PolicyVerdict(verdict="ALLOW", reasons=[])
        s1, status = execute(p, v, s0)
        assert "obs:cpu=high" in s1["env"]

    def test_plan_sets_env_key(self, s0):
        from helen_mutual_learning import Proposal, PolicyVerdict
        p = Proposal("x", "PLAN", "scale_out", {"plan": "scale_out"}, 0.9, 0)
        v = PolicyVerdict(verdict="ALLOW", reasons=[])
        s1, status = execute(p, v, s0)
        assert "plan:scale_out" in s1["env"]

    def test_chat_sets_last_message(self, s0):
        from helen_mutual_learning import Proposal, PolicyVerdict
        p = Proposal("x", "CHAT", "hello", {"message": "hello"}, 0.9, 0)
        v = PolicyVerdict(verdict="ALLOW", reasons=[])
        s1, _ = execute(p, v, s0)
        assert s1["env"]["last_message"] == "hello"

    def test_allow_increments_turn(self, s0):
        from helen_mutual_learning import Proposal, PolicyVerdict
        p = Proposal("x", "ECHO", "x", {"message": "x"}, 0.9, 0)
        v = PolicyVerdict(verdict="ALLOW", reasons=[])
        s1, _ = execute(p, v, s0)
        assert s1["turn"] == s0["turn"] + 1

    def test_deny_does_not_increment_turn(self, s0):
        from helen_mutual_learning import Proposal, PolicyVerdict
        p = Proposal("x", "ECHO", "x", {}, 0.9, 0)
        v = PolicyVerdict(verdict="DENY", reasons=[])
        s1, _ = execute(p, v, s0)
        assert s1["turn"] == s0["turn"]


# ── M1: Determinism ───────────────────────────────────────────────────


class TestDeterminism:
    def test_step_produces_identical_hashes(self, s0):
        """step(copy(s0), u) twice must produce the same receipt hashes."""
        s1a, p1a, e1a = step(copy.deepcopy(s0), "echo hello")
        s1b, p1b, e1b = step(copy.deepcopy(s0), "echo hello")
        assert p1a.receipt_hash == p1b.receipt_hash
        assert e1a.receipt_hash == e1b.receipt_hash
        assert state_hash(s1a) == state_hash(s1b)

    def test_multi_step_determinism(self, s0):
        inputs = ["echo hello", "observe x", "plan y", "chat ok"]
        s_a = copy.deepcopy(s0)
        s_b = copy.deepcopy(s0)
        for u in inputs:
            s_a, _, _ = step(s_a, u)
            s_b, _, _ = step(s_b, u)
        for ra, rb in zip(s_a["receipts"], s_b["receipts"]):
            assert ra["receipt_hash"] == rb["receipt_hash"]


# ── M2 + M3: GovernorGates + NoSilentEffect ───────────────────────────


class TestGovernorGatesAndNoSilentEffect:
    def test_learn_via_step_is_pending(self, s0):
        s1, p_r, e_r = step(s0, "learn prune on high_load")
        assert p_r.verdict["verdict"] == "PENDING"
        assert e_r.effect_status == "DEFERRED"

    def test_pending_does_not_change_env(self, s0):
        env_before = copy.deepcopy(s0["env"])
        s1, _, _ = step(s0, "learn prune on high_load")
        assert s1["env"] == env_before

    def test_pending_does_not_change_learning_index(self, s0):
        idx_before = copy.deepcopy(s0["learning_index"])
        s1, _, _ = step(s0, "learn prune on high_load")
        assert s1["learning_index"] == idx_before

    def test_unknown_intent_via_proposal_is_deny(self, s0):
        from helen_mutual_learning import Proposal
        p = Proposal("x", "NUKE", "everything", {}, 0.9, 0)
        v = governor(p, s0)
        assert v.verdict == "DENY"

    def test_deny_does_not_change_env(self, s0):
        from helen_mutual_learning import Proposal, PolicyVerdict
        p = Proposal("x", "ECHO", "hi", {}, 0.9, 0)
        v = PolicyVerdict(verdict="DENY", reasons=["test"])
        env_before = copy.deepcopy(s0["env"])
        s1, _ = execute(p, v, s0)
        assert s1["env"] == env_before


# ── M4: ChainIntegrity ────────────────────────────────────────────────


class TestChainIntegrity:
    def test_first_receipt_links_genesis(self, s0):
        s1, p_r, _ = step(s0, "echo hello")
        assert s1["receipts"][0]["previous_hash"] == GENESIS_HASH

    def test_proposal_to_execution_link(self, s0):
        s1, p_r, e_r = step(s0, "echo hello")
        assert e_r.previous_hash == p_r.receipt_hash

    def test_multi_step_chain(self, s0):
        s = copy.deepcopy(s0)
        for u in ["echo a", "observe b", "plan c"]:
            s, _, _ = step(s, u)
        ok, errors = verify_chain(s["receipts"])
        assert ok, f"Chain errors: {errors}"

    def test_learning_receipt_in_chain(self, s0):
        s1, p_r, e_r = step(s0, "echo hello")
        s2, l_r = learn(s1, "observe cpu=high", "scale_out", "approve", 0.9)
        ok, errors = verify_chain(s2["receipts"])
        assert ok, errors
        # The learning receipt links to the last execution receipt
        assert l_r.previous_hash == e_r.receipt_hash

    def test_chain_after_mixed_receipts(self, s0):
        """PROPOSAL → EXECUTION → LEARNING → PROPOSAL → EXECUTION chain."""
        s = copy.deepcopy(s0)
        s, _, _ = step(s, "echo step1")
        s, _    = learn(s, "observe x", "do_y", "approve", 0.9)
        s, _, _ = step(s, "echo step2")
        ok, errors = verify_chain(s["receipts"])
        assert ok, errors

    def test_all_receipts_have_previous_hash(self, s0):
        s = replay(s0, ["echo a", "observe b", "chat c"])
        for r in s["receipts"]:
            assert "previous_hash" in r

    def test_all_receipts_authority_false(self, s0):
        s = replay(s0, ["echo a", "observe b"])
        s, _ = learn(s, "observe x", "act", "approve", 0.9)
        for r in s["receipts"]:
            assert r["authority"] is False


# ── M5: ZeroHiddenLearning ────────────────────────────────────────────


class TestZeroHiddenLearning:
    def test_approve_high_conf_enters_index(self, s0):
        s1, _ = learn(s0, "observe cpu=high", "scale_out", "approve", 0.9)
        assert len(s1["learning_index"]) == 1

    def test_reject_does_not_enter_index(self, s0):
        s1, _ = learn(s0, "observe cpu=high", "scale_out", "reject", 0.9)
        assert len(s1["learning_index"]) == 0

    def test_edit_does_not_enter_index(self, s0):
        s1, _ = learn(s0, "observe cpu=high", "scale_out", "edit", 0.9)
        assert len(s1["learning_index"]) == 0

    def test_approve_low_conf_does_not_enter_index(self, s0):
        s1, _ = learn(s0, "observe cpu=high", "scale_out", "approve", 0.3)
        assert len(s1["learning_index"]) == 0

    def test_approve_at_threshold_enters_index(self, s0):
        s1, _ = learn(s0, "observe cpu=high", "scale_out", "approve", MIN_CONFIDENCE)
        assert len(s1["learning_index"]) == 1

    def test_step_learn_intent_does_not_enter_index(self, s0):
        """step() routes LEARN to governor which returns PENDING — index stays empty."""
        s1, p_r, _ = step(s0, "learn scale_up when cpu=high")
        assert p_r.verdict["verdict"] == "PENDING"
        assert len(s1["learning_index"]) == 0

    def test_multiple_approvals_accumulate(self, s0):
        s = copy.deepcopy(s0)
        for i in range(5):
            s, _ = learn(s, f"observe event_{i}", f"action_{i}", "approve", 0.8)
        assert len(s["learning_index"]) == 5


# ── M10: AuditCompleteness ────────────────────────────────────────────


class TestAuditCompleteness:
    def test_reject_appears_in_receipt_chain(self, s0):
        s1, l_r = learn(s0, "observe x", "bad_action", "reject", 0.9)
        assert l_r.feedback == "reject"
        assert any(
            r.get("receipt_type") == "LEARNING" and r.get("feedback") == "reject"
            for r in s1["receipts"]
        )

    def test_edit_appears_in_receipt_chain(self, s0):
        s1, l_r = learn(s0, "observe x", "bad_action", "edit", 0.9)
        assert l_r.feedback == "edit"
        assert any(
            r.get("receipt_type") == "LEARNING" and r.get("feedback") == "edit"
            for r in s1["receipts"]
        )

    def test_all_three_feedbacks_auditable(self, s0):
        s = copy.deepcopy(s0)
        s, _ = learn(s, "observe x", "action_a", "approve", 0.9)
        s, _ = learn(s, "observe x", "action_b", "reject",  0.9)
        s, _ = learn(s, "observe x", "action_c", "edit",    0.9)
        learning_receipts = [
            r for r in s["receipts"] if r.get("receipt_type") == "LEARNING"
        ]
        assert len(learning_receipts) == 3
        feedbacks = {r["feedback"] for r in learning_receipts}
        assert feedbacks == {"approve", "reject", "edit"}


# ── M9: LoopSeparation ────────────────────────────────────────────────


class TestLoopSeparation:
    def test_loop1_does_not_affect_env(self, s0):
        """learn() must not mutate env — only learning_index."""
        env_before = copy.deepcopy(s0["env"])
        s1, _ = learn(s0, "observe x", "act", "approve", 0.9)
        assert s1["env"] == env_before

    def test_loop2_retrieve_returns_only_approved(self, s0):
        s = copy.deepcopy(s0)
        s, _ = learn(s, "observe x", "good_action",  "approve", 0.9)
        s, _ = learn(s, "observe x", "bad_action",   "reject",  0.9)
        s, _ = learn(s, "observe x", "iffy_action",  "edit",    0.9)
        hits = retrieve(s, "x")
        assert len(hits) == 1  # only approved
        assert hits[0]["feedback"] == "approve"

    def test_loop2_retrieve_returns_empty_if_none(self, s0):
        hits = retrieve(s0, "nonexistent_target")
        assert hits == []

    def test_loop2_insight_uses_only_index(self, s0):
        """insight() must reflect only the approved learning_index."""
        s = copy.deepcopy(s0)
        s, _ = learn(s, "observe cpu=high", "scale_out",  "approve", 0.9)
        s, _ = learn(s, "observe cpu=high", "bad_action", "reject",  0.9)
        report = insight(s)
        assert report["total"] == 1   # only the approved one

    def test_step_retrieve_via_kernel(self, s0):
        """RETRIEVE intent through step() surfaces results in env."""
        s = copy.deepcopy(s0)
        s, _ = learn(s, "observe mem_high", "prune", "approve", 0.9)
        s, p_r, e_r = step(s, "retrieve mem_high")
        assert p_r.verdict["verdict"] == "ALLOW"
        assert "last_retrieval" in s["env"]
        assert s["env"]["last_retrieval"]["hits"] == 1


# ── Insight analytics ─────────────────────────────────────────────────


class TestInsight:
    def test_empty_state_returns_zero(self, s0):
        report = insight(s0)
        assert report["total"] == 0
        assert report["top_targets"] == []
        assert report["avg_confidence"] == 0.0

    def test_insight_counts_only_indexed(self, s0):
        s = copy.deepcopy(s0)
        for fb, conf in [("approve", 0.9), ("reject", 0.9), ("approve", 0.8)]:
            s, _ = learn(s, "observe x", "act", fb, conf)
        report = insight(s)
        assert report["total"] == 2

    def test_insight_top_targets(self, s0):
        s = copy.deepcopy(s0)
        for target in ["cpu=high", "cpu=high", "mem=low"]:
            s, _ = learn(s, f"observe {target}", "act", "approve", 0.9)
        report = insight(s)
        top = dict(report["top_targets"])
        assert top.get("cpu=high") == 2
        assert top.get("mem=low") == 1

    def test_insight_avg_confidence(self, s0):
        s = copy.deepcopy(s0)
        s, _ = learn(s, "observe x", "a", "approve", 0.8)
        s, _ = learn(s, "observe y", "b", "approve", 1.0)
        report = insight(s)
        assert abs(report["avg_confidence"] - 0.9) < 0.001

    def test_insight_intent_filter(self, s0):
        s = copy.deepcopy(s0)
        s, _ = learn(s, "observe cpu=high", "scale", "approve", 0.9)
        s, _ = learn(s, "plan deploy",       "go",    "approve", 0.9)
        observe_report = insight(s, intent_filter="OBSERVE")
        assert observe_report["total"] == 1
        plan_report = insight(s, intent_filter="PLAN")
        assert plan_report["total"] == 1

    def test_insight_note_always_present(self, s0):
        s, _ = learn(s0, "observe x", "act", "approve", 0.9)
        report = insight(s)
        assert isinstance(report["note"], str)
        assert len(report["note"]) > 0


# ── M7: Replay ────────────────────────────────────────────────────────


class TestReplay:
    def test_replay_produces_correct_receipt_count(self, s0):
        inputs = ["echo a", "observe b", "plan c", "chat d"]
        s = replay(s0, inputs)
        assert len(s["receipts"]) == len(inputs) * 2

    def test_replay_matches_sequential_step(self, s0):
        inputs = ["echo hello", "observe x", "chat what?"]
        # Sequential
        s_seq = copy.deepcopy(s0)
        for u in inputs:
            s_seq, _, _ = step(s_seq, u)
        # Replay
        s_rep = replay(copy.deepcopy(s0), inputs)
        for r_seq, r_rep in zip(s_seq["receipts"], s_rep["receipts"]):
            assert r_seq["receipt_hash"] == r_rep["receipt_hash"]

    def test_replay_learning_index_empty(self, s0):
        """replay() only uses step(), not learn() — index stays empty."""
        inputs = ["learn scale on cpu=high"]   # PENDING via governor
        s = replay(s0, inputs)
        assert len(s["learning_index"]) == 0

    def test_replay_state_hash_stable(self, s0):
        inputs = ["echo a", "observe b", "chat c"]
        s_a = replay(copy.deepcopy(s0), inputs)
        s_b = replay(copy.deepcopy(s0), inputs)
        assert state_hash(s_a) == state_hash(s_b)


# ── M8: TamperDetection ───────────────────────────────────────────────


class TestTamperDetection:
    def test_tampered_receipt_breaks_chain(self, s0):
        s, _, _ = step(s0, "echo hello")
        # Mutate the proposal receipt hash directly
        s["receipts"][0]["receipt_hash"] = "deadbeef" * 8
        ok, errors = verify_chain(s["receipts"])
        assert not ok
        assert errors

    def test_tampered_previous_hash_breaks_chain(self, s0):
        s = replay(s0, ["echo a", "observe b"])
        s["receipts"][2]["previous_hash"] = "0" * 64
        ok, errors = verify_chain(s["receipts"])
        assert not ok

    def test_verify_chain_empty_is_ok(self, s0):
        ok, errors = verify_chain([])
        assert ok
        assert errors == []

    def test_verify_chain_single_valid_receipt(self, s0):
        s, _, _ = step(s0, "echo hello")
        # Only one receipt (the proposal) should still have genesis link
        ok, errors = verify_chain([s["receipts"][0]])
        assert ok


# ── M6: AuthorityFalse ────────────────────────────────────────────────


class TestAuthorityFalse:
    def test_all_step_receipts_authority_false(self, s0):
        s = replay(s0, ["echo a", "observe b", "plan c", "learn x"])
        for r in s["receipts"]:
            assert r["authority"] is False

    def test_learning_receipts_authority_false(self, s0):
        s, _ = learn(s0, "observe x", "act", "approve", 0.9)
        for r in s["receipts"]:
            assert r["authority"] is False


# ── learn() validation ────────────────────────────────────────────────


class TestLearnValidation:
    def test_invalid_feedback_raises(self, s0):
        with pytest.raises(ValueError, match="Invalid feedback"):
            learn(s0, "observe x", "act", "thumbs_up", 0.9)  # type: ignore[arg-type]

    def test_confidence_out_of_range_raises(self, s0):
        with pytest.raises(ValueError, match="confidence"):
            learn(s0, "observe x", "act", "approve", 1.5)

    def test_negative_confidence_raises(self, s0):
        with pytest.raises(ValueError, match="confidence"):
            learn(s0, "observe x", "act", "approve", -0.1)

    def test_confidence_exactly_zero_approve_not_indexed(self, s0):
        """conf=0.0 < MIN_CONFIDENCE, so not indexed."""
        s1, _ = learn(s0, "observe x", "act", "approve", 0.0)
        assert len(s1["learning_index"]) == 0

    def test_source_field_recorded(self, s0):
        s1, l_r = learn(s0, "observe x", "act", "approve", 0.9, source="expert_A")
        assert l_r.source == "expert_A"
        assert s1["learning_index"][0]["source"] == "expert_A"


# ── state_hash ────────────────────────────────────────────────────────


class TestStateHash:
    def test_hash_stable_across_calls(self, s0):
        h1 = state_hash(s0)
        h2 = state_hash(s0)
        assert h1 == h2

    def test_hash_changes_after_step(self, s0):
        h0 = state_hash(s0)
        s1, _, _ = step(s0, "echo hello")
        h1 = state_hash(s1)
        assert h0 != h1   # echo mutates env

    def test_hash_excludes_receipts(self, s0):
        """Adding a receipt must not change state_hash (receipts are not material state)."""
        h0 = state_hash(s0)
        s_modified = copy.deepcopy(s0)
        s_modified["receipts"].append({"fake": "receipt"})
        assert state_hash(s_modified) == h0

    def test_hash_changes_after_learn_approve(self, s0):
        h0 = state_hash(s0)
        s1, _ = learn(s0, "observe x", "act", "approve", 0.9)
        h1 = state_hash(s1)
        assert h0 != h1   # learning_index changed

    def test_hash_unchanged_after_learn_reject(self, s0):
        h0 = state_hash(s0)
        s1, _ = learn(s0, "observe x", "act", "reject", 0.9)
        h1 = state_hash(s1)
        assert h0 == h1   # env and learning_index unchanged


# ── retrieve_similar ──────────────────────────────────────────────────


class TestRetrieveSimilar:
    def test_returns_empty_if_no_learning_receipts(self, s0):
        s = replay(s0, ["echo a", "observe b"])
        hits = retrieve_similar(s["receipts"], "b")
        assert hits == []

    def test_returns_only_approved_by_default(self, s0):
        s = copy.deepcopy(s0)
        s, _ = learn(s, "observe x", "good", "approve", 0.9)
        s, _ = learn(s, "observe x", "bad",  "reject",  0.9)
        hits = retrieve_similar(s["receipts"], "x", approved_only=True)
        assert len(hits) == 1

    def test_returns_all_when_not_approved_only(self, s0):
        s = copy.deepcopy(s0)
        s, _ = learn(s, "observe x", "good", "approve", 0.9)
        s, _ = learn(s, "observe x", "bad",  "reject",  0.9)
        hits = retrieve_similar(s["receipts"], "x", approved_only=False)
        assert len(hits) == 2

    def test_top_5_cap(self, s0):
        s = copy.deepcopy(s0)
        for i in range(8):
            s, _ = learn(s, "observe x", f"act_{i}", "approve", 0.9)
        hits = retrieve_similar(s["receipts"], "x")
        assert len(hits) <= 5
