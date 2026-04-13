"""Tests for helensh/gateway.py — Gateway Layer.

Tests verify:
  - Gateway initializes from fresh or existing state
  - submit() routes through kernel and returns GatewayResponse
  - Every submission produces a VerifiableClaim
  - Verdict mapping: ALLOW->OK, DENY->DENIED, PENDING->PENDING
  - Batch submission processes sequentially
  - inspect() returns verification results
  - Claims are stored and retrievable
  - Ledger integrity claims are available
  - Memory disclosure claims work through gateway
  - Receipt inclusion claims work through gateway
  - Full lifecycle: submit -> claim -> verify -> inspect
"""
import pytest

from helensh.kernel import init_session, revoke_capability
from helensh.gateway import Gateway, GatewayResponse, InspectResponse
from helensh.claims import verify_claim


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def gw():
    return Gateway(session_id="S-gw-test")


@pytest.fixture
def gw_chat(gw):
    gw.submit("hello")
    gw.submit("world")
    return gw


# ── Gateway Initialization ───────────────────────────────────────────


class TestGatewayInit:
    def test_fresh_gateway(self):
        gw = Gateway()
        assert gw.session_id == "gateway"
        assert gw.receipt_count == 0

    def test_custom_session_id(self):
        gw = Gateway(session_id="custom")
        assert gw.session_id == "custom"

    def test_from_existing_state(self):
        s = init_session(session_id="existing")
        gw = Gateway(state=s)
        assert gw.session_id == "existing"

    def test_state_is_deep_copied(self):
        s = init_session()
        gw = Gateway(state=s)
        gw.submit("hello")
        assert len(s["receipts"]) == 0  # original untouched


# ── Submit ───────────────────────────────────────────────────────────


class TestGatewaySubmit:
    def test_submit_returns_response(self, gw):
        resp = gw.submit("hello")
        assert isinstance(resp, GatewayResponse)

    def test_chat_is_ok(self, gw):
        resp = gw.submit("hello")
        assert resp.status == "OK"
        assert resp.verdict == "ALLOW"
        assert resp.action == "chat"

    def test_write_is_pending(self, gw):
        resp = gw.submit("#write some code")
        assert resp.status == "PENDING"
        assert resp.verdict == "PENDING"

    def test_denied_action(self):
        s = init_session(session_id="deny-test")
        s = revoke_capability(s, "chat")
        gw = Gateway(state=s)
        resp = gw.submit("hello")
        assert resp.status == "DENIED"
        assert resp.verdict == "DENY"

    def test_receipt_count_increments(self, gw):
        assert gw.receipt_count == 0
        gw.submit("hello")
        assert gw.receipt_count == 2  # proposal + execution
        gw.submit("world")
        assert gw.receipt_count == 4

    def test_state_hash_changes(self, gw):
        h0 = gw.state_hash
        gw.submit("hello")
        h1 = gw.state_hash
        assert h1 != h0

    def test_merkle_root_changes(self, gw):
        gw.submit("hello")
        r1 = gw.merkle_root
        gw.submit("world")
        r2 = gw.merkle_root
        assert r2 != r1

    def test_response_has_claim(self, gw):
        resp = gw.submit("hello")
        assert resp.claim is not None
        assert resp.claim.claim_type == "STATE_TRANSITION"

    def test_claim_verifies(self, gw):
        resp = gw.submit("hello")
        ok, errors = verify_claim(resp.claim)
        assert ok, f"Errors: {errors}"

    def test_response_has_receipt_hash(self, gw):
        resp = gw.submit("hello")
        assert len(resp.receipt_hash) == 64

    def test_response_has_request_id(self, gw):
        resp = gw.submit("hello")
        assert len(resp.request_id) > 0

    def test_no_error_on_success(self, gw):
        resp = gw.submit("hello")
        assert resp.error is None


# ── Batch Submit ─────────────────────────────────────────────────────


class TestGatewayBatch:
    def test_batch_returns_list(self, gw):
        responses = gw.submit_batch(["hello", "world"])
        assert len(responses) == 2

    def test_batch_sequential_state(self, gw):
        responses = gw.submit_batch(["hello", "world"])
        assert responses[0].receipt_count == 2
        assert responses[1].receipt_count == 4

    def test_batch_each_has_claim(self, gw):
        responses = gw.submit_batch(["hello", "world", "#recall"])
        for r in responses:
            assert r.claim is not None
            ok, errors = verify_claim(r.claim)
            assert ok, f"Errors: {errors}"

    def test_empty_batch(self, gw):
        responses = gw.submit_batch([])
        assert responses == []


# ── Inspect ──────────────────────────────────────────────────────────


class TestGatewayInspect:
    def test_inspect_existing_claim(self, gw):
        resp = gw.submit("hello")
        inspection = gw.inspect(resp.claim.claim_id)
        assert isinstance(inspection, InspectResponse)
        assert inspection.verified is True

    def test_inspect_unknown_returns_none(self, gw):
        assert gw.inspect("nonexistent") is None

    def test_inspect_shows_receipt_count(self, gw_chat):
        claims = gw_chat.list_claims()
        inspection = gw_chat.inspect(claims[0].claim_id)
        assert inspection.receipt_count == 4

    def test_inspect_has_errors_list(self, gw):
        resp = gw.submit("hello")
        inspection = gw.inspect(resp.claim.claim_id)
        assert inspection.errors == []


# ── Claim Retrieval ──────────────────────────────────────────────────


class TestGatewayClaimRetrieval:
    def test_list_claims(self, gw_chat):
        claims = gw_chat.list_claims()
        assert len(claims) == 2  # one per submit

    def test_get_claim_by_id(self, gw):
        resp = gw.submit("hello")
        claim = gw.get_claim(resp.claim.claim_id)
        assert claim is not None
        assert claim.claim_id == resp.claim.claim_id

    def test_get_unknown_returns_none(self, gw):
        assert gw.get_claim("nonexistent") is None

    def test_claims_accumulate(self, gw):
        gw.submit("a")
        gw.submit("b")
        gw.submit("c")
        assert len(gw.list_claims()) == 3


# ── Specialized Claims via Gateway ───────────────────────────────────


class TestGatewaySpecializedClaims:
    def test_ledger_integrity_claim(self, gw_chat):
        claim = gw_chat.claim_ledger_integrity()
        assert claim.claim_type == "LEDGER_INTEGRITY"
        ok, errors = verify_claim(claim)
        assert ok, f"Errors: {errors}"

    def test_memory_claim(self, gw):
        gw.submit("hello")
        claim = gw.claim_memory("last_message")
        assert claim.claim_type == "MEMORY_DISCLOSURE"
        assert claim.evidence["value"] == "hello"

    def test_receipt_inclusion_claim(self, gw_chat):
        claim = gw_chat.claim_receipt(0)
        assert claim.claim_type == "RECEIPT_INCLUSION"
        ok, errors = verify_claim(claim)
        assert ok, f"Errors: {errors}"

    def test_receipt_claim_for_every_receipt(self, gw_chat):
        for i in range(gw_chat.receipt_count):
            claim = gw_chat.claim_receipt(i)
            ok, errors = verify_claim(claim)
            assert ok, f"Receipt {i} failed: {errors}"

    def test_memory_claim_after_remember(self, gw):
        gw.submit("#remember critical data")
        claim = gw.claim_memory("mem_0")
        assert "critical data" in str(claim.evidence["value"])

    def test_specialized_claims_stored(self, gw_chat):
        initial = len(gw_chat.list_claims())
        gw_chat.claim_ledger_integrity()
        gw_chat.claim_receipt(0)
        assert len(gw_chat.list_claims()) == initial + 2


# ── Integration ──────────────────────────────────────────────────────


class TestGatewayIntegration:
    def test_full_lifecycle(self):
        """Submit -> claim -> verify -> inspect — full loop."""
        gw = Gateway(session_id="lifecycle")

        # 1. Submit intent
        resp = gw.submit("hello world")
        assert resp.status == "OK"

        # 2. Get claim
        claim = resp.claim
        assert claim is not None

        # 3. Verify independently
        ok, errors = verify_claim(claim)
        assert ok, f"Errors: {errors}"

        # 4. Inspect
        inspection = gw.inspect(claim.claim_id)
        assert inspection.verified

        # 5. Ledger integrity
        ledger_claim = gw.claim_ledger_integrity()
        ok, errors = verify_claim(ledger_claim)
        assert ok, f"Errors: {errors}"

    def test_multi_step_all_claims_verify(self):
        """Multiple steps, every claim verifies."""
        gw = Gateway(session_id="multi")
        inputs = ["hello", "#remember x", "#recall", "world", "#remember y"]
        for inp in inputs:
            resp = gw.submit(inp)
            assert resp.claim is not None
            ok, errors = verify_claim(resp.claim)
            assert ok, f"'{inp}' claim failed: {errors}"

        # All stored claims still valid
        for claim in gw.list_claims():
            ok, errors = verify_claim(claim)
            assert ok, f"Stored claim failed: {errors}"

    def test_deny_path_produces_claim(self):
        """DENY verdict still produces a verifiable claim."""
        s = init_session(session_id="deny")
        s = revoke_capability(s, "chat")
        gw = Gateway(state=s)
        resp = gw.submit("hello")
        assert resp.status == "DENIED"
        assert resp.claim is not None
        ok, errors = verify_claim(resp.claim)
        assert ok, f"Deny claim failed: {errors}"

    def test_pending_path_produces_claim(self):
        """PENDING verdict still produces a verifiable claim."""
        gw = Gateway(session_id="pending")
        resp = gw.submit("#write dangerous code")
        assert resp.status == "PENDING"
        assert resp.claim is not None
        ok, errors = verify_claim(resp.claim)
        assert ok, f"Pending claim failed: {errors}"

    def test_gateway_state_matches_kernel(self):
        """Gateway state should be identical to manual kernel stepping."""
        from helensh.kernel import step as kernel_step
        import copy

        s = init_session(session_id="compare")
        gw = Gateway(state=copy.deepcopy(s))

        # Step through both
        s, _ = kernel_step(s, "hello")
        gw.submit("hello")

        # State hashes should match
        from helensh.state import governed_state_hash
        assert governed_state_hash(s) == gw.state_hash
