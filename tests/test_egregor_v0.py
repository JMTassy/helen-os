"""HELEN OS — Egregor v0 Tests.

The 5 tests that matter first:
    1. same task → same street (determinism)
    2. code task routes to code street
    3. HAL reject triggers fallback
    4. success returns first allowed result
    5. all rejects returns failure cleanly

Plus: authority always False, receipt hash present, registry sanity.

All Ollama calls mocked. No network.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from helensh.egregor.registry import (
    EGREGOR_ROUTES,
    DEFAULT_STREET,
    REGISTRY_MODELS,
    get_chain,
    list_streets,
)
from helensh.egregor.router import classify
from helensh.egregor.executor import run_task, EgregorResult


# ── Helpers ──────────────────────────────────────────────────────────────────

def _mock_client(responses=None):
    """Create a mock OllamaClient that returns given responses in order."""
    client = MagicMock()
    if responses is None:
        responses = ["model response"]
    client.chat = MagicMock(side_effect=list(responses))
    return client


def _mock_hal(verdicts=None):
    """Create a mock HalReviewer that returns given verdicts in order."""
    hal = MagicMock()
    if verdicts is None:
        verdicts = ["APPROVE"]
    reviews = []
    for v in verdicts:
        reviews.append({
            "verdict": v,
            "kernel_verdict": "ALLOW" if v == "APPROVE" else "DENY",
            "rationale": f"test: {v}",
            "issues": [],
            "confidence": 0.8,
            "authority": False,
        })
    hal.review = MagicMock(side_effect=reviews)
    return hal


# ── 1. Determinism: same task → same street ──────────────────────────────────

class TestDeterminism:
    def test_same_task_same_street(self):
        """Calling classify with the same input must always return the same street."""
        task = "write a python function to sort a list"
        s1 = classify(task)
        s2 = classify(task)
        s3 = classify(task)
        assert s1 == s2 == s3

    def test_same_task_same_street_chat(self):
        task = "how is the weather today"
        assert classify(task) == classify(task)

    def test_same_task_same_street_fast(self):
        task = "quick answer: yes or no"
        assert classify(task) == classify(task)


# ── 2. Code task routes to code street ───────────────────────────────────────

class TestCodeRouting:
    def test_code_keyword_routes_to_code(self):
        assert classify("write code for fibonacci") == "code"

    def test_python_keyword(self):
        assert classify("python script to parse CSV") == "code"

    def test_bug_keyword(self):
        assert classify("fix the bug in the login") == "code"

    def test_function_keyword(self):
        assert classify("create a function for sorting") == "code"

    def test_refactor_keyword(self):
        assert classify("refactor this module") == "code"

    def test_default_is_chat(self):
        assert classify("hello how are you") == "chat"

    def test_fast_keyword(self):
        assert classify("quick answer please") == "fast"

    def test_brief_keyword(self):
        assert classify("give me a brief summary") == "fast"


# ── 3. HAL reject triggers fallback ──────────────────────────────────────────

class TestHalRejectFallback:
    def test_reject_tries_next_model(self):
        """If HAL rejects primary, executor tries fallback model."""
        client = _mock_client(["bad answer", "good answer"])
        hal = _mock_hal(["REJECT", "APPROVE"])

        result = run_task("write code for fibonacci", client=client, hal=hal)

        assert result.allowed
        assert result.model == "qwen2.5-coder:7b"  # second in code chain
        assert result.result == "good answer"
        assert hal.review.call_count == 2

    def test_reject_primary_uses_fallback(self):
        """Verify the fallback chain is followed in order."""
        client = _mock_client(["first", "second"])
        hal = _mock_hal(["REJECT", "APPROVE"])

        result = run_task("write python code", client=client, hal=hal)

        assert result.street == "code"
        # First call: her-coder, second call: qwen2.5-coder
        assert client.chat.call_count == 2


# ── 4. Success returns first allowed result ──────────────────────────────────

class TestFirstAllowed:
    def test_first_allowed_returned(self):
        """If primary is approved by HAL, use it — don't try fallback."""
        client = _mock_client(["primary answer"])
        hal = _mock_hal(["APPROVE"])

        result = run_task("tell me about HELEN", client=client, hal=hal)

        assert result.allowed
        assert result.model == "helen-chat:latest"  # first in chat chain
        assert result.result == "primary answer"
        assert client.chat.call_count == 1
        assert hal.review.call_count == 1

    def test_code_primary_approved(self):
        client = _mock_client(["code output"])
        hal = _mock_hal(["APPROVE"])

        result = run_task("write code for sorting", client=client, hal=hal)

        assert result.street == "code"
        assert result.model == "her-coder:latest"
        assert result.result == "code output"

    def test_fast_primary_approved(self):
        client = _mock_client(["quick answer"])
        hal = _mock_hal(["APPROVE"])

        result = run_task("quick: is this correct?", client=client, hal=hal)

        assert result.street == "fast"
        assert result.model == "helen-ship:latest"


# ── 5. All rejects returns failure cleanly ───────────────────────────────────

class TestAllRejects:
    def test_all_rejected_returns_none(self):
        """If every model in the chain is rejected by HAL, return governed failure."""
        client = _mock_client(["bad1", "bad2"])
        hal = _mock_hal(["REJECT", "REJECT"])

        result = run_task("write code for fibonacci", client=client, hal=hal)

        assert not result.allowed
        assert result.model is None
        assert result.result is None
        assert result.review["verdict"] == "REJECT"

    def test_failure_still_has_receipt(self):
        # fast street has 2 models → need 2 responses + 2 rejects
        client = _mock_client(["bad1", "bad2"])
        hal = _mock_hal(["REJECT", "REJECT"])

        result = run_task("quick yes or no", client=client, hal=hal)

        assert len(result.receipt_hash) == 64  # SHA-256 hex
        assert not result.allowed

    def test_failure_authority_false(self):
        # chat street has 2 models → 2 responses + 2 rejects
        client = _mock_client(["bad1", "bad2"])
        hal = _mock_hal(["REJECT", "REJECT"])

        result = run_task("anything", client=client, hal=hal)

        assert result.authority is False


# ── Authority invariant ──────────────────────────────────────────────────────

class TestAuthorityInvariant:
    def test_success_authority_false(self):
        client = _mock_client(["answer"])
        hal = _mock_hal(["APPROVE"])

        result = run_task("hello", client=client, hal=hal)
        assert result.authority is False

    def test_failure_authority_false(self):
        # chat has 2 models → 2 responses + 2 rejects
        client = _mock_client(["bad1", "bad2"])
        hal = _mock_hal(["REJECT", "REJECT"])

        result = run_task("hello", client=client, hal=hal)
        assert result.authority is False

    def test_cannot_set_authority_true(self):
        """Frozen dataclass with __post_init__ enforcement."""
        r = EgregorResult(
            street="chat",
            model="test",
            result="text",
            review={"verdict": "APPROVE"},
            receipt_hash="a" * 64,
            authority=True,  # try to force True
        )
        assert r.authority is False  # __post_init__ forces it back


# ── Receipt hash ─────────────────────────────────────────────────────────────

class TestReceiptHash:
    def test_receipt_is_sha256(self):
        client = _mock_client(["answer"])
        hal = _mock_hal(["APPROVE"])

        result = run_task("hello", client=client, hal=hal)
        assert len(result.receipt_hash) == 64
        assert all(c in "0123456789abcdef" for c in result.receipt_hash)

    def test_different_tasks_different_receipts(self):
        client1 = _mock_client(["answer1"])
        hal1 = _mock_hal(["APPROVE"])
        r1 = run_task("hello world", client=client1, hal=hal1)

        client2 = _mock_client(["answer2"])
        hal2 = _mock_hal(["APPROVE"])
        r2 = run_task("write code for sorting", client=client2, hal=hal2)

        assert r1.receipt_hash != r2.receipt_hash

    def test_same_inputs_same_receipt(self):
        """Deterministic: same task + same model output + same verdict → same hash."""
        client1 = _mock_client(["exact same output"])
        hal1 = _mock_hal(["APPROVE"])
        r1 = run_task("hello", client=client1, hal=hal1)

        client2 = _mock_client(["exact same output"])
        hal2 = _mock_hal(["APPROVE"])
        r2 = run_task("hello", client=client2, hal=hal2)

        assert r1.receipt_hash == r2.receipt_hash


# ── Registry sanity ──────────────────────────────────────────────────────────

class TestRegistrySanity:
    def test_four_streets_only(self):
        assert set(EGREGOR_ROUTES.keys()) == {"chat", "code", "review", "fast"}

    def test_each_street_has_models(self):
        for street, chain in EGREGOR_ROUTES.items():
            assert len(chain) >= 1, f"Street {street} empty"

    def test_default_street_is_chat(self):
        assert DEFAULT_STREET == "chat"

    def test_get_chain_known(self):
        assert get_chain("code") == ["her-coder:latest", "qwen2.5-coder:7b"]

    def test_get_chain_unknown(self):
        assert get_chain("nonexistent") == []

    def test_list_streets(self):
        assert sorted(list_streets()) == ["chat", "code", "fast", "review"]

    def test_registry_models_nonempty(self):
        assert len(REGISTRY_MODELS) >= 4
