"""HELEN OS — Tests for Governance Normal Form (GNF).

Tests the 5-layer governance function: G = E . T . V . P . S

Covers:
  - S layer: sensing produces correct Signal from inputs
  - P layer: proposal generation from signal
  - V layer: validation maps kernel verdicts to GNF vocabulary
  - T layer: stress checks detect invariant violations
  - E layer: execution produces correct state mutation + memory effect
  - gnf_step(): full 5-layer pipeline
  - GNFReceipt: structural verification
  - Backward compatibility with kernel.step()
  - Determinism (I1), NoSilentEffect (I2), AuthorityFalse (I6)
  - Stress layer PREVENT override
  - Custom stress checks
"""
import copy
import pytest

from helensh.kernel import (
    init_session,
    step as kernel_step,
    cognition,
    governor,
    revoke_capability,
    KNOWN_ACTIONS,
    WRITE_ACTIONS,
)
from helensh.gnf import (
    gnf_step,
    sense,
    propose,
    validate,
    stress,
    execute,
    verify_gnf_receipt,
    Signal,
    StressResult,
    GNFReceipt,
    GNF_ALLOW,
    GNF_PREVENT,
    GNF_DEFER,
    DEFAULT_STRESS_CHECKS,
)
from helensh.state import effect_footprint, governed_state_hash
from helensh.memory import verify_memory, reconstruct_memory
from helensh.replay import verify_chain


# ── Helpers ──────────────────────────────────────────────────────────

def _fresh():
    return init_session("S-gnf-test", "tester", "/tmp")


# ═══════════════════════════════════════════════════════════════════════
# S LAYER: Sensing
# ═══════════════════════════════════════════════════════════════════════

class TestSensing:
    """S layer produces correct Signal from input + state."""

    def test_string_input(self):
        s = _fresh()
        sig = sense(s, "hello")
        assert sig.input_type == "string"
        assert sig.raw_input == "hello"
        assert sig.turn == 0
        assert isinstance(sig.environment, dict)
        assert isinstance(sig.pressure, dict)

    def test_dict_input(self):
        s = _fresh()
        inp = {"action": "memory_write", "payload": {"key": "x", "value": 1}}
        sig = sense(s, inp)
        assert sig.input_type == "dict"
        assert sig.raw_input == inp

    def test_empty_input(self):
        s = _fresh()
        sig = sense(s, "")
        assert sig.input_type == "empty"

    def test_none_input(self):
        s = _fresh()
        sig = sense(s, None)
        assert sig.input_type == "empty"

    def test_environment_snapshot(self):
        s = _fresh()
        sig = sense(s, "hello")
        assert sig.environment["turn"] == 0
        assert sig.environment["receipt_count"] == 0
        assert isinstance(sig.environment["memory_keys"], list)
        assert sig.environment["active_capabilities"] > 0

    def test_pressure_vectors(self):
        s = _fresh()
        sig = sense(s, "hello")
        assert "capability_pressure" in sig.pressure
        assert "memory_pressure" in sig.pressure
        assert "chain_depth" in sig.pressure
        # Fresh state: low pressure
        assert sig.pressure["memory_pressure"] == 0.0
        assert sig.pressure["chain_depth"] == 0.0

    def test_pressure_increases_with_revoked_caps(self):
        s = _fresh()
        sig_before = sense(s, "hello")
        s = revoke_capability(s, "chat")
        s = revoke_capability(s, "search")
        sig_after = sense(s, "hello")
        assert sig_after.pressure["capability_pressure"] > sig_before.pressure["capability_pressure"]

    def test_session_id_propagates(self):
        s = _fresh()
        sig = sense(s, "hello")
        assert sig.session_id == "S-gnf-test"

    def test_signal_frozen(self):
        s = _fresh()
        sig = sense(s, "hello")
        with pytest.raises(AttributeError):
            sig.turn = 99


# ═══════════════════════════════════════════════════════════════════════
# P LAYER: Proposal
# ═══════════════════════════════════════════════════════════════════════

class TestProposal:
    """P layer generates proposals from signal."""

    def test_string_proposal(self):
        s = _fresh()
        sig = sense(s, "hello")
        prop = propose(s, sig)
        assert prop["action"] == "chat"
        assert prop["authority"] is False

    def test_dict_proposal(self):
        s = _fresh()
        sig = sense(s, {"action": "memory_write", "payload": {"key": "x", "value": 42}})
        prop = propose(s, sig)
        assert prop["action"] == "memory_write"
        assert prop["payload"]["key"] == "x"
        assert prop["authority"] is False

    def test_remember_kv_proposal(self):
        s = _fresh()
        sig = sense(s, "#remember score=100")
        prop = propose(s, sig)
        assert prop["action"] == "memory_write"
        assert prop["payload"]["key"] == "score"
        assert prop["payload"]["value"] == 100


# ═══════════════════════════════════════════════════════════════════════
# V LAYER: Validation
# ═══════════════════════════════════════════════════════════════════════

class TestValidation:
    """V layer maps governor verdicts to GNF vocabulary."""

    def test_chat_allows(self):
        s = _fresh()
        prop = cognition(s, "hello")
        verdict = validate(prop, s)
        assert verdict == GNF_ALLOW

    def test_write_defers(self):
        s = _fresh()
        prop = cognition(s, "#write something")
        verdict = validate(prop, s)
        assert verdict == GNF_DEFER  # write → PENDING → DEFER

    def test_revoked_prevents(self):
        s = _fresh()
        s = revoke_capability(s, "chat")
        prop = cognition(s, "hello")
        verdict = validate(prop, s)
        assert verdict == GNF_PREVENT


# ═══════════════════════════════════════════════════════════════════════
# T LAYER: Stress
# ═══════════════════════════════════════════════════════════════════════

class TestStress:
    """T layer runs adversarial invariant checks."""

    def test_clean_proposal_passes(self):
        s = _fresh()
        prop = cognition(s, "hello")
        result = stress(prop, s, GNF_ALLOW)
        assert result.passed is True
        assert len(result.failures) == 0
        assert result.verdict_override is None

    def test_authority_true_fails(self):
        s = _fresh()
        prop = {"action": "chat", "payload": {"message": "hi"}, "authority": True}
        result = stress(prop, s, GNF_ALLOW)
        assert result.passed is False
        assert any("authority" in f for f in result.failures)
        assert result.verdict_override == GNF_PREVENT

    def test_unknown_action_fails(self):
        s = _fresh()
        prop = {"action": "teleport", "payload": {}, "authority": False}
        result = stress(prop, s, GNF_ALLOW)
        assert result.passed is False
        assert any("unknown action" in f for f in result.failures)

    def test_all_default_checks_run(self):
        s = _fresh()
        prop = cognition(s, "hello")
        result = stress(prop, s, GNF_ALLOW)
        assert len(result.checks_run) == len(DEFAULT_STRESS_CHECKS)

    def test_custom_checks(self):
        """Custom stress checks can be injected."""
        def always_fail(proposal, state, verdict):
            return "custom failure"

        s = _fresh()
        prop = cognition(s, "hello")
        result = stress(prop, s, GNF_ALLOW, checks=[("custom", always_fail)])
        assert result.passed is False
        assert "custom: custom failure" in result.failures

    def test_stress_result_frozen(self):
        s = _fresh()
        prop = cognition(s, "hello")
        result = stress(prop, s, GNF_ALLOW)
        with pytest.raises(AttributeError):
            result.passed = False

    def test_prevent_verdict_not_checked_for_capability(self):
        """PREVENT verdict skips capability check (it's already blocked)."""
        s = _fresh()
        prop = cognition(s, "hello")
        result = stress(prop, s, GNF_PREVENT)
        # Should pass — capability check only fires on ALLOW
        assert all("capability" not in f for f in result.failures)


# ═══════════════════════════════════════════════════════════════════════
# E LAYER: Execution
# ═══════════════════════════════════════════════════════════════════════

class TestExecution:
    """E layer executes state mutation with memory effect tracking."""

    def test_allow_applies(self):
        s = _fresh()
        prop = {"action": "chat", "payload": {"message": "hi"}, "authority": False}
        new_s, status, mem_effect, tool_res = execute(s, prop, "ALLOW")
        assert status == "APPLIED"
        assert new_s["working_memory"]["last_message"] == "hi"
        assert mem_effect is not None
        assert mem_effect["last_message"] == "hi"
        assert tool_res is None  # no tool registry → no tool execution

    def test_deny_no_effect(self):
        s = _fresh()
        fp_before = effect_footprint(s)
        prop = {"action": "chat", "payload": {"message": "hi"}, "authority": False}
        new_s, status, mem_effect, tool_res = execute(s, prop, "DENY")
        assert status == "DENIED"
        assert mem_effect is None
        assert tool_res is None
        assert effect_footprint(new_s) == fp_before

    def test_kv_memory_write(self):
        s = _fresh()
        prop = {"action": "memory_write", "payload": {"key": "x", "value": 42}, "authority": False}
        new_s, status, mem_effect, tool_res = execute(s, prop, "ALLOW")
        assert status == "APPLIED"
        assert new_s["working_memory"]["x"] == 42
        assert mem_effect["x"] == 42
        assert tool_res is None


# ═══════════════════════════════════════════════════════════════════════
# gnf_step(): Full 5-layer pipeline
# ═══════════════════════════════════════════════════════════════════════

class TestGNFStep:
    """Full G = E . T . V . P . S pipeline."""

    def test_basic_chat(self):
        s = _fresh()
        s, receipt = gnf_step(s, "hello")
        assert receipt.final_verdict == GNF_ALLOW
        assert receipt.kernel_verdict == "ALLOW"
        assert receipt.effect_status == "APPLIED"
        assert receipt.authority is False
        assert receipt.stress_result.passed is True
        assert s["working_memory"]["last_message"] == "hello"

    def test_memory_write_kv(self):
        s = _fresh()
        s, receipt = gnf_step(s, "#remember x=42")
        assert receipt.final_verdict == GNF_ALLOW
        assert receipt.effect_status == "APPLIED"
        assert s["working_memory"]["x"] == 42
        assert receipt.memory_effect["x"] == 42

    def test_dict_passthrough(self):
        s = _fresh()
        s, receipt = gnf_step(s, {"action": "memory_write", "payload": {"key": "y", "value": True}})
        assert receipt.final_verdict == GNF_ALLOW
        assert s["working_memory"]["y"] is True

    def test_write_defers(self):
        s = _fresh()
        s, receipt = gnf_step(s, "#write something")
        assert receipt.final_verdict == GNF_DEFER
        assert receipt.kernel_verdict == "PENDING"
        assert receipt.effect_status == "DEFERRED"

    def test_revoked_prevents(self):
        s = _fresh()
        s = revoke_capability(s, "chat")
        s, receipt = gnf_step(s, "hello")
        assert receipt.final_verdict == GNF_PREVENT
        assert receipt.kernel_verdict == "DENY"
        assert receipt.effect_status == "DENIED"

    def test_receipts_appended(self):
        """Two kernel receipts per gnf_step (proposal + execution)."""
        s = _fresh()
        assert len(s["receipts"]) == 0
        s, _ = gnf_step(s, "hello")
        assert len(s["receipts"]) == 2

    def test_turn_increments(self):
        s = _fresh()
        assert s["turn"] == 0
        s, _ = gnf_step(s, "hello")
        assert s["turn"] == 1

    def test_signal_captured(self):
        s = _fresh()
        s, receipt = gnf_step(s, "hello")
        assert receipt.signal.input_type == "string"
        assert receipt.signal.raw_input == "hello"
        assert receipt.signal.turn == 0

    def test_multi_step_chain(self):
        s = _fresh()
        s, r1 = gnf_step(s, "hello")
        s, r2 = gnf_step(s, "#remember x=1")
        s, r3 = gnf_step(s, "#remember y=2")
        assert s["turn"] == 3
        assert len(s["receipts"]) == 6
        assert s["working_memory"]["x"] == 1
        assert s["working_memory"]["y"] == 2


# ═══════════════════════════════════════════════════════════════════════
# Stress layer PREVENT override
# ═══════════════════════════════════════════════════════════════════════

class TestStressOverride:
    """Stress layer can promote any verdict to PREVENT."""

    def test_stress_overrides_allow(self):
        """Custom stress check that always fails → PREVENT even if V says ALLOW."""
        def fail_all(proposal, state, verdict):
            return "blocked by stress test"

        s = _fresh()
        s, receipt = gnf_step(s, "hello", stress_checks=[("fail_all", fail_all)])
        assert receipt.validation_verdict == GNF_ALLOW  # V layer said ALLOW
        assert receipt.stress_result.passed is False
        assert receipt.final_verdict == GNF_PREVENT     # T layer overrode to PREVENT
        assert receipt.kernel_verdict == "DENY"
        assert receipt.effect_status == "DENIED"
        # No mutation
        assert "last_message" not in s["working_memory"]

    def test_stress_does_not_weaken_prevent(self):
        """If V says PREVENT, stress cannot weaken it to ALLOW."""
        s = _fresh()
        s = revoke_capability(s, "chat")
        s, receipt = gnf_step(s, "hello")
        assert receipt.validation_verdict == GNF_PREVENT
        assert receipt.final_verdict == GNF_PREVENT


# ═══════════════════════════════════════════════════════════════════════
# GNF Invariants
# ═══════════════════════════════════════════════════════════════════════

class TestGNFInvariants:
    """Kernel invariants hold through GNF pipeline."""

    def test_determinism_I1(self):
        s = _fresh()
        s1, r1 = gnf_step(copy.deepcopy(s), "hello")
        s2, r2 = gnf_step(copy.deepcopy(s), "hello")
        assert r1.proposal_receipt_hash == r2.proposal_receipt_hash
        assert r1.execution_receipt_hash == r2.execution_receipt_hash
        assert s1["working_memory"] == s2["working_memory"]

    def test_no_silent_effect_I2(self):
        s = _fresh()
        s = revoke_capability(s, "chat")
        fp_before = effect_footprint(s)
        s, receipt = gnf_step(s, "hello")
        fp_after = effect_footprint(s)
        assert fp_before == fp_after
        assert receipt.effect_status == "DENIED"

    def test_receipt_completeness_I3(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        assert len(s["receipts"]) == 2
        assert s["receipts"][0]["type"] == "PROPOSAL"
        assert s["receipts"][1]["type"] == "EXECUTION"

    def test_chain_integrity_I4(self):
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "#remember x=1")
        s, _ = gnf_step(s, "world")
        assert verify_chain(s["receipts"])

    def test_authority_false_I6(self):
        s = _fresh()
        for _ in range(5):
            s, receipt = gnf_step(s, "hello")
            assert receipt.authority is False
        for r in s["receipts"]:
            assert r["authority"] is False

    def test_epistemic_closure(self):
        """verify_memory passes after GNF steps."""
        s = _fresh()
        s, _ = gnf_step(s, "hello")
        s, _ = gnf_step(s, "#remember x=42")
        s, _ = gnf_step(s, "#witness observation")
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed: {errors}"


# ═══════════════════════════════════════════════════════════════════════
# GNFReceipt verification
# ═══════════════════════════════════════════════════════════════════════

class TestGNFReceiptVerification:
    """verify_gnf_receipt checks structural properties."""

    def test_valid_receipt_passes(self):
        s = _fresh()
        s, receipt = gnf_step(s, "hello")
        ok, errors = verify_gnf_receipt(receipt)
        assert ok, f"verify_gnf_receipt failed: {errors}"

    def test_authority_true_fails(self):
        """Manually constructed receipt with authority=True fails."""
        s = _fresh()
        s, receipt = gnf_step(s, "hello")
        # Construct invalid receipt
        bad = GNFReceipt(
            signal=receipt.signal,
            proposal=receipt.proposal,
            validation_verdict=receipt.validation_verdict,
            stress_result=receipt.stress_result,
            final_verdict=receipt.final_verdict,
            kernel_verdict=receipt.kernel_verdict,
            effect_status=receipt.effect_status,
            memory_effect=receipt.memory_effect,
            tool_result=receipt.tool_result,
            proposal_receipt_hash=receipt.proposal_receipt_hash,
            execution_receipt_hash=receipt.execution_receipt_hash,
            state_hash_before=receipt.state_hash_before,
            state_hash_after=receipt.state_hash_after,
            turn=receipt.turn,
            authority=True,  # violation
        )
        ok, errors = verify_gnf_receipt(bad)
        assert not ok
        assert any("authority" in e for e in errors)


# ═══════════════════════════════════════════════════════════════════════
# Backward compatibility with kernel.step()
# ═══════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """gnf_step produces same kernel state as kernel.step for clean inputs."""

    def test_same_state_chat(self):
        s_k = _fresh()
        s_g = _fresh()
        s_k, _ = kernel_step(s_k, "hello")
        s_g, _ = gnf_step(s_g, "hello")
        assert s_k["working_memory"] == s_g["working_memory"]
        assert s_k["turn"] == s_g["turn"]
        assert len(s_k["receipts"]) == len(s_g["receipts"])

    def test_same_state_memory_write(self):
        s_k = _fresh()
        s_g = _fresh()
        s_k, _ = kernel_step(s_k, "#remember x=42")
        s_g, _ = gnf_step(s_g, "#remember x=42")
        assert s_k["working_memory"] == s_g["working_memory"]

    def test_same_receipt_hashes(self):
        """Kernel receipts from gnf_step match kernel.step hashes."""
        s_k = _fresh()
        s_g = _fresh()
        s_k, rk = kernel_step(s_k, "hello")
        s_g, rg = gnf_step(s_g, "hello")
        assert s_k["receipts"][0]["hash"] == s_g["receipts"][0]["hash"]
        assert s_k["receipts"][1]["hash"] == s_g["receipts"][1]["hash"]

    def test_same_chain_integrity(self):
        s_k = _fresh()
        s_g = _fresh()
        for inp in ["hello", "#remember x=1", "#recall"]:
            s_k, _ = kernel_step(s_k, inp)
            s_g, _ = gnf_step(s_g, inp)
        assert verify_chain(s_k["receipts"])
        assert verify_chain(s_g["receipts"])
        # Receipt hashes match
        for i in range(len(s_k["receipts"])):
            assert s_k["receipts"][i]["hash"] == s_g["receipts"][i]["hash"]
