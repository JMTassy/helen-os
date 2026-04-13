"""Tests for helensh/claims.py + helensh/claim_types.py — Claim Engine.

Tests verify:
  - Closed claim vocabulary (6 types, fail-closed on unknown)
  - Evidence validation (complete/incomplete detection)
  - ClaimEngine produces all 6 claim types from governed state
  - VerifiableClaim immutability and deterministic hashing
  - Merkle proof attachment to claims
  - verify_claim catches: unknown types, missing evidence, hash tampering
  - verify_claim_against_state catches: root mismatch, value divergence
  - Claims from different states are different
  - Claims from same state + fixed ID are deterministic
"""
import copy
import pytest

from helensh.kernel import init_session, step, replay, revoke_capability
from helensh.claims import (
    VerifiableClaim,
    ClaimEngine,
    verify_claim,
    verify_claim_against_state,
)
from helensh.claim_types import (
    KNOWN_CLAIM_TYPES,
    CLAIM_TYPE_REGISTRY,
    is_known_claim_type,
    get_claim_type,
    validate_evidence,
    STATE_TRANSITION,
    MEMORY_DISCLOSURE,
    LEDGER_INTEGRITY,
    RECEIPT_INCLUSION,
    EXECUTION_RESULT,
    CAPABILITY_STATE,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return init_session(session_id="S-claims-test")


@pytest.fixture
def s_chat(s0):
    return replay(s0, ["hello", "world"])


@pytest.fixture
def s_mem(s0):
    return replay(s0, ["hello", "#remember important data"])


@pytest.fixture
def engine_chat(s_chat):
    return ClaimEngine(s_chat)


@pytest.fixture
def engine_mem(s_mem):
    return ClaimEngine(s_mem)


# ── Claim Type Registry ─────────────────────────────────────────────


class TestClaimTypes:
    def test_six_known_types(self):
        assert len(KNOWN_CLAIM_TYPES) == 6

    def test_all_types_known(self):
        for name in [
            "STATE_TRANSITION", "MEMORY_DISCLOSURE", "LEDGER_INTEGRITY",
            "RECEIPT_INCLUSION", "EXECUTION_RESULT", "CAPABILITY_STATE",
        ]:
            assert is_known_claim_type(name)

    def test_unknown_type_rejected(self):
        assert not is_known_claim_type("HALLUCINATED")

    def test_get_known_type(self):
        ct = get_claim_type("STATE_TRANSITION")
        assert ct.name == "STATE_TRANSITION"

    def test_get_unknown_raises(self):
        with pytest.raises(KeyError, match="Unknown claim type"):
            get_claim_type("FAKE")

    def test_validate_evidence_complete(self):
        evidence = {
            "state_hash_before": "a", "state_hash_after": "b",
            "user_input": "c", "verdict": "d",
        }
        ok, missing = validate_evidence(STATE_TRANSITION, evidence)
        assert ok
        assert missing == frozenset()

    def test_validate_evidence_incomplete(self):
        evidence = {"state_hash_before": "a"}
        ok, missing = validate_evidence(STATE_TRANSITION, evidence)
        assert not ok
        assert len(missing) == 3

    def test_each_type_has_required_evidence(self):
        for ct in CLAIM_TYPE_REGISTRY.values():
            assert len(ct.required_evidence) > 0
            assert ct.description != ""

    def test_claim_type_is_frozen(self):
        with pytest.raises(AttributeError):
            STATE_TRANSITION.name = "TAMPERED"


# ── ClaimEngine Construction ─────────────────────────────────────────


class TestClaimEngineConstruction:
    def test_engine_from_empty_state(self, s0):
        engine = ClaimEngine(s0)
        assert engine.session_id == "S-claims-test"
        assert len(engine.receipts) == 0

    def test_engine_from_chat_state(self, engine_chat):
        assert len(engine_chat.receipts) == 4  # 2 steps * 2 receipts

    def test_tree_lazily_built(self, engine_chat):
        assert engine_chat._tree is None
        _ = engine_chat.tree
        assert engine_chat._tree is not None

    def test_tree_root_is_hex(self, engine_chat):
        root = engine_chat.tree.root
        assert len(root) == 64
        assert all(c in "0123456789abcdef" for c in root)


# ── State Transition Claims ──────────────────────────────────────────


class TestStateTransitionClaim:
    def test_creates_valid_claim(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        assert claim.claim_type == "STATE_TRANSITION"
        assert claim.session_id == "S-claims-test"

    def test_evidence_complete(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        ok, missing = validate_evidence(STATE_TRANSITION, claim.evidence)
        assert ok, f"Missing: {missing}"

    def test_claim_is_frozen(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        with pytest.raises(AttributeError):
            claim.claim_type = "TAMPERED"

    def test_receipt_hash_is_hex(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        assert len(claim.receipt_hash) == 64
        assert all(c in "0123456789abcdef" for c in claim.receipt_hash)

    def test_deterministic_with_fixed_id_and_time(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        c1 = engine_chat.claim_state_transition(
            pr, claim_id="fixed", created_at="2024-01-01T00:00:00Z",
        )
        c2 = engine_chat.claim_state_transition(
            pr, claim_id="fixed", created_at="2024-01-01T00:00:00Z",
        )
        assert c1.receipt_hash == c2.receipt_hash

    def test_different_receipts_different_claims(self, s_chat, engine_chat):
        r0 = s_chat["receipts"][0]
        r2 = s_chat["receipts"][2]  # second step's proposal
        c1 = engine_chat.claim_state_transition(r0, claim_id="a", created_at="t")
        c2 = engine_chat.claim_state_transition(r2, claim_id="b", created_at="t")
        assert c1.receipt_hash != c2.receipt_hash

    def test_has_merkle_proof(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        assert isinstance(claim.merkle_proof, tuple)

    def test_merkle_root_matches_tree(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        assert claim.merkle_root == engine_chat.tree.root


# ── Memory Disclosure Claims ─────────────────────────────────────────


class TestMemoryDisclosureClaim:
    def test_creates_valid_claim(self, engine_mem):
        claim = engine_mem.claim_memory_disclosure("last_message")
        assert claim.claim_type == "MEMORY_DISCLOSURE"
        assert claim.evidence["key"] == "last_message"

    def test_value_matches_reconstruction(self, engine_mem):
        claim = engine_mem.claim_memory_disclosure("last_message")
        # last chat before #remember was "hello"
        assert claim.evidence["value"] == "hello"

    def test_memory_write_claim(self, engine_mem):
        claim = engine_mem.claim_memory_disclosure("mem_1")
        assert "important data" in str(claim.evidence["value"])

    def test_missing_key_raises(self, engine_mem):
        with pytest.raises(ValueError, match="not in receipt-derived"):
            engine_mem.claim_memory_disclosure("nonexistent")

    def test_has_source_receipt_hash(self, engine_mem):
        claim = engine_mem.claim_memory_disclosure("last_message")
        assert len(claim.evidence["source_receipt_hash"]) == 64


# ── Ledger Integrity Claims ─────────────────────────────────────────


class TestLedgerIntegrityClaim:
    def test_creates_valid_claim(self, engine_chat):
        claim = engine_chat.claim_ledger_integrity()
        assert claim.claim_type == "LEDGER_INTEGRITY"

    def test_receipt_count_matches(self, s_chat, engine_chat):
        claim = engine_chat.claim_ledger_integrity()
        assert claim.evidence["receipt_count"] == len(s_chat["receipts"])

    def test_chain_valid(self, engine_chat):
        claim = engine_chat.claim_ledger_integrity()
        assert claim.evidence["chain_valid"] is True

    def test_empty_ledger(self, s0):
        engine = ClaimEngine(s0)
        claim = engine.claim_ledger_integrity()
        assert claim.evidence["receipt_count"] == 0
        assert claim.evidence["chain_valid"] is True


# ── Receipt Inclusion Claims ─────────────────────────────────────────


class TestReceiptInclusionClaim:
    def test_creates_valid_claim(self, engine_chat):
        claim = engine_chat.claim_receipt_inclusion(0)
        assert claim.claim_type == "RECEIPT_INCLUSION"

    def test_evidence_has_proof(self, engine_chat):
        claim = engine_chat.claim_receipt_inclusion(0)
        assert "merkle_proof" in claim.evidence

    def test_every_receipt_claimable(self, s_chat, engine_chat):
        for i in range(len(s_chat["receipts"])):
            claim = engine_chat.claim_receipt_inclusion(i)
            assert claim.evidence["index"] == i

    def test_out_of_range_raises(self, engine_chat):
        with pytest.raises(IndexError):
            engine_chat.claim_receipt_inclusion(999)

    def test_negative_index_raises(self, engine_chat):
        with pytest.raises(IndexError):
            engine_chat.claim_receipt_inclusion(-1)


# ── Execution Result Claims ──────────────────────────────────────────


class TestExecutionResultClaim:
    def test_creates_valid_claim(self, s_chat, engine_chat):
        exec_receipt = s_chat["receipts"][1]  # first execution receipt
        claim = engine_chat.claim_execution_result(exec_receipt)
        assert claim.claim_type == "EXECUTION_RESULT"

    def test_evidence_has_action(self, s_chat, engine_chat):
        exec_receipt = s_chat["receipts"][1]
        claim = engine_chat.claim_execution_result(exec_receipt)
        assert claim.evidence["action"] == "chat"

    def test_evidence_has_effect_status(self, s_chat, engine_chat):
        exec_receipt = s_chat["receipts"][1]
        claim = engine_chat.claim_execution_result(exec_receipt)
        assert claim.evidence["effect_status"] == "APPLIED"

    def test_deny_path_claim(self, s0):
        s = revoke_capability(s0, "chat")
        s, _ = step(s, "hello")
        engine = ClaimEngine(s)
        exec_receipt = s["receipts"][1]
        claim = engine.claim_execution_result(exec_receipt)
        assert claim.evidence["verdict"] == "DENY"
        assert claim.evidence["effect_status"] == "DENIED"


# ── Capability State Claims ──────────────────────────────────────────


class TestCapabilityStateClaim:
    def test_creates_valid_claim(self, engine_chat):
        claim = engine_chat.claim_capability_state("chat")
        assert claim.claim_type == "CAPABILITY_STATE"

    def test_enabled_capability(self, engine_chat):
        claim = engine_chat.claim_capability_state("chat")
        assert claim.evidence["enabled"] is True

    def test_disabled_capability(self, s0):
        s = revoke_capability(s0, "chat")
        engine = ClaimEngine(s)
        claim = engine.claim_capability_state("chat")
        assert claim.evidence["enabled"] is False

    def test_unknown_capability_raises(self, engine_chat):
        with pytest.raises(KeyError):
            engine_chat.claim_capability_state("nonexistent")

    def test_has_state_hash(self, engine_chat):
        claim = engine_chat.claim_capability_state("chat")
        assert len(claim.evidence["state_hash"]) == 64


# ── Verification ─────────────────────────────────────────────────────


class TestVerifyClaim:
    def test_valid_claim_passes(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        ok, errors = verify_claim(claim)
        assert ok, f"Errors: {errors}"

    def test_all_claim_types_verify(self, s_mem, engine_mem):
        pr = s_mem["receipts"][0]
        claims = [
            engine_mem.claim_state_transition(pr),
            engine_mem.claim_memory_disclosure("last_message"),
            engine_mem.claim_ledger_integrity(),
            engine_mem.claim_receipt_inclusion(0),
            engine_mem.claim_execution_result(s_mem["receipts"][1]),
            engine_mem.claim_capability_state("chat"),
        ]
        for c in claims:
            ok, errors = verify_claim(c)
            assert ok, f"{c.claim_type} failed: {errors}"

    def test_tampered_hash_detected(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        # Create a claim with tampered hash
        tampered = VerifiableClaim(
            claim_id=claim.claim_id,
            claim_type=claim.claim_type,
            subject=claim.subject,
            assertion=claim.assertion,
            evidence=claim.evidence,
            merkle_root=claim.merkle_root,
            merkle_proof=claim.merkle_proof,
            session_id=claim.session_id,
            created_at=claim.created_at,
            receipt_hash="0" * 64,
        )
        ok, errors = verify_claim(tampered)
        assert not ok
        assert any("hash mismatch" in e for e in errors)

    def test_unknown_type_detected(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        tampered = VerifiableClaim(
            claim_id=claim.claim_id,
            claim_type="HALLUCINATED",
            subject=claim.subject,
            assertion=claim.assertion,
            evidence=claim.evidence,
            merkle_root=claim.merkle_root,
            merkle_proof=claim.merkle_proof,
            session_id=claim.session_id,
            created_at=claim.created_at,
            receipt_hash=claim.receipt_hash,
        )
        ok, errors = verify_claim(tampered)
        assert not ok
        assert any("Unknown claim type" in e for e in errors)

    def test_invalid_proof_side_detected(self, s_chat, engine_chat):
        pr = s_chat["receipts"][0]
        claim = engine_chat.claim_state_transition(pr)
        # Inject bad proof
        bad = VerifiableClaim(
            claim_id=claim.claim_id,
            claim_type=claim.claim_type,
            subject=claim.subject,
            assertion=claim.assertion,
            evidence=claim.evidence,
            merkle_root=claim.merkle_root,
            merkle_proof=(("abc123", "middle"),),  # invalid side
            session_id=claim.session_id,
            created_at=claim.created_at,
            receipt_hash=claim.receipt_hash,
        )
        ok, errors = verify_claim(bad)
        assert not ok
        assert any("proof side" in e or "hash mismatch" in e for e in errors)


# ── Verification Against State ───────────────────────────────────────


class TestVerifyClaimAgainstState:
    def test_valid_claim_passes(self, s_chat, engine_chat):
        claim = engine_chat.claim_ledger_integrity()
        ok, errors = verify_claim_against_state(claim, s_chat)
        assert ok, f"Errors: {errors}"

    def test_stale_ledger_claim_detected(self, s_chat, engine_chat):
        claim = engine_chat.claim_ledger_integrity()
        # Advance state with more receipts
        s_advanced = replay(s_chat, ["more input"])
        ok, errors = verify_claim_against_state(claim, s_advanced)
        assert not ok
        assert any("count changed" in e or "root mismatch" in e for e in errors)

    def test_memory_divergence_detected(self, s_mem, engine_mem):
        claim = engine_mem.claim_memory_disclosure("last_message")
        # Advance state so last_message changes
        s_advanced = replay(s_mem, ["new message"])
        ok, errors = verify_claim_against_state(claim, s_advanced)
        assert not ok
        assert any("diverged" in e or "root mismatch" in e for e in errors)

    def test_capability_change_detected(self, s_chat, engine_chat):
        claim = engine_chat.claim_capability_state("chat")
        # Revoke the capability
        s_revoked = revoke_capability(copy.deepcopy(s_chat), "chat")
        ok, errors = verify_claim_against_state(claim, s_revoked)
        assert not ok
        assert any("changed" in e for e in errors)


# ── Integration ──────────────────────────────────────────────────────


class TestClaimsIntegration:
    def test_every_step_produces_verifiable_claims(self, s0):
        """After every step, all claim types should verify."""
        inputs = ["hello", "#remember x", "#recall", "world"]
        s = copy.deepcopy(s0)
        for u in inputs:
            s, pr = step(s, u)
            engine = ClaimEngine(s)

            # State transition claim from this step
            claim = engine.claim_state_transition(pr)
            ok, errors = verify_claim(claim)
            assert ok, f"State transition failed after '{u}': {errors}"

            # Ledger integrity
            claim = engine.claim_ledger_integrity()
            ok, errors = verify_claim(claim)
            assert ok, f"Ledger integrity failed after '{u}': {errors}"

    def test_claims_from_replayed_state(self, s0):
        """Claims produced from replayed state should verify."""
        inputs = ["a", "b", "#remember c"]
        s = replay(s0, inputs)
        engine = ClaimEngine(s)

        for i in range(len(s["receipts"])):
            claim = engine.claim_receipt_inclusion(i)
            ok, errors = verify_claim(claim)
            assert ok, f"Receipt {i} failed: {errors}"

    def test_claim_determinism(self, s0):
        """Same state produces same claims (with fixed ID and timestamp)."""
        s1 = replay(s0, ["hello", "world"])
        s2 = replay(s0, ["hello", "world"])

        e1 = ClaimEngine(s1)
        e2 = ClaimEngine(s2)

        c1 = e1.claim_ledger_integrity(
            claim_id="test", created_at="2024-01-01T00:00:00Z",
        )
        c2 = e2.claim_ledger_integrity(
            claim_id="test", created_at="2024-01-01T00:00:00Z",
        )
        assert c1.receipt_hash == c2.receipt_hash
