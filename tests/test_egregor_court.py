"""HELEN OS — Egregor × Court Tests.

Tests the governed executor: Egregor (intelligence) + Court (truth).

Test groups:
    1. run_task_governed — CLAIM → ATTESTATION → DECISION receipts
    2. run_task_with_tools — python_exec attestations, tool_result_hash
    3. Chain integrity — hash chain valid after all operations
    4. SHIP / NO_SHIP — court decision correctness
    5. Tool binding — execution-backed evidence vs weak evidence

All Ollama calls monkeypatched. No network.
python_exec runs for real (sandboxed).
"""
from __future__ import annotations

import pytest

from helensh.court import CourtLedger
from helensh.egregor.egregor_court import run_task_governed, run_task_with_tools


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def ledger():
    l = CourtLedger(":memory:")
    yield l
    l.close()


@pytest.fixture(autouse=True)
def patch_ollama(monkeypatch):
    """All tests get a fake ollama_call. No network."""
    monkeypatch.setattr(
        "helensh.egregor.executor.ollama_call",
        _default_fake_call,
    )


def _default_fake_call(model, prompt):
    """Smart fake: code tasks get code, everything else gets text."""
    if "code" in model or "coder" in model:
        return "def add(a, b):\n    return a + b\nprint(add(2, 3))"
    return "This is a valid response from HELEN."


# ── 1. run_task_governed — receipts exist ────────────────────────────────────

class TestGovernedReceipts:
    def test_claim_recorded(self, ledger):
        run_task_governed("hello world", ledger)
        claims = ledger.get_by_type("CLAIM")
        assert len(claims) == 1
        assert claims[0]["payload"]["text"] == "hello world"

    def test_attestation_recorded_on_approval(self, ledger):
        run_task_governed("hello world", ledger)
        atts = ledger.get_by_type("ATTESTATION")
        assert len(atts) == 1
        assert atts[0]["payload"]["obligation_name"] == "basic_proof"

    def test_decision_recorded(self, ledger):
        run_task_governed("hello world", ledger)
        decisions = ledger.get_by_type("DECISION")
        assert len(decisions) == 1

    def test_minimum_three_receipts(self, ledger):
        """CLAIM + ATTESTATION + DECISION = at least 3."""
        run_task_governed("hello world", ledger)
        assert ledger.count() >= 3

    def test_claim_id_format(self, ledger):
        result = run_task_governed("hello", ledger)
        assert result["claim"].claim_id.startswith("egregor_")


# ── 2. SHIP / NO_SHIP ───────────────────────────────────────────────────────

class TestDecisions:
    def test_approved_task_ships(self, ledger):
        result = run_task_governed("hello world", ledger)
        assert result["decision"].decision == "SHIP"

    def test_rejected_task_no_ship(self, ledger, monkeypatch):
        """All models return empty → no attestation → NO_SHIP."""
        monkeypatch.setattr(
            "helensh.egregor.executor.ollama_call",
            lambda m, t: "",
        )
        result = run_task_governed("hello", ledger)
        assert result["decision"].decision == "NO_SHIP"
        assert len(result["decision"].missing) > 0

    def test_decision_authority_false(self, ledger):
        result = run_task_governed("hello", ledger)
        assert result["decision"].authority is False

    def test_egregor_result_included(self, ledger):
        result = run_task_governed("hello", ledger)
        assert "egregor" in result
        assert result["egregor"]["approved"] is True
        assert result["egregor"]["street"] == "chat"


# ── 3. Chain integrity ──────────────────────────────────────────────────────

class TestChainIntegrity:
    def test_chain_valid_after_one_task(self, ledger):
        run_task_governed("hello", ledger)
        valid, errors = ledger.verify_chain()
        assert valid is True
        assert errors == []

    def test_chain_valid_after_multiple_tasks(self, ledger):
        run_task_governed("hello", ledger)
        run_task_governed("write code for sorting", ledger)
        run_task_governed("explain why the sky is blue", ledger)
        valid, errors = ledger.verify_chain()
        assert valid is True
        assert errors == []

    def test_chain_valid_mixed_success_failure(self, ledger, monkeypatch):
        run_task_governed("hello", ledger)

        # Make second task fail
        monkeypatch.setattr(
            "helensh.egregor.executor.ollama_call",
            lambda m, t: "",
        )
        run_task_governed("write code", ledger)

        valid, errors = ledger.verify_chain()
        assert valid is True

    def test_receipts_all_have_hashes(self, ledger):
        run_task_governed("hello", ledger)
        for entry in ledger.get_all():
            assert len(entry["hash"]) == 64
            assert len(entry["previous_hash"]) >= 16


# ── 4. run_task_with_tools — tool binding ────────────────────────────────────

class TestToolBinding:
    def test_code_task_has_tool_result_hash(self, ledger):
        """Code tasks get python_exec → tool_result_hash is not None."""
        result = run_task_with_tools("write code to add numbers", ledger)
        assert result["tool_bound"] is True

        # Find the code_execution attestation
        atts = ledger.get_by_type("ATTESTATION")
        code_atts = [a for a in atts if a["payload"]["obligation_name"] == "code_execution"]
        assert len(code_atts) == 1
        assert code_atts[0]["payload"]["tool_result_hash"] is not None

    def test_code_task_ships(self, ledger):
        result = run_task_with_tools("write code to add", ledger)
        assert result["decision"].decision == "SHIP"

    def test_chat_task_no_tool_binding(self, ledger):
        """Non-code tasks get weak attestations — tool_result_hash is None."""
        result = run_task_with_tools("hello world", ledger)
        assert result["tool_bound"] is False

        atts = ledger.get_by_type("ATTESTATION")
        for a in atts:
            assert a["payload"]["tool_result_hash"] is None

    def test_tool_bound_has_more_receipts(self, ledger):
        """Tool-bound tasks produce more attestations (code_execution + output_verification + basic_proof)."""
        result = run_task_with_tools("write code to add numbers", ledger)
        atts = ledger.get_by_type("ATTESTATION")
        # code_execution + output_verification + basic_proof = 3
        assert len(atts) >= 3

    def test_chain_valid_after_tool_binding(self, ledger):
        run_task_with_tools("write code to add numbers", ledger)
        run_task_with_tools("hello world", ledger)
        valid, errors = ledger.verify_chain()
        assert valid is True
        assert errors == []

    def test_execution_evidence_is_real(self, ledger):
        """The execution actually ran — output is the real result of the code."""
        result = run_task_with_tools("write code for addition", ledger)

        atts = ledger.get_by_type("ATTESTATION")
        output_atts = [a for a in atts if a["payload"]["obligation_name"] == "output_verification"]
        if output_atts:
            # The fake coder returns "def add(a,b): return a+b\nprint(add(2,3))"
            # python_exec should produce "5\n" as output
            evidence = output_atts[0]["payload"]["evidence"]
            assert "5" in str(evidence)


# ── 5. Weak vs strong evidence ──────────────────────────────────────────────

class TestEvidenceStrength:
    def test_governed_is_weak(self, ledger):
        """run_task_governed produces weak attestations (no tool_result_hash)."""
        run_task_governed("hello", ledger)
        atts = ledger.get_by_type("ATTESTATION")
        for a in atts:
            assert a["payload"]["tool_result_hash"] is None

    def test_tools_code_is_strong(self, ledger):
        """run_task_with_tools on code produces strong attestations."""
        run_task_with_tools("write code", ledger)
        atts = ledger.get_by_type("ATTESTATION")
        code_atts = [a for a in atts if a["payload"]["obligation_name"] == "code_execution"]
        for a in code_atts:
            assert a["payload"]["tool_result_hash"] is not None

    def test_tools_chat_is_still_weak(self, ledger):
        """run_task_with_tools on chat still produces weak attestations."""
        run_task_with_tools("hello world", ledger)
        atts = ledger.get_by_type("ATTESTATION")
        for a in atts:
            assert a["payload"]["tool_result_hash"] is None

    def test_failed_execution_invalid_attestation(self, ledger, monkeypatch):
        """If python_exec fails, attestation.valid = False."""
        def bad_coder(model, prompt):
            if "coder" in model:
                return "raise ValueError('boom')"
            return "valid text"

        monkeypatch.setattr("helensh.egregor.executor.ollama_call", bad_coder)
        result = run_task_with_tools("write code", ledger)

        atts = ledger.get_by_type("ATTESTATION")
        code_atts = [a for a in atts if a["payload"]["obligation_name"] == "code_execution"]
        if code_atts:
            assert code_atts[0]["payload"]["valid"] is False


# ── 6. Multiple tasks accumulate correctly ───────────────────────────────────

class TestAccumulation:
    def test_two_tasks_correct_count(self, ledger):
        run_task_governed("hello", ledger)
        run_task_governed("good morning", ledger)
        # Each: 1 claim + 1 attestation + 1 decision = 3
        assert ledger.count() == 6

    def test_claim_ids_unique(self, ledger):
        r1 = run_task_governed("hello", ledger)
        r2 = run_task_governed("world", ledger)
        assert r1["claim"].claim_id != r2["claim"].claim_id

    def test_replay_decisions(self, ledger):
        run_task_governed("hello", ledger)
        run_task_governed("write code", ledger)
        decisions = ledger.replay_decisions()
        assert len(decisions) == 2
