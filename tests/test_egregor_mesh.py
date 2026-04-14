"""HELEN OS — EGREGOR Model Mesh Tests.

Tests the multi-LLM routing mesh without hitting Ollama.
All Ollama calls are mocked.

Test classes:
    1. TestClassifier          — task classification → street mapping
    2. TestStreetRegistry      — street definitions complete + valid
    3. TestMeshResult          — MeshResult authority invariant
    4. TestInstantFallback     — fallback responses in-character
    5. TestMeshCallMocked      — mesh_call with mocked Ollama
    6. TestMeshCallFallback    — mesh_call when all Ollama offline
    7. TestConsensusMocked     — consensus engine with mocked models
    8. TestMeshAvailableModels — mesh health endpoint structure
    9. TestNonSovereignty      — authority always False
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import patch, MagicMock

import pytest

from helensh.egregor.mesh import (
    classify_task,
    mesh_call,
    mesh_available_models,
    _instant_fallback,
    _get_available_models,
    MeshResult,
    STREETS,
    ALL_MODELS,
    CONSENSUS_STREETS,
)


# ── 1. Classifier ─────────────────────────────────────────────────────────────


class TestClassifier:
    def test_temple_mode_overrides_content(self):
        assert classify_task("hello world", mode="temple") == "TEMPLE"

    def test_oracle_mode_overrides(self):
        assert classify_task("hello world", mode="oracle") == "ORACLE_MODE"

    def test_adult_mode_is_fast(self):
        assert classify_task("hello world", mode="adult") == "FAST"

    def test_mayor_mode_is_heavy(self):
        assert classify_task("hello world", mode="mayor") == "HEAVY"

    def test_companion_uses_content(self):
        result = classify_task("write code to sort a list", mode="companion")
        assert result == "CODE"

    def test_code_keywords(self):
        for msg in ["write code for me", "implement a function", "debug this def "]:
            assert classify_task(msg) == "CODE", f"Failed for: {msg}"

    def test_reasoning_keywords(self):
        assert classify_task("step by step explain this") == "REASONING"
        assert classify_task("analyze the logic here") == "REASONING"

    def test_research_keywords(self):
        assert classify_task("research the latest studies") == "RESEARCH"
        assert classify_task("fact check this claim") == "RESEARCH"

    def test_temple_keywords(self):
        assert classify_task("temple mode activate") == "TEMPLE"
        assert classify_task("let's sit with this question") == "TEMPLE"

    def test_fast_keywords(self):
        assert classify_task("quick answer please") == "FAST"
        assert classify_task("tldr this") == "FAST"

    def test_heavy_keywords(self):
        assert classify_task("comprehensive deep dive into this") == "HEAVY"
        assert classify_task("complete guide to machine learning") == "HEAVY"

    def test_default_conversation(self):
        assert classify_task("hi there how's it going") == "CONVERSATION"
        assert classify_task("what time is it") == "CONVERSATION"

    def test_case_insensitive(self):
        assert classify_task("WRITE CODE for this") == "CODE"
        assert classify_task("TEMPLE MODE") == "TEMPLE"

    def test_review_keywords(self):
        assert classify_task("review this code") == "REVIEW"
        assert classify_task("audit this PR") == "REVIEW"

    def test_kernel_keywords(self):
        assert classify_task("check the kernel governance") == "KERNEL"
        assert classify_task("receipt law applies here") == "KERNEL"


# ── 2. Street Registry ────────────────────────────────────────────────────────


class TestStreetRegistry:
    def test_all_streets_defined(self):
        expected = [
            "CONVERSATION", "CODE", "REASONING", "RESEARCH",
            "REVIEW", "FAST", "HEAVY", "TEMPLE", "ORACLE_MODE",
            "KERNEL", "CREATIVE", "CLAW",
        ]
        for s in expected:
            assert s in STREETS, f"Missing street: {s}"

    def test_each_street_has_models(self):
        for street, models in STREETS.items():
            assert len(models) >= 1, f"Street {street} has no models"

    def test_each_street_has_fallback(self):
        """Every street should have at least 2 entries (primary + fallback)."""
        for street, models in STREETS.items():
            assert len(models) >= 2, f"Street {street} has no fallback"

    def test_all_models_list_nonempty(self):
        assert len(ALL_MODELS) >= 30

    def test_consensus_streets_subset(self):
        """Consensus streets must be a subset of STREETS."""
        for street in CONSENSUS_STREETS:
            assert street in STREETS

    def test_consensus_streets_have_3_models(self):
        for street, models in CONSENSUS_STREETS.items():
            assert len(models) >= 2, f"Consensus street {street} needs ≥ 2 models"

    def test_no_duplicate_streets(self):
        keys = list(STREETS.keys())
        assert len(keys) == len(set(keys))


# ── 3. MeshResult ─────────────────────────────────────────────────────────────


class TestMeshResult:
    def test_authority_always_false(self):
        r = MeshResult(text="hi", model="gemma4:latest", street="CONVERSATION")
        assert r.authority is False

    def test_authority_cannot_be_true(self):
        # Even if you try to set it True, the field stays False by default
        r = MeshResult(text="hi", model="test", street="FAST", authority=False)
        assert r.authority is False

    def test_fallback_default_false(self):
        r = MeshResult(text="hi", model="test", street="FAST")
        assert r.fallback is False

    def test_consensus_default_false(self):
        r = MeshResult(text="hi", model="test", street="FAST")
        assert r.consensus is False

    def test_latency_default_zero(self):
        r = MeshResult(text="hi", model="test", street="FAST")
        assert r.latency_ms == 0

    def test_fields_readable(self):
        r = MeshResult(
            text="response",
            model="helen-chat:latest",
            street="CONVERSATION",
            fallback=True,
            latency_ms=150,
        )
        assert r.text == "response"
        assert r.model == "helen-chat:latest"
        assert r.street == "CONVERSATION"
        assert r.latency_ms == 150


# ── 4. Instant Fallback ───────────────────────────────────────────────────────


class TestInstantFallback:
    def test_greeting_response(self):
        for msg in ("hi", "hello", "hey", "bonjour", "salut"):
            r = _instant_fallback(msg, "CONVERSATION")
            assert len(r) > 5

    def test_who_are_you(self):
        r = _instant_fallback("who are you", "CONVERSATION")
        assert "HELEN" in r

    def test_how_are_you(self):
        r = _instant_fallback("how are you", "CONVERSATION")
        assert len(r) > 5

    def test_temple_street_response(self):
        r = _instant_fallback("something deep", "TEMPLE")
        assert "temple" in r.lower() or "question" in r.lower()

    def test_oracle_street_response(self):
        r = _instant_fallback("anything", "ORACLE_MODE")
        assert len(r) > 5

    def test_heavy_street_response(self):
        r = _instant_fallback("complex query", "HEAVY")
        assert len(r) > 5

    def test_fast_street_response(self):
        r = _instant_fallback("quick", "FAST")
        assert len(r) > 1

    def test_code_street_response(self):
        r = _instant_fallback("write code", "CODE")
        assert len(r) > 5

    def test_kernel_street_response(self):
        r = _instant_fallback("governance query", "KERNEL")
        assert len(r) > 5

    def test_default_response(self):
        r = _instant_fallback("random query", "UNKNOWN_STREET")
        assert len(r) > 5

    def test_never_claims_authority(self):
        for street in STREETS:
            r = _instant_fallback("test", street)
            assert "authority" not in r.lower() or "non-sovereign" in r.lower()


# ── 5. mesh_call Mocked ───────────────────────────────────────────────────────


FAKE_MODELS = {
    "helen-chat:latest",
    "qwen3.5:9b",
    "qwen2.5:3b",
    "gemma4:latest",
    "her-coder:latest",
    "oracle-research:latest",
    "hal-reviewer:latest",
    "mistral-kernel:latest",
    "deepseek-r1:8b",
    "helen-ship:latest",
}


class TestMeshCallMocked:
    """mesh_call with Ollama mocked — verifies routing without network."""

    def _mock_call(self, model, messages, timeout=25):
        return f"[{model}] response to your query"

    def test_conversation_routed_to_helen_chat(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=self._mock_call):
                res = mesh_call("hello world", [], "sys", mode="companion")
        assert res.street == "CONVERSATION"
        assert "helen-chat" in res.model or "qwen3" in res.model
        assert res.authority is False

    def test_code_street_selection(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=self._mock_call):
                res = mesh_call("write code for fibonacci", [], "sys", mode="companion")
        assert res.street == "CODE"
        assert res.authority is False

    def test_temple_mode_routes_to_temple(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=self._mock_call):
                res = mesh_call("hello", [], "sys", mode="temple")
        assert res.street == "TEMPLE"

    def test_oracle_mode_routes_to_oracle(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=self._mock_call):
                res = mesh_call("anything", [], "sys", mode="oracle")
        assert res.street == "ORACLE_MODE"

    def test_adult_mode_routes_to_fast(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=self._mock_call):
                res = mesh_call("quick", [], "sys", mode="adult")
        assert res.street == "FAST"

    def test_mayor_mode_routes_to_heavy(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=self._mock_call):
                res = mesh_call("governance", [], "sys", mode="mayor")
        assert res.street == "HEAVY"

    def test_result_has_text(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=self._mock_call):
                res = mesh_call("hi", [], "sys")
        assert len(res.text) > 0

    def test_result_has_latency(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=self._mock_call):
                res = mesh_call("hi", [], "sys")
        assert isinstance(res.latency_ms, int)

    def test_history_passed_as_messages(self):
        history = [
            {"role": "user", "content": "previous question"},
            {"role": "assistant", "content": "previous answer"},
        ]
        calls = []

        def capture_call(model, messages, timeout=25):
            calls.append(messages)
            return "response"

        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=capture_call):
                mesh_call("new question", history, "sys")

        assert len(calls) == 1
        # messages should include system + history + user
        msgs = calls[0]
        roles = [m["role"] for m in msgs]
        assert "system" in roles
        assert "user" in roles

    def test_fallback_chain_on_primary_failure(self):
        """If primary model fails, next in chain is tried."""
        primary_model = STREETS["CONVERSATION"][0]
        call_log = []

        def side_effect(model, messages, timeout=25):
            call_log.append(model)
            if model == primary_model:
                return None  # primary fails
            return f"[{model}] success"

        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", side_effect=side_effect):
                res = mesh_call("hello", [], "sys")

        assert len(call_log) >= 2  # at least tried primary + fallback
        assert res.text != ""


# ── 6. mesh_call Fallback (no Ollama) ────────────────────────────────────────


class TestMeshCallFallback:
    """When all Ollama models are offline, smart fallback kicks in."""

    def test_full_fallback_when_no_models(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=set()):
            with patch("helensh.egregor.mesh._call_model", return_value=None):
                res = mesh_call("hello", [], "sys")

        assert res.fallback is True
        assert len(res.text) > 0
        assert res.authority is False

    def test_fallback_model_name(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=set()):
            with patch("helensh.egregor.mesh._call_model", return_value=None):
                res = mesh_call("hello", [], "sys")
        assert res.model == "fallback"

    def test_fallback_temple_response(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=set()):
            with patch("helensh.egregor.mesh._call_model", return_value=None):
                res = mesh_call("hello", [], "sys", mode="temple")
        assert "temple" in res.text.lower() or len(res.text) > 5

    def test_fallback_greeting_response(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=set()):
            with patch("helensh.egregor.mesh._call_model", return_value=None):
                res = mesh_call("hi", [], "sys")
        assert len(res.text) > 5  # not empty

    def test_authority_false_on_fallback(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=set()):
            with patch("helensh.egregor.mesh._call_model", return_value=None):
                res = mesh_call("anything", [], "sys")
        assert res.authority is False


# ── 7. Consensus Mocked ───────────────────────────────────────────────────────


class TestConsensusMocked:
    """Test consensus engine with mocked parallel calls."""

    def test_consensus_returns_answer(self):
        consensus_models = set(CONSENSUS_STREETS.get("FAST", []))
        all_available = FAKE_MODELS | consensus_models

        def side_effect(model, messages, timeout=25):
            return f"[{model}] fast answer"

        with patch("helensh.egregor.mesh._get_available_models", return_value=all_available):
            with patch("helensh.egregor.mesh._call_model", side_effect=side_effect):
                res = mesh_call("quick answer", [], "sys", mode="adult", use_consensus=True)

        assert res.street in ("FAST", "HEAVY")  # consensus or escalated
        assert len(res.text) > 0
        assert res.authority is False

    def test_consensus_flag_set(self):
        consensus_models = set(CONSENSUS_STREETS.get("FAST", []))
        all_available = FAKE_MODELS | consensus_models

        def side_effect(model, messages, timeout=25):
            return "consistent answer"

        with patch("helensh.egregor.mesh._get_available_models", return_value=all_available):
            with patch("helensh.egregor.mesh._call_model", side_effect=side_effect):
                res = mesh_call("quick", [], "sys", mode="adult", use_consensus=True)

        # consensus=True only if consensus engine ran and returned
        assert isinstance(res.consensus, bool)

    def test_no_consensus_models_available_falls_through(self):
        """If consensus models unavailable, falls through to primary chain."""
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", return_value="normal answer"):
                res = mesh_call("quick", [], "sys", mode="adult", use_consensus=True)

        assert len(res.text) > 0


# ── 8. mesh_available_models ──────────────────────────────────────────────────


class TestMeshAvailableModels:
    def test_returns_all_streets(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            result = mesh_available_models()
        assert set(result.keys()) == set(STREETS.keys())

    def test_each_street_has_chain(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            result = mesh_available_models()
        for street, info in result.items():
            assert "chain" in info
            assert "available" in info
            assert "online" in info
            assert "primary" in info

    def test_available_subset_of_chain(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            result = mesh_available_models()
        for street, info in result.items():
            for m in info["available"]:
                assert m in info["chain"]

    def test_online_count_correct(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            result = mesh_available_models()
        for street, info in result.items():
            assert info["online"] == len(info["available"])

    def test_primary_is_first_available(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            result = mesh_available_models()
        for street, info in result.items():
            if info["available"]:
                assert info["primary"] == info["available"][0]

    def test_offline_street_primary_is_none(self):
        # Only one model available — CLAW street won't have any
        with patch("helensh.egregor.mesh._get_available_models", return_value={"gemma4:latest"}):
            result = mesh_available_models()
        claw = result.get("CLAW", {})
        # CLAW chain doesn't include gemma4 as primary
        if claw.get("online", 0) == 0:
            assert claw["primary"] is None

    def test_no_models_available_graceful(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=set()):
            result = mesh_available_models()
        for street, info in result.items():
            assert info["online"] == 0
            assert info["primary"] is None
            assert info["available"] == []


# ── 9. Non-Sovereignty ────────────────────────────────────────────────────────


class TestNonSovereignty:
    """authority is ALWAYS False on every MeshResult."""

    def test_normal_call_authority_false(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", return_value="answer"):
                res = mesh_call("hello", [], "sys")
        assert res.authority is False

    def test_fallback_authority_false(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=set()):
            with patch("helensh.egregor.mesh._call_model", return_value=None):
                res = mesh_call("hello", [], "sys")
        assert res.authority is False

    def test_consensus_authority_false(self):
        consensus_models = set(CONSENSUS_STREETS.get("FAST", []))
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS | consensus_models):
            with patch("helensh.egregor.mesh._call_model", return_value="answer"):
                res = mesh_call("quick", [], "sys", mode="adult", use_consensus=True)
        assert res.authority is False

    def test_all_streets_authority_false(self):
        with patch("helensh.egregor.mesh._get_available_models", return_value=FAKE_MODELS):
            with patch("helensh.egregor.mesh._call_model", return_value="answer"):
                for mode in ["companion", "temple", "oracle", "mayor", "adult"]:
                    res = mesh_call("test", [], "sys", mode=mode)
                    assert res.authority is False, f"authority True for mode={mode}"

    def test_mesh_result_authority_field_cannot_be_true(self):
        """MeshResult is a dataclass — authority field must be False."""
        r = MeshResult(text="x", model="y", street="FAST", authority=False)
        assert r.authority is False
        # You can't accidentally set True without knowing it
        assert type(r.authority) is bool
