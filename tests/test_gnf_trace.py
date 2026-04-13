"""HELEN OS — GNF v1.1 Full Trace Tests.

Tests the trace observability layer:
  - Trace capsule attached to both proposal and execution receipts
  - Trace NOT in receipt_hash (backward compat)
  - Signal persistence (S layer in trace)
  - StressResult persistence (T layer in trace)
  - TraceCompleteness invariant
  - GNF replay engine (5-layer causal log)
  - Adversarial integration (stress blocks valid proposal, trace proves why)
  - Verify gnf_trace across chains
"""
import copy
import json
import pytest

from helensh.kernel import (
    init_session,
    step as kernel_step,
    revoke_capability,
)
from helensh.gnf import (
    gnf_step,
    sense,
    stress,
    build_trace,
    verify_gnf_receipt,
    verify_trace_completeness,
    Signal,
    StressResult,
    GNFReceipt,
    GNF_ALLOW,
    GNF_PREVENT,
    GNF_DEFER,
    DEFAULT_STRESS_CHECKS,
)
from helensh.gnf_replay import (
    replay_gnf,
    replay_gnf_trace,
    replay_gnf_decisions,
    verify_gnf_trace,
    TraceEntry,
    DecisionSummary,
)
from helensh.state import governed_state_hash, effect_footprint
from helensh.replay import verify_chain, rebuild_and_verify
from helensh.memory import verify_memory, reconstruct_memory


def _fresh():
    return init_session("S-trace-test", "tester", "/tmp")


# ═══════════════════════════════════════════════════════════════════════
# Trace Capsule Attachment
# ═══════════════════════════════════════════════════════════════════════

class TestTraceCapsuleAttachment:
    """Trace attached to both proposal and execution receipts."""

    def test_execution_receipt_has_trace(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        e_receipt = s["receipts"][-1]
        assert e_receipt["type"] == "EXECUTION"
        assert "trace" in e_receipt
        assert isinstance(e_receipt["trace"], dict)

    def test_proposal_receipt_has_trace(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        p_receipt = s["receipts"][-2]
        assert p_receipt["type"] == "PROPOSAL"
        assert "trace" in p_receipt
        assert isinstance(p_receipt["trace"], dict)

    def test_trace_has_all_four_layers(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        trace = s["receipts"][-1]["trace"]
        assert "signal" in trace
        assert "proposal" in trace
        assert "validation" in trace
        assert "stress" in trace

    def test_trace_has_final_verdict(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        trace = s["receipts"][-1]["trace"]
        assert trace["final_verdict"] == "ALLOW"

    def test_trace_signal_layer(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        sig = s["receipts"][-1]["trace"]["signal"]
        assert sig["input_type"] == "string"
        assert sig["raw_input"] == "hello"
        assert sig["turn"] == 0
        assert "environment" in sig
        assert "pressure" in sig

    def test_trace_proposal_layer(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        prop = s["receipts"][-1]["trace"]["proposal"]
        assert prop["action"] == "chat"
        assert prop["authority"] is False

    def test_trace_validation_layer(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        val = s["receipts"][-1]["trace"]["validation"]
        assert val["verdict"] == "ALLOW"

    def test_trace_stress_layer(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        stress_t = s["receipts"][-1]["trace"]["stress"]
        assert stress_t["verdict"] == "PASS"
        assert stress_t["passed"] is True
        assert len(stress_t["failures"]) == 0
        assert len(stress_t["checks_run"]) == len(DEFAULT_STRESS_CHECKS)

    def test_proposal_trace_is_stub(self):
        """Proposal receipt carries only S, P (pre-governance stub)."""
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        p_trace = s["receipts"][-2]["trace"]
        assert "signal" in p_trace
        assert "proposal" in p_trace
        # Stub does NOT contain V or T
        assert "validation" not in p_trace
        assert "stress" not in p_trace

    def test_execution_trace_is_full(self):
        """Execution receipt carries S, P, V, T (full trace per I11)."""
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        e_trace = s["receipts"][-1]["trace"]
        assert "signal" in e_trace
        assert "proposal" in e_trace
        assert "validation" in e_trace
        assert "stress" in e_trace
        assert "final_verdict" in e_trace

    def test_stub_is_subset_of_full(self):
        """Proposal stub fields are identical to execution trace fields."""
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        p_trace = s["receipts"][-2]["trace"]
        e_trace = s["receipts"][-1]["trace"]
        assert p_trace["signal"] == e_trace["signal"]
        assert p_trace["proposal"] == e_trace["proposal"]


# ═══════════════════════════════════════════════════════════════════════
# Trace NOT in hash (backward compat)
# ═══════════════════════════════════════════════════════════════════════

class TestTraceNotInHash:
    """Trace must not affect receipt_hash — observability only."""

    def test_gnf_hashes_match_kernel_hashes(self):
        """gnf_step receipt hashes still match kernel.step hashes."""
        s_k = _fresh()
        s_g = _fresh()
        s_k, _ = kernel_step(s_k, "hello")
        s_g, _ = gnf_step(s_g, "hello")
        # Kernel receipts don't have trace
        assert "trace" not in s_k["receipts"][-1]
        # GNF receipts do have trace
        assert "trace" in s_g["receipts"][-1]
        # But hashes are identical
        assert s_k["receipts"][0]["hash"] == s_g["receipts"][0]["hash"]
        assert s_k["receipts"][1]["hash"] == s_g["receipts"][1]["hash"]

    def test_chain_integrity_with_trace(self):
        s = _fresh()
        for inp in ["hello", "#remember x=1", "#recall", "#witness test"]:
            s, _ = gnf_step(s, inp)
        assert verify_chain(s["receipts"])

    def test_trace_is_json_serializable(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        trace = s["receipts"][-1]["trace"]
        # Must not raise
        serialized = json.dumps(trace, sort_keys=True)
        deserialized = json.loads(serialized)
        assert deserialized == trace


# ═══════════════════════════════════════════════════════════════════════
# Signal Serialization
# ═══════════════════════════════════════════════════════════════════════

class TestSignalSerialization:
    """Signal.to_dict() produces correct trace-safe output."""

    def test_string_signal(self):
        s = _fresh()
        sig = sense(s, "hello")
        d = sig.to_dict()
        assert d["input_type"] == "string"
        assert d["raw_input"] == "hello"
        assert isinstance(d["environment"], dict)
        assert isinstance(d["pressure"], dict)

    def test_dict_signal(self):
        s = _fresh()
        sig = sense(s, {"action": "chat", "payload": {"message": "hi"}})
        d = sig.to_dict()
        assert d["input_type"] == "dict"
        assert isinstance(d["raw_input"], dict)

    def test_signal_roundtrip(self):
        """Signal → to_dict → JSON → parse → same dict."""
        s = _fresh()
        sig = sense(s, "hello world")
        d = sig.to_dict()
        rt = json.loads(json.dumps(d))
        assert rt == d


# ═══════════════════════════════════════════════════════════════════════
# StressResult Serialization
# ═══════════════════════════════════════════════════════════════════════

class TestStressResultSerialization:
    """StressResult.to_dict() produces correct trace-safe output."""

    def test_pass_result(self):
        from helensh.kernel import cognition
        s = _fresh()
        prop = cognition(s, "hello")
        result = stress(prop, s, GNF_ALLOW)
        d = result.to_dict()
        assert d["passed"] is True
        assert d["verdict"] == "PASS"
        assert len(d["failures"]) == 0

    def test_fail_result(self):
        prop = {"action": "teleport", "payload": {}, "authority": True}
        s = _fresh()
        result = stress(prop, s, GNF_ALLOW)
        d = result.to_dict()
        assert d["passed"] is False
        assert d["verdict"] == "FAIL"
        assert len(d["failures"]) > 0

    def test_stress_roundtrip(self):
        from helensh.kernel import cognition
        s = _fresh()
        prop = cognition(s, "hello")
        result = stress(prop, s, GNF_ALLOW)
        d = result.to_dict()
        rt = json.loads(json.dumps(d))
        assert rt == d


# ═══════════════════════════════════════════════════════════════════════
# TraceCompleteness Invariant
# ═══════════════════════════════════════════════════════════════════════

class TestTraceCompleteness:
    """verify_trace_completeness checks all 4 layers present."""

    def test_gnf_receipts_complete(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "#remember x=1")
        ok, errors = verify_trace_completeness(s["receipts"])
        assert ok, f"TraceCompleteness failed: {errors}"

    def test_kernel_receipts_skip(self):
        """kernel.step() receipts (no trace) pass — backward compat."""
        s = _fresh()
        s, _ = kernel_step(s, "hello")
        ok, errors = verify_trace_completeness(s["receipts"])
        assert ok  # no trace → skipped, not failed

    def test_mixed_receipts(self):
        """Mix of kernel.step and gnf_step receipts."""
        s = _fresh()
        s, _ = kernel_step(s, "hello")
        s, _ = gnf_step(s, "world")
        ok, errors = verify_trace_completeness(s["receipts"])
        assert ok

    def test_incomplete_trace_fails(self):
        """Manually corrupt a trace → should fail."""
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        # Remove signal from trace
        s["receipts"][-1]["trace"]["signal"] = None
        ok, errors = verify_trace_completeness(s["receipts"])
        assert not ok
        assert any("signal" in e for e in errors)


# ═══════════════════════════════════════════════════════════════════════
# GNF Replay Engine
# ═══════════════════════════════════════════════════════════════════════

class TestGNFReplayTrace:
    """replay_gnf_trace reconstructs full 5-layer causal log."""

    def test_single_step(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        entries = replay_gnf_trace(s["receipts"])
        assert len(entries) == 1
        e = entries[0]
        assert e.has_trace is True
        assert e.turn == 0
        assert e.signal is not None
        assert e.proposal is not None
        assert e.validation is not None
        assert e.stress is not None
        assert e.effect_status == "APPLIED"
        assert e.final_verdict == "ALLOW"

    def test_multi_step(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "#remember x=1")
        s, _ = gnf_step(s, "#write file")
        entries = replay_gnf_trace(s["receipts"])
        assert len(entries) == 3
        assert entries[0].effect_status == "APPLIED"
        assert entries[1].effect_status == "APPLIED"
        assert entries[2].effect_status == "DEFERRED"  # write → PENDING → DEFER

    def test_kernel_step_no_trace(self):
        s = _fresh()
        s, _ = kernel_step(s, "hello")
        entries = replay_gnf_trace(s["receipts"])
        assert len(entries) == 1
        assert entries[0].has_trace is False
        assert entries[0].signal is None

    def test_entry_frozen(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        entries = replay_gnf_trace(s["receipts"])
        with pytest.raises(AttributeError):
            entries[0].turn = 99


class TestGNFReplayDecisions:
    """replay_gnf_decisions produces compressed decision log."""

    def test_basic_decisions(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "#remember x=42")
        decisions = replay_gnf_decisions(s["receipts"])
        assert len(decisions) == 2
        assert decisions[0].action == "chat"
        assert decisions[0].validation_verdict == "ALLOW"
        assert decisions[0].stress_verdict == "PASS"
        assert decisions[1].action == "memory_write"

    def test_denied_decision(self):
        s = _fresh()
        s = revoke_capability(s, "chat")
        s, _ = gnf_step(s, "hello")
        decisions = replay_gnf_decisions(s["receipts"])
        assert len(decisions) == 1
        d = decisions[0]
        assert d.validation_verdict == "PREVENT"
        assert d.effect_status == "DENIED"

    def test_stress_failure_decision(self):
        def fail_check(proposal, state, verdict):
            return "adversarial_block"

        s = _fresh()
        s, _ = gnf_step(s, "hello", stress_checks=[("adversarial", fail_check)])
        decisions = replay_gnf_decisions(s["receipts"])
        assert len(decisions) == 1
        d = decisions[0]
        assert d.stress_verdict == "FAIL"
        assert d.final_verdict == "PREVENT"
        assert d.effect_status == "DENIED"
        assert "adversarial: adversarial_block" in d.stress_failures

    def test_decision_summary_frozen(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        decisions = replay_gnf_decisions(s["receipts"])
        with pytest.raises(AttributeError):
            decisions[0].action = "hacked"


# ═══════════════════════════════════════════════════════════════════════
# Verify GNF Trace
# ═══════════════════════════════════════════════════════════════════════

class TestVerifyGNFTrace:
    """verify_gnf_trace checks trace integrity across chain."""

    def test_clean_chain_passes(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "#remember x=1")
        s, _ = gnf_step(s, "#witness observation")
        ok, errors = verify_gnf_trace(s["receipts"])
        assert ok, f"verify_gnf_trace failed: {errors}"

    def test_deferred_chain_passes(self):
        s = _fresh()
        s, _ = gnf_step(s, "#write something")
        ok, errors = verify_gnf_trace(s["receipts"])
        assert ok

    def test_stress_fail_consistency(self):
        """Stress FAIL → effect DENIED is verified."""
        def fail_all(proposal, state, verdict):
            return "blocked"

        s = _fresh()
        s, _ = gnf_step(s, "hello", stress_checks=[("block", fail_all)])
        ok, errors = verify_gnf_trace(s["receipts"])
        assert ok  # consistent: FAIL → DENIED

    def test_corrupted_verdict_fails(self):
        """Manually corrupt final_verdict → should fail."""
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        # Corrupt: claim PREVENT but effect is APPLIED
        s["receipts"][-1]["trace"]["final_verdict"] = "PREVENT"
        ok, errors = verify_gnf_trace(s["receipts"])
        assert not ok
        assert any("PREVENT" in e and "APPLIED" in e for e in errors)

    def test_authority_violation_detected(self):
        """Trace with authority=True in proposal is caught."""
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s["receipts"][-1]["trace"]["proposal"]["authority"] = True
        ok, errors = verify_gnf_trace(s["receipts"])
        assert not ok
        assert any("authority" in e for e in errors)


# ═══════════════════════════════════════════════════════════════════════
# ADVERSARIAL INTEGRATION TEST (MANDATORY)
# ═══════════════════════════════════════════════════════════════════════

class TestAdversarialIntegration:
    """Stress blocks valid proposal — trace proves why.

    The core test from the GNF v1.1 spec:
    A proposal that passes V (validation) but fails T (stress)
    must be DENIED, and the trace must record both V=ALLOW and T=FAIL.
    """

    def test_stress_blocks_valid_proposal(self):
        """V=ALLOW, T=FAIL => D=PREVENT, with replay still exact.

        The core proof:
          - final decision = PREVENT
          - effect status = no mutation
          - state unchanged
          - chain valid
          - trace preserved exactly
        """
        s_before = copy.deepcopy(_fresh())
        s = copy.deepcopy(s_before)

        def malicious_check(proposal, state, verdict):
            return "forced_failure_by_adversarial_test"

        fp_before = effect_footprint(s)
        s, receipt = gnf_step(s, "hello", stress_checks=[("adversarial", malicious_check)])

        # 1. Final decision = PREVENT
        assert receipt.final_verdict == GNF_PREVENT

        # 2. Effect status = DENIED (no mutation)
        assert receipt.effect_status == "DENIED"
        assert receipt.kernel_verdict == "DENY"

        # 3. State unchanged (effect footprint identical)
        fp_after = effect_footprint(s)
        assert fp_before == fp_after
        assert "last_message" not in s["working_memory"]

        # 4. Chain valid
        assert verify_chain(s["receipts"])

        # 5. Trace preserved exactly — V=ALLOW, T=FAIL → D=PREVENT
        trace = s["receipts"][-1]["trace"]
        assert trace["validation"]["verdict"] == "ALLOW"
        assert trace["stress"]["verdict"] == "FAIL"
        assert trace["final_verdict"] == "PREVENT"
        assert "adversarial: forced_failure_by_adversarial_test" in trace["stress"]["failures"]

        # 6. verify_gnf_trace passes (trace is consistent with effect)
        ok, errors = verify_gnf_trace(s["receipts"])
        assert ok, f"verify_gnf_trace failed: {errors}"

        # 7. Memory verify passes (no hidden state)
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed: {errors}"

        # 8. Canonical replay: ReplayGNF(R) → (X_t, Θ_≤t) both correct
        state_replayed, trace_log = replay_gnf(s["receipts"])
        assert state_replayed["working_memory"] == {}  # nothing applied
        assert len(trace_log) == 1
        assert trace_log[0].effect_status == "DENIED"
        assert trace_log[0].final_verdict == "PREVENT"

    def test_adversarial_then_clean(self):
        """Adversarial block followed by clean step — both traced."""
        s = _fresh()

        def fail_once(proposal, state, verdict):
            return "blocked_first"

        # First step: adversarial block
        s, r1 = gnf_step(s, "hello", stress_checks=[("fail_once", fail_once)])
        assert r1.effect_status == "DENIED"

        # Second step: clean (default stress checks)
        s, r2 = gnf_step(s, "hello")
        assert r2.effect_status == "APPLIED"

        # Both traces present and valid
        entries = replay_gnf_trace(s["receipts"])
        assert len(entries) == 2
        assert entries[0].has_trace is True
        assert entries[0].effect_status == "DENIED"
        assert entries[1].has_trace is True
        assert entries[1].effect_status == "APPLIED"

        # Full chain valid
        assert verify_chain(s["receipts"])
        ok, errors = verify_gnf_trace(s["receipts"])
        assert ok

    def test_multiple_stress_failures_all_recorded(self):
        """Multiple custom stress checks fail — all failures in trace."""
        def fail_a(p, s, v):
            return "check_a_failed"
        def fail_b(p, s, v):
            return "check_b_failed"
        def pass_c(p, s, v):
            return None  # passes

        s = _fresh()
        s, receipt = gnf_step(s, "hello", stress_checks=[
            ("a", fail_a), ("b", fail_b), ("c", pass_c)
        ])

        assert receipt.effect_status == "DENIED"
        trace = s["receipts"][-1]["trace"]
        failures = trace["stress"]["failures"]
        assert "a: check_a_failed" in failures
        assert "b: check_b_failed" in failures
        assert len(failures) == 2  # c passed


# ═══════════════════════════════════════════════════════════════════════
# Full Pipeline Replay Integration
# ═══════════════════════════════════════════════════════════════════════

class TestFullPipelineReplay:
    """End-to-end: multi-step gnf → replay trace → verify."""

    def test_full_pipeline(self):
        s = _fresh()

        # Mixed operations
        s, _ = gnf_step(s, "hello")                    # chat → ALLOW
        s, _ = gnf_step(s, "#remember color=blue")     # memory_write → ALLOW
        s, _ = gnf_step(s, "#write output.txt")        # write → DEFER
        s, _ = gnf_step(s, "#recall")                  # memory_read → ALLOW
        s, _ = gnf_step(s, "#witness proof1")           # witness → ALLOW

        # 5 steps × 2 receipts = 10 receipts
        assert len(s["receipts"]) == 10

        # Chain valid
        assert verify_chain(s["receipts"])

        # Memory consistent
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed: {errors}"

        # Trace replay
        entries = replay_gnf_trace(s["receipts"])
        assert len(entries) == 5
        assert all(e.has_trace for e in entries)

        statuses = [e.effect_status for e in entries]
        assert statuses == ["APPLIED", "APPLIED", "DEFERRED", "APPLIED", "APPLIED"]

        # Decision replay
        decisions = replay_gnf_decisions(s["receipts"])
        assert len(decisions) == 5
        actions = [d.action for d in decisions]
        assert actions == ["chat", "memory_write", "write_file", "memory_read", "witness"]

        # Trace integrity
        ok, errors = verify_gnf_trace(s["receipts"])
        assert ok, f"verify_gnf_trace failed: {errors}"

        # TraceCompleteness
        ok, errors = verify_trace_completeness(s["receipts"])
        assert ok, f"TraceCompleteness failed: {errors}"


# ═══════════════════════════════════════════════════════════════════════
# Determinism with Trace
# ═══════════════════════════════════════════════════════════════════════

class TestCanonicalReplay:
    """replay_gnf() returns (state, trace_log) — ReplayGNF(R≤t) → (X_t, Θ≤t)."""

    def test_replay_returns_state_and_trace(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "#remember x=42")
        state, trace_log = replay_gnf(s["receipts"])
        # X_t: state has correct working memory
        assert state["working_memory"]["last_message"] == "hello"
        assert state["working_memory"]["x"] == 42
        # Θ≤t: trace log has 2 entries
        assert len(trace_log) == 2

    def test_replay_state_matches_live(self):
        """Replayed state equals live working_memory."""
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "#remember color=blue")
        s, _ = gnf_step(s, "#witness proof")
        state, _ = replay_gnf(s["receipts"])
        assert state["working_memory"] == s["working_memory"]

    def test_replay_trace_log_has_traces(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "#write file")  # DEFER
        _, trace_log = replay_gnf(s["receipts"])
        assert all(e.has_trace for e in trace_log)
        assert trace_log[0].effect_status == "APPLIED"
        assert trace_log[1].effect_status == "DEFERRED"

    def test_replay_empty_receipts(self):
        state, trace_log = replay_gnf([])
        assert state["working_memory"] == {}
        assert trace_log == []

    def test_replay_denied_produces_empty_state(self):
        """All DENIED steps → empty working memory."""
        s = _fresh()
        s = revoke_capability(s, "chat")
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "world")
        state, trace_log = replay_gnf(s["receipts"])
        assert state["working_memory"] == {}
        assert len(trace_log) == 2
        assert all(e.effect_status == "DENIED" for e in trace_log)


class TestDeterminismWithTrace:
    """I1: Determinism holds even with trace attached."""

    def test_same_hashes_with_trace(self):
        s = _fresh()
        s1, r1 = gnf_step(copy.deepcopy(s), "hello")
        s2, r2 = gnf_step(copy.deepcopy(s), "hello")
        # Receipt hashes identical (trace not in hash)
        assert s1["receipts"][0]["hash"] == s2["receipts"][0]["hash"]
        assert s1["receipts"][1]["hash"] == s2["receipts"][1]["hash"]
        # Execution traces identical
        assert s1["receipts"][-1]["trace"] == s2["receipts"][-1]["trace"]
        # Proposal stubs identical
        assert s1["receipts"][-2]["trace"] == s2["receipts"][-2]["trace"]
