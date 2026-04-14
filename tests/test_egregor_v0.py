"""HELEN OS — Egregor v0 Tests.

The 5 tests that matter:
    1. code task routes to code street
    2. reason task routes to reason street
    3. chat fallback for unknown
    4. HAL rejection triggers fallback model
    5. full rejection returns clean failure

Plus: registry hard boundary, hal_review guardrail, determinism.

All Ollama calls monkeypatched. No network.
"""
from __future__ import annotations

import pytest

from helensh.egregor.registry import (
    EGREGOR_ROUTES,
    VALID_STREETS,
    get_models_for_street,
)
from helensh.egregor.router import classify
from helensh.egregor.executor import run_task, run_task_receipted, hal_review
from helensh.court import CourtLedger


# ── Test 1: Code routing ────────────────────────────────────────────────────

def test_code_routing(monkeypatch):
    monkeypatch.setattr(
        "helensh.egregor.executor.ollama_call",
        lambda m, t: "def add(a, b): return a + b",
    )
    result = run_task("write a python function to add two numbers")
    assert result["street"] == "code"
    assert result["approved"] is True


# ── Test 2: Reason routing ──────────────────────────────────────────────────

def test_reason_routing(monkeypatch):
    monkeypatch.setattr(
        "helensh.egregor.executor.ollama_call",
        lambda m, t: "Light scatters shorter wavelengths more — Rayleigh scattering.",
    )
    result = run_task("explain why the sky is blue")
    assert result["street"] == "reason"
    assert result["approved"] is True


# ── Test 3: Chat fallback ───────────────────────────────────────────────────

def test_chat_default(monkeypatch):
    monkeypatch.setattr(
        "helensh.egregor.executor.ollama_call",
        lambda m, t: "I'm here. What's on your mind?",
    )
    result = run_task("hello how are you")
    assert result["street"] == "chat"
    assert result["approved"] is True


# ── Test 4: HAL rejection triggers fallback ─────────────────────────────────

def test_fallback_model(monkeypatch):
    """Primary returns empty (HAL rejects), fallback returns valid output."""
    def fake_call(model, prompt):
        if model == "her-coder":
            return ""  # empty → HAL rejects
        return "def add(a, b): return a + b"  # fallback succeeds

    monkeypatch.setattr("helensh.egregor.executor.ollama_call", fake_call)

    result = run_task("write code")

    assert result["approved"] is True
    assert result["model"] != "her-coder"
    assert result["model"] == "qwen2.5-coder:7b"
    assert len(result["attempts"]) == 2


# ── Test 5: Full rejection ──────────────────────────────────────────────────

def test_full_rejection(monkeypatch):
    """All models return empty → all rejected → clean governed failure."""
    monkeypatch.setattr("helensh.egregor.executor.ollama_call", lambda m, t: "")

    result = run_task("write code")

    assert result["approved"] is False
    assert result["model"] is None
    assert result["output"] is None
    assert len(result["attempts"]) == 2  # tried both models in code chain


# ── Registry: hard boundary ─────────────────────────────────────────────────

class TestRegistry:
    def test_four_streets(self):
        assert VALID_STREETS == {"chat", "code", "reason", "fast"}

    def test_unknown_street_raises(self):
        with pytest.raises(ValueError, match="Unknown street"):
            get_models_for_street("nonexistent")

    def test_code_chain(self):
        assert get_models_for_street("code") == ["her-coder", "qwen2.5-coder:7b"]

    def test_reason_chain(self):
        assert get_models_for_street("reason") == ["deepseek-r1:8b", "gemma4"]

    def test_chat_chain(self):
        assert get_models_for_street("chat") == ["helen-chat", "helen-core"]

    def test_fast_chain(self):
        assert get_models_for_street("fast") == ["helen-ship", "qwen2.5:3b"]


# ── HAL review guardrail ───────────────────────────────────────────────────

class TestHalReview:
    def test_empty_rejected(self):
        assert hal_review("")["verdict"] == "REJECT"

    def test_short_rejected(self):
        assert hal_review("hi")["verdict"] == "REJECT"

    def test_whitespace_rejected(self):
        assert hal_review("    ")["verdict"] == "REJECT"

    def test_valid_approved(self):
        assert hal_review("This is a valid response.")["verdict"] == "APPROVE"

    def test_five_chars_approved(self):
        assert hal_review("abcde")["verdict"] == "APPROVE"

    def test_none_rejected(self):
        assert hal_review(None)["verdict"] == "REJECT"


# ── Determinism ─────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_street(self):
        """classify is pure. Same input → same output. Always."""
        task = "write a python function to sort a list"
        assert classify(task) == classify(task) == classify(task)

    def test_code_deterministic(self):
        assert classify("fix the bug") == "code"
        assert classify("fix the bug") == "code"

    def test_reason_deterministic(self):
        assert classify("explain why X works") == "reason"
        assert classify("explain why X works") == "reason"

    def test_fast_deterministic(self):
        assert classify("quick answer") == "fast"
        assert classify("quick answer") == "fast"

    def test_chat_deterministic(self):
        assert classify("good morning") == "chat"
        assert classify("good morning") == "chat"


# ── Attempts trace ──────────────────────────────────────────────────────────

class TestAttemptsTrace:
    def test_success_has_one_attempt(self, monkeypatch):
        monkeypatch.setattr(
            "helensh.egregor.executor.ollama_call",
            lambda m, t: "valid output here",
        )
        result = run_task("hello")
        assert len(result["attempts"]) == 1
        assert result["attempts"][0]["model"] == "helen-chat"
        assert result["attempts"][0]["review"]["verdict"] == "APPROVE"

    def test_fallback_has_two_attempts(self, monkeypatch):
        call_count = {"n": 0}

        def fake(model, prompt):
            call_count["n"] += 1
            return "" if call_count["n"] == 1 else "valid output"

        monkeypatch.setattr("helensh.egregor.executor.ollama_call", fake)
        result = run_task("hello")
        assert len(result["attempts"]) == 2
        assert result["attempts"][0]["review"]["verdict"] == "REJECT"
        assert result["attempts"][1]["review"]["verdict"] == "APPROVE"

    def test_full_failure_traces_all(self, monkeypatch):
        monkeypatch.setattr("helensh.egregor.executor.ollama_call", lambda m, t: "")
        result = run_task("explain why")  # reason street has 2 models
        assert len(result["attempts"]) == 2
        for a in result["attempts"]:
            assert a["review"]["verdict"] == "REJECT"


# ── CourtLedger wire ───────────────────────────────────────────────────────

class TestCourtLedgerWire:
    """Every Egregor attempt becomes a receipt. No receipt = no reality."""

    def test_success_creates_receipts(self, monkeypatch):
        monkeypatch.setattr(
            "helensh.egregor.executor.ollama_call",
            lambda m, t: "valid output here",
        )
        ledger = CourtLedger(":memory:")

        result = run_task_receipted("hello world", ledger)

        assert result["approved"] is True
        # 1 attempt receipt + 1 result receipt = 2
        assert ledger.count() == 2

        entries = ledger.get_all()
        assert entries[0]["type"] == "EGREGOR_ATTEMPT"
        assert entries[1]["type"] == "EGREGOR_RESULT"
        ledger.close()

    def test_fallback_creates_multiple_attempt_receipts(self, monkeypatch):
        call_count = {"n": 0}

        def fake(model, prompt):
            call_count["n"] += 1
            return "" if call_count["n"] == 1 else "valid output"

        monkeypatch.setattr("helensh.egregor.executor.ollama_call", fake)
        ledger = CourtLedger(":memory:")

        result = run_task_receipted("write code", ledger)

        assert result["approved"] is True
        # 2 attempt receipts + 1 result receipt = 3
        assert ledger.count() == 3

        attempts = ledger.get_by_type("EGREGOR_ATTEMPT")
        assert len(attempts) == 2
        assert attempts[0]["payload"]["verdict"] == "REJECT"
        assert attempts[1]["payload"]["verdict"] == "APPROVE"
        ledger.close()

    def test_full_rejection_receipted(self, monkeypatch):
        monkeypatch.setattr("helensh.egregor.executor.ollama_call", lambda m, t: "")
        ledger = CourtLedger(":memory:")

        result = run_task_receipted("write code", ledger)

        assert result["approved"] is False
        # 2 attempts + 1 result = 3
        assert ledger.count() == 3

        result_entry = ledger.get_by_type("EGREGOR_RESULT")
        assert len(result_entry) == 1
        assert result_entry[0]["payload"]["approved"] is False
        ledger.close()

    def test_chain_integrity_after_egregor(self, monkeypatch):
        monkeypatch.setattr(
            "helensh.egregor.executor.ollama_call",
            lambda m, t: "valid output here",
        )
        ledger = CourtLedger(":memory:")

        run_task_receipted("hello", ledger)
        run_task_receipted("write code", ledger)

        valid, errors = ledger.verify_chain()
        assert valid is True
        assert errors == []
        ledger.close()

    def test_egregor_receipt_has_street(self, monkeypatch):
        monkeypatch.setattr(
            "helensh.egregor.executor.ollama_call",
            lambda m, t: "valid output here",
        )
        ledger = CourtLedger(":memory:")

        run_task_receipted("fix the bug", ledger)

        attempt = ledger.get_by_type("EGREGOR_ATTEMPT")[0]
        assert attempt["payload"]["street"] == "code"
        ledger.close()

    def test_egregor_receipt_has_model(self, monkeypatch):
        monkeypatch.setattr(
            "helensh.egregor.executor.ollama_call",
            lambda m, t: "valid output here",
        )
        ledger = CourtLedger(":memory:")

        run_task_receipted("hello", ledger)

        attempt = ledger.get_by_type("EGREGOR_ATTEMPT")[0]
        assert attempt["payload"]["model"] == "helen-chat"
        ledger.close()

    def test_result_receipt_has_attempt_count(self, monkeypatch):
        monkeypatch.setattr("helensh.egregor.executor.ollama_call", lambda m, t: "")
        ledger = CourtLedger(":memory:")

        run_task_receipted("explain why", ledger)

        result_entry = ledger.get_by_type("EGREGOR_RESULT")[0]
        assert result_entry["payload"]["attempt_count"] == 2
        ledger.close()
