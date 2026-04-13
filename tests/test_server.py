"""HELEN OS — Court API (FastAPI) Tests.

Tests the FastAPI surface using httpx TestClient.

Endpoints tested:
    POST /claim     — submit claim, get obligations
    POST /attest    — submit attestation (manual + execution-backed)
    POST /run       — full pipeline execution
    GET  /ledger    — read all entries
    GET  /ledger/verify   — hash chain verification
    GET  /ledger/decisions — replay decisions
    GET  /health    — health check

Test classes:
    1. TestHealth           — Health endpoint
    2. TestClaimEndpoint    — Claim submission
    3. TestAttestEndpoint   — Attestation submission
    4. TestRunEndpoint      — Full pipeline
    5. TestLedgerEndpoint   — Ledger reads
    6. TestLedgerVerify     — Hash chain verification
    7. TestLedgerDecisions  — Decision replay
    8. TestEndToEnd         — Full lifecycle through API
    9. TestNonSovereignty   — Authority always false
"""
import pytest
from fastapi.testclient import TestClient

from helensh.court import CourtLedger
from helensh.server import app, set_ledger, get_ledger


@pytest.fixture(autouse=True)
def fresh_ledger():
    """Each test gets a fresh in-memory ledger."""
    ledger = CourtLedger(":memory:")
    set_ledger(ledger)
    yield ledger
    ledger.close()
    set_ledger(None)


@pytest.fixture
def client():
    return TestClient(app)


# ── 1. Health ─────────────────────────────────────────────────────


class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["kernel"] == "court"

    def test_health_has_version(self, client):
        r = client.get("/health")
        assert "version" in r.json()


# ── 2. Claim Endpoint ────────────────────────────────────────────


class TestClaimEndpoint:
    def test_submit_basic_claim(self, client):
        r = client.post("/claim", json={
            "claim_id": "c1",
            "text": "hello world",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["claim_id"] == "c1"
        assert len(data["receipt_hash"]) == 64
        assert "basic_proof" in data["obligations"]

    def test_submit_code_claim(self, client):
        r = client.post("/claim", json={
            "claim_id": "c2",
            "text": "write code to compute fibonacci",
        })
        data = r.json()
        assert "code_execution" in data["obligations"]
        assert "output_verification" in data["obligations"]
        assert "basic_proof" in data["obligations"]

    def test_claim_recorded_in_ledger(self, client, fresh_ledger):
        client.post("/claim", json={"claim_id": "c1", "text": "test"})
        assert fresh_ledger.count() == 1
        entries = fresh_ledger.get_by_type("CLAIM")
        assert len(entries) == 1
        assert entries[0]["payload"]["claim_id"] == "c1"

    def test_claim_with_payload(self, client):
        r = client.post("/claim", json={
            "claim_id": "c3",
            "text": "test",
            "payload": {"key": "value"},
        })
        assert r.status_code == 200


# ── 3. Attest Endpoint ──────────────────────────────────────────


class TestAttestEndpoint:
    def test_manual_attestation(self, client):
        r = client.post("/attest", json={
            "claim_id": "c1",
            "obligation_name": "basic_proof",
            "evidence": "I checked it",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["execution_backed"] is False
        assert len(data["receipt_hash"]) == 64

    def test_execution_attestation(self, client):
        r = client.post("/attest", json={
            "claim_id": "c1",
            "obligation_name": "code_execution",
            "code": "print(42)",
        })
        data = r.json()
        assert data["valid"] is True
        assert data["execution_backed"] is True

    def test_execution_attestation_failure(self, client):
        r = client.post("/attest", json={
            "claim_id": "c1",
            "obligation_name": "code_execution",
            "code": "raise ValueError('boom')",
        })
        data = r.json()
        assert data["valid"] is False
        assert data["execution_backed"] is True

    def test_attestation_recorded_in_ledger(self, client, fresh_ledger):
        client.post("/attest", json={
            "claim_id": "c1",
            "obligation_name": "proof",
            "evidence": "yes",
        })
        entries = fresh_ledger.get_by_type("ATTESTATION")
        assert len(entries) == 1


# ── 4. Run Endpoint ─────────────────────────────────────────────


class TestRunEndpoint:
    def test_run_no_attestations_no_ship(self, client):
        r = client.post("/run", json={
            "claim_id": "c1",
            "text": "hello world",
        })
        data = r.json()
        assert data["decision"] == "NO_SHIP"
        assert len(data["missing"]) > 0

    def test_run_with_attestation_ships(self, client):
        r = client.post("/run", json={
            "claim_id": "c1",
            "text": "hello world",
            "attestations": [
                {"obligation_name": "basic_proof", "evidence": "yes"},
            ],
        })
        data = r.json()
        assert data["decision"] == "SHIP"
        assert data["missing"] == []

    def test_run_with_kill_flag(self, client):
        r = client.post("/run", json={
            "claim_id": "c1",
            "text": "hello world",
            "kill_flag": True,
            "attestations": [
                {"obligation_name": "basic_proof", "evidence": "yes"},
            ],
        })
        data = r.json()
        assert data["decision"] == "NO_SHIP"
        assert data["kill_flag"] is True

    def test_run_with_code_execution(self, client):
        r = client.post("/run", json={
            "claim_id": "c1",
            "text": "compute with code",
            "attestations": [
                {"obligation_name": "output_verification", "evidence": "42"},
                {"obligation_name": "basic_proof", "evidence": "yes"},
            ],
            "code": "print(42)",
        })
        data = r.json()
        assert data["decision"] == "SHIP"

    def test_run_records_claim_and_decision(self, client, fresh_ledger):
        client.post("/run", json={
            "claim_id": "c1",
            "text": "hello",
            "attestations": [
                {"obligation_name": "basic_proof", "evidence": "yes"},
            ],
        })
        claims = fresh_ledger.get_by_type("CLAIM")
        decisions = fresh_ledger.get_by_type("DECISION")
        assert len(claims) == 1
        assert len(decisions) == 1

    def test_run_has_receipt_hash(self, client):
        r = client.post("/run", json={
            "claim_id": "c1",
            "text": "hello",
        })
        data = r.json()
        assert len(data["receipt_hash"]) == 64


# ── 5. Ledger Endpoint ──────────────────────────────────────────


class TestLedgerEndpoint:
    def test_empty_ledger(self, client):
        r = client.get("/ledger")
        data = r.json()
        assert data["count"] == 0
        assert data["entries"] == []

    def test_ledger_after_claim(self, client):
        client.post("/claim", json={"claim_id": "c1", "text": "test"})
        r = client.get("/ledger")
        data = r.json()
        assert data["count"] == 1

    def test_ledger_after_multiple_ops(self, client):
        client.post("/claim", json={"claim_id": "c1", "text": "test"})
        client.post("/attest", json={
            "claim_id": "c1",
            "obligation_name": "proof",
            "evidence": "yes",
        })
        r = client.get("/ledger")
        data = r.json()
        assert data["count"] == 2

    def test_ledger_entries_have_hashes(self, client):
        client.post("/claim", json={"claim_id": "c1", "text": "test"})
        r = client.get("/ledger")
        entry = r.json()["entries"][0]
        assert "hash" in entry
        assert "previous_hash" in entry
        assert len(entry["hash"]) == 64


# ── 6. Ledger Verify ────────────────────────────────────────────


class TestLedgerVerify:
    def test_empty_ledger_valid(self, client):
        r = client.get("/ledger/verify")
        data = r.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_populated_ledger_valid(self, client):
        client.post("/claim", json={"claim_id": "c1", "text": "test"})
        client.post("/attest", json={
            "claim_id": "c1",
            "obligation_name": "proof",
            "evidence": "yes",
        })
        r = client.get("/ledger/verify")
        data = r.json()
        assert data["valid"] is True
        assert data["entry_count"] == 2

    def test_verify_after_full_run(self, client):
        client.post("/run", json={
            "claim_id": "c1",
            "text": "hello",
            "attestations": [
                {"obligation_name": "basic_proof", "evidence": "yes"},
            ],
        })
        r = client.get("/ledger/verify")
        data = r.json()
        assert data["valid"] is True


# ── 7. Ledger Decisions ─────────────────────────────────────────


class TestLedgerDecisions:
    def test_no_decisions(self, client):
        r = client.get("/ledger/decisions")
        data = r.json()
        assert data["count"] == 0
        assert data["decisions"] == []

    def test_decisions_after_run(self, client):
        client.post("/run", json={
            "claim_id": "c1",
            "text": "hello",
        })
        r = client.get("/ledger/decisions")
        data = r.json()
        assert data["count"] == 1
        assert data["decisions"][0]["payload"]["decision"] == "NO_SHIP"

    def test_multiple_decisions(self, client):
        client.post("/run", json={
            "claim_id": "c1",
            "text": "hello",
        })
        client.post("/run", json={
            "claim_id": "c2",
            "text": "world",
            "attestations": [
                {"obligation_name": "basic_proof", "evidence": "yes"},
            ],
        })
        r = client.get("/ledger/decisions")
        data = r.json()
        assert data["count"] == 2


# ── 8. End-to-End ───────────────────────────────────────────────


class TestEndToEnd:
    """Full lifecycle through the API."""

    def test_claim_attest_run_verify(self, client):
        # 1. Submit claim
        r = client.post("/claim", json={
            "claim_id": "e2e",
            "text": "hello world",
        })
        assert r.status_code == 200
        obligations = r.json()["obligations"]
        assert "basic_proof" in obligations

        # 2. Submit attestation
        r = client.post("/attest", json={
            "claim_id": "e2e",
            "obligation_name": "basic_proof",
            "evidence": "confirmed",
        })
        assert r.status_code == 200
        assert r.json()["valid"] is True

        # 3. Run pipeline
        r = client.post("/run", json={
            "claim_id": "e2e",
            "text": "hello world",
            "attestations": [
                {"obligation_name": "basic_proof", "evidence": "confirmed"},
            ],
        })
        data = r.json()
        assert data["decision"] == "SHIP"

        # 4. Verify chain
        r = client.get("/ledger/verify")
        assert r.json()["valid"] is True

        # 5. Check ledger
        r = client.get("/ledger")
        assert r.json()["count"] >= 3  # claim + attestation + claim + attestation + decision

    def test_code_execution_lifecycle(self, client):
        # Submit code claim with execution
        r = client.post("/run", json={
            "claim_id": "code_e2e",
            "text": "compute with code",
            "code": "print(2 + 2)",
            "attestations": [
                {"obligation_name": "output_verification", "evidence": "4"},
                {"obligation_name": "basic_proof", "evidence": "executed"},
            ],
        })
        data = r.json()
        assert data["decision"] == "SHIP"

        # Verify chain integrity
        r = client.get("/ledger/verify")
        assert r.json()["valid"] is True

    def test_no_ship_to_ship_progression(self, client):
        # First: no attestations → NO_SHIP
        r = client.post("/run", json={
            "claim_id": "prog1",
            "text": "hello",
        })
        assert r.json()["decision"] == "NO_SHIP"

        # Second: with attestations → SHIP
        r = client.post("/run", json={
            "claim_id": "prog2",
            "text": "hello",
            "attestations": [
                {"obligation_name": "basic_proof", "evidence": "done"},
            ],
        })
        assert r.json()["decision"] == "SHIP"

        # Both decisions in replay
        r = client.get("/ledger/decisions")
        data = r.json()
        assert data["count"] == 2


# ── 9. Non-Sovereignty ──────────────────────────────────────────


class TestNonSovereignty:
    """Authority is ALWAYS false on every response."""

    def test_ship_decision_no_authority(self, client):
        r = client.post("/run", json={
            "claim_id": "c1",
            "text": "hello",
            "attestations": [
                {"obligation_name": "basic_proof", "evidence": "yes"},
            ],
        })
        assert r.json()["authority"] is False

    def test_no_ship_decision_no_authority(self, client):
        r = client.post("/run", json={
            "claim_id": "c1",
            "text": "hello",
        })
        assert r.json()["authority"] is False

    def test_kill_decision_no_authority(self, client):
        r = client.post("/run", json={
            "claim_id": "c1",
            "text": "hello",
            "kill_flag": True,
            "attestations": [
                {"obligation_name": "basic_proof", "evidence": "yes"},
            ],
        })
        assert r.json()["authority"] is False
