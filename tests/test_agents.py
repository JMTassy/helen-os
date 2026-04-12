"""Tests for HELEN OS multi-agent infrastructure.

All Ollama HTTP calls are mocked — tests pass without Ollama running.

Coverage:
  - OllamaClient (adapters/ollama.py)
  - HerCoder (agents/her_coder.py)
  - HalReviewer (agents/hal_reviewer.py)
  - ClawAgent / ClawAction (agents/claw.py)
  - TempleSandbox / TempleSession / Claim (sandbox/temple.py)
  - Kernel integration: claw_external in KNOWN_ACTIONS + WRITE_ACTIONS
"""
import json
import copy
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from helensh.adapters.ollama import OllamaClient, OllamaError
from helensh.agents.her_coder import (
    HerCoder,
    FALLBACK_PROPOSAL,
    HER_ACTIONS,
    MODEL_PRIMARY as HER_MODEL_PRIMARY,
    MODEL_FALLBACK as HER_MODEL_FALLBACK,
    _extract_json,
    _normalize_proposal,
)
from helensh.agents.hal_reviewer import (
    HalReviewer,
    FALLBACK_REVIEW,
    HAL_VERDICTS,
    VERDICT_TO_KERNEL,
    MODEL_PRIMARY as HAL_MODEL_PRIMARY,
    _normalize_review,
)
from helensh.agents.claw import (
    ClawAgent,
    ClawAction,
    claw_governor_gate,
    CLAW_KNOWN_SKILLS,
    CLAW_WRITE_SKILLS,
)
from helensh.sandbox.temple import (
    TempleSandbox,
    TempleSession,
    Claim,
    GENESIS_HASH,
    DEFAULT_ITERATIONS,
    DEFAULT_APPROVAL_THRESHOLD,
    _make_proposal_receipt,
    _make_review_receipt,
)
from helensh.sandbox.evolve import (
    EvolutionLoop,
    EvolveSession,
    EvolveTurn,
    DEFAULT_EVOLVE_ITERATIONS,
    DEFAULT_PROMOTION_THRESHOLD,
    _make_feedback_receipt,
    _build_evolve_prompt,
    _compose_feedback,
)
from helensh.kernel import KNOWN_ACTIONS, WRITE_ACTIONS, init_session, governor


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def base_state():
    return init_session("test-session", "tester", "/tmp")


def _make_her_response(action="write_code", target="module.py", confidence=0.8) -> str:
    return json.dumps({
        "action": action,
        "target": target,
        "payload": {
            "description": "Test proposal",
            "code": "def foo(): pass",
            "rationale": "Because tests",
        },
        "confidence": confidence,
        "authority": False,
    })


def _make_hal_response(verdict="APPROVE", confidence=0.85) -> str:
    return json.dumps({
        "verdict": verdict,
        "kernel_verdict": VERDICT_TO_KERNEL[verdict],
        "rationale": "Looks good",
        "issues": [],
        "confidence": confidence,
        "authority": False,
    })


def _mock_client(chat_response: str = "", has_model_return: bool = True) -> MagicMock:
    """Create a mock OllamaClient."""
    mock = MagicMock(spec=OllamaClient)
    mock.chat.return_value = chat_response
    mock.has_model.return_value = has_model_return
    mock.is_available.return_value = True
    mock.list_models.return_value = ["her-coder", "hal-reviewer", "gemma4"]
    return mock


# ═══════════════════════════════════════════════════════════════════════
# OllamaClient tests
# ═══════════════════════════════════════════════════════════════════════


class TestOllamaClient:
    """Tests for the OllamaClient HTTP adapter."""

    def test_init_defaults(self):
        client = OllamaClient()
        assert client.base_url == "http://localhost:11434"
        assert client.timeout == 120

    def test_init_custom(self):
        client = OllamaClient(base_url="http://0.0.0.0:11435", timeout=30)
        assert client.base_url == "http://0.0.0.0:11435"
        assert client.timeout == 30

    def test_base_url_trailing_slash_stripped(self):
        client = OllamaClient(base_url="http://localhost:11434/")
        assert client.base_url == "http://localhost:11434"

    def test_is_available_true(self):
        client = OllamaClient()
        with patch.object(client, "_get", return_value={"models": []}):
            assert client.is_available() is True

    def test_is_available_false_on_error(self):
        client = OllamaClient()
        with patch.object(client, "_get", side_effect=OllamaError("no conn")):
            assert client.is_available() is False

    def test_has_model_true(self):
        client = OllamaClient()
        with patch.object(client, "_get", return_value={"models": [{"name": "gemma4"}]}):
            assert client.has_model("gemma4") is True

    def test_has_model_false(self):
        client = OllamaClient()
        with patch.object(client, "_get", return_value={"models": [{"name": "other"}]}):
            assert client.has_model("gemma4") is False

    def test_has_model_false_on_error(self):
        client = OllamaClient()
        with patch.object(client, "_get", side_effect=OllamaError("down")):
            assert client.has_model("gemma4") is False

    def test_list_models(self):
        client = OllamaClient()
        with patch.object(client, "_get", return_value={"models": [{"name": "a"}, {"name": "b"}]}):
            models = client.list_models()
        assert models == ["a", "b"]

    def test_list_models_empty(self):
        client = OllamaClient()
        with patch.object(client, "_get", return_value={"models": []}):
            assert client.list_models() == []

    def test_generate_returns_response(self):
        client = OllamaClient()
        with patch.object(client, "_post", return_value={"response": "hello"}):
            result = client.generate("gemma4", "prompt")
        assert result == "hello"

    def test_chat_returns_content(self):
        client = OllamaClient()
        with patch.object(client, "_post", return_value={"message": {"content": "world"}}):
            result = client.chat("gemma4", [{"role": "user", "content": "hi"}])
        assert result == "world"

    def test_chat_with_system_prepends_message(self):
        client = OllamaClient()
        captured = {}
        def fake_post(path, body):
            captured["body"] = body
            return {"message": {"content": "ok"}}
        with patch.object(client, "_post", side_effect=fake_post):
            client.chat("gemma4", [{"role": "user", "content": "hi"}], system="be good")
        msgs = captured["body"]["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "be good"
        assert msgs[1]["role"] == "user"

    def test_post_raises_ollama_error_on_urlerror(self):
        import urllib.error
        client = OllamaClient()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with pytest.raises(OllamaError, match="Cannot reach"):
                client._post("/api/generate", {})

    def test_get_raises_ollama_error_on_urlerror(self):
        import urllib.error
        client = OllamaClient()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with pytest.raises(OllamaError, match="Cannot reach"):
                client._get("/api/tags")

    def test_ollama_error_is_exception(self):
        err = OllamaError("test error")
        assert isinstance(err, Exception)
        assert str(err) == "test error"


# ═══════════════════════════════════════════════════════════════════════
# HerCoder tests
# ═══════════════════════════════════════════════════════════════════════


class TestHerCoder:
    """Tests for HER coding sub-agent."""

    def test_propose_returns_valid_proposal(self, base_state):
        mock = _mock_client(chat_response=_make_her_response())
        her = HerCoder(client=mock)
        proposal = her.propose(base_state, "refactor the governor")
        assert proposal["action"] in HER_ACTIONS
        assert proposal["authority"] is False
        assert "payload" in proposal
        assert 0.0 <= proposal["confidence"] <= 1.0

    def test_authority_always_false(self, base_state):
        # Even if model returns authority=True, it must be forced False
        bad_response = json.dumps({
            "action": "write_code",
            "target": "foo.py",
            "payload": {"description": "bad", "code": None, "rationale": ""},
            "confidence": 0.9,
            "authority": True,  # model misbehaves
        })
        mock = _mock_client(chat_response=bad_response)
        her = HerCoder(client=mock)
        proposal = her.propose(base_state, "do something")
        assert proposal["authority"] is False

    def test_fallback_on_ollama_error(self, base_state):
        mock = MagicMock(spec=OllamaClient)
        mock.has_model.return_value = True
        mock.chat.side_effect = OllamaError("connection refused")
        her = HerCoder(client=mock)
        proposal = her.propose(base_state, "anything")
        assert proposal["action"] == "chat"
        assert proposal["authority"] is False
        assert proposal["fallback"] is True
        assert proposal["confidence"] == 0.0

    def test_fallback_proposal_is_copy_not_singleton(self, base_state):
        mock = MagicMock(spec=OllamaClient)
        mock.has_model.return_value = True
        mock.chat.side_effect = OllamaError("down")
        her = HerCoder(client=mock)
        p1 = her.propose(base_state, "a")
        p2 = her.propose(base_state, "b")
        assert p1 is not p2  # separate dicts
        assert p1 == p2  # same content

    def test_non_json_response_wrapped_as_chat(self, base_state):
        mock = _mock_client(chat_response="I would write a function that...")
        her = HerCoder(client=mock)
        proposal = her.propose(base_state, "write something")
        assert proposal["action"] == "chat"
        assert proposal["authority"] is False
        assert proposal["fallback"] is False

    def test_unknown_action_coerced_to_chat(self):
        raw = {"action": "launch_missiles", "target": "x", "payload": {}, "confidence": 0.9}
        result = _normalize_proposal(raw, "test-model")
        assert result["action"] == "chat"

    def test_confidence_clamped_high(self):
        raw = {"action": "chat", "target": "x", "payload": {}, "confidence": 99.0}
        result = _normalize_proposal(raw, "test-model")
        assert result["confidence"] == 1.0

    def test_confidence_clamped_low(self):
        raw = {"action": "chat", "target": "x", "payload": {}, "confidence": -5.0}
        result = _normalize_proposal(raw, "test-model")
        assert result["confidence"] == 0.0

    def test_confidence_bad_type_defaults(self):
        raw = {"action": "chat", "target": "x", "payload": {}, "confidence": "high"}
        result = _normalize_proposal(raw, "test-model")
        assert result["confidence"] == 0.5

    def test_extract_json_direct(self):
        text = '{"action": "chat"}'
        result = _extract_json(text)
        assert result == {"action": "chat"}

    def test_extract_json_with_preamble(self):
        text = 'Here is the JSON: {"action": "chat", "x": 1}'
        result = _extract_json(text)
        assert result["action"] == "chat"

    def test_extract_json_none_on_invalid(self):
        assert _extract_json("not json at all") is None

    def test_model_primary_constant(self):
        assert HER_MODEL_PRIMARY == "her-coder"

    def test_model_fallback_constant(self):
        assert HER_MODEL_FALLBACK == "gemma4"

    def test_known_actions_coverage(self):
        required = {"write_code", "refactor", "analyse", "explain", "scaffold", "search_code", "chat"}
        assert required == HER_ACTIONS

    def test_fallback_proposal_authority_false(self):
        assert FALLBACK_PROPOSAL["authority"] is False

    def test_fallback_proposal_zero_confidence(self):
        assert FALLBACK_PROPOSAL["confidence"] == 0.0

    def test_payload_non_dict_coerced(self):
        raw = {"action": "chat", "target": "x", "payload": "just a string", "confidence": 0.5}
        result = _normalize_proposal(raw, "test-model")
        assert isinstance(result["payload"], dict)


# ═══════════════════════════════════════════════════════════════════════
# HalReviewer tests
# ═══════════════════════════════════════════════════════════════════════


class TestHalReviewer:
    """Tests for HAL code review sub-agent."""

    def test_review_returns_valid_structure(self, base_state):
        mock = _mock_client(chat_response=_make_hal_response())
        hal = HalReviewer(client=mock)
        proposal = {"action": "write_code", "target": "f.py", "payload": {}, "authority": False}
        review = hal.review(proposal, base_state)
        assert review["verdict"] in HAL_VERDICTS
        assert review["kernel_verdict"] in {"ALLOW", "DENY", "PENDING"}
        assert review["authority"] is False

    def test_authority_always_false_in_review(self, base_state):
        bad_response = json.dumps({
            "verdict": "APPROVE",
            "kernel_verdict": "ALLOW",
            "rationale": "ok",
            "issues": [],
            "confidence": 0.9,
            "authority": True,  # model misbehaves
        })
        mock = _mock_client(chat_response=bad_response)
        hal = HalReviewer(client=mock)
        proposal = {"action": "chat", "target": "x", "payload": {}, "authority": False}
        review = hal.review(proposal, base_state)
        assert review["authority"] is False

    def test_proposal_with_authority_true_immediately_rejected(self, base_state):
        """Structural authority guard: proposal claiming authority → immediate REJECT."""
        hal = HalReviewer(client=MagicMock(spec=OllamaClient))
        proposal = {"action": "chat", "target": "x", "payload": {}, "authority": True}
        review = hal.review(proposal, base_state)
        assert review["verdict"] == "REJECT"
        assert review["kernel_verdict"] == "DENY"
        assert review["confidence"] == 1.0

    def test_fallback_on_ollama_error(self, base_state):
        mock = MagicMock(spec=OllamaClient)
        mock.has_model.return_value = True
        mock.chat.side_effect = OllamaError("down")
        hal = HalReviewer(client=mock)
        proposal = {"action": "chat", "target": "x", "payload": {}, "authority": False}
        review = hal.review(proposal, base_state)
        assert review["verdict"] == "REJECT"
        assert review["kernel_verdict"] == "DENY"
        assert review["fallback"] is True
        assert review["authority"] is False

    def test_non_json_response_fail_closed(self, base_state):
        mock = _mock_client(chat_response="I think we should approve this...")
        hal = HalReviewer(client=mock)
        proposal = {"action": "write_code", "target": "f.py", "payload": {}, "authority": False}
        review = hal.review(proposal, base_state)
        assert review["verdict"] == "REJECT"
        assert review["kernel_verdict"] == "DENY"

    def test_verdict_mapping_approve(self):
        raw = {"verdict": "APPROVE", "kernel_verdict": "ALLOW", "rationale": "ok",
               "issues": [], "confidence": 0.9}
        result = _normalize_review(raw, "test-model")
        assert result["verdict"] == "APPROVE"
        assert result["kernel_verdict"] == "ALLOW"

    def test_verdict_mapping_reject(self):
        raw = {"verdict": "REJECT", "kernel_verdict": "DENY", "rationale": "no",
               "issues": ["bad"], "confidence": 0.1}
        result = _normalize_review(raw, "test-model")
        assert result["kernel_verdict"] == "DENY"

    def test_verdict_mapping_request_changes(self):
        raw = {"verdict": "REQUEST_CHANGES", "kernel_verdict": "PENDING", "rationale": "fix it",
               "issues": ["missing test"], "confidence": 0.5}
        result = _normalize_review(raw, "test-model")
        assert result["kernel_verdict"] == "PENDING"

    def test_unknown_verdict_fail_closed_to_deny(self):
        raw = {"verdict": "MAYBE", "rationale": "dunno", "issues": [], "confidence": 0.5}
        result = _normalize_review(raw, "test-model")
        assert result["verdict"] == "REJECT"
        assert result["kernel_verdict"] == "DENY"

    def test_kernel_verdict_derived_from_mapping_not_model(self):
        """Model's kernel_verdict field is ignored; derived from verdict mapping."""
        raw = {
            "verdict": "APPROVE",
            "kernel_verdict": "DENY",  # model lies
            "rationale": "ok",
            "issues": [],
            "confidence": 0.9,
        }
        result = _normalize_review(raw, "test-model")
        assert result["kernel_verdict"] == "ALLOW"  # mapping wins

    def test_map_to_kernel_verdict(self, base_state):
        mock = _mock_client(chat_response=_make_hal_response("APPROVE", 0.9))
        hal = HalReviewer(client=mock)
        proposal = {"action": "chat", "target": "x", "payload": {}, "authority": False}
        review = hal.review(proposal, base_state)
        kv = hal.map_to_kernel_verdict(review)
        assert kv == "ALLOW"

    def test_map_to_kernel_verdict_unknown_defaults_deny(self):
        hal = HalReviewer(client=MagicMock(spec=OllamaClient))
        assert hal.map_to_kernel_verdict({"verdict": "UNKNOWN"}) == "DENY"

    def test_fallback_review_is_fail_closed(self):
        assert FALLBACK_REVIEW["verdict"] == "REJECT"
        assert FALLBACK_REVIEW["kernel_verdict"] == "DENY"
        assert FALLBACK_REVIEW["authority"] is False

    def test_issues_always_list(self):
        raw = {"verdict": "REJECT", "rationale": "no", "issues": "just one issue", "confidence": 0.3}
        result = _normalize_review(raw, "test-model")
        assert isinstance(result["issues"], list)


# ═══════════════════════════════════════════════════════════════════════
# ClawAgent / ClawAction tests
# ═══════════════════════════════════════════════════════════════════════


class TestClawAgent:
    """Tests for CLAW skills agent."""

    def test_clawaction_require_approval_always_true(self):
        action = ClawAction(skill="ping", payload={"host": "localhost"}, rationale="test")
        assert action.require_approval is True

    def test_clawaction_authority_always_false(self):
        action = ClawAction(skill="ping", payload={"host": "localhost"}, rationale="test")
        assert action.authority is False

    def test_clawaction_cannot_override_require_approval(self):
        action = ClawAction(skill="ping", payload={}, rationale="")
        # Frozen via __post_init__ — structural invariant
        assert action.require_approval is True

    def test_clawaction_to_kernel_proposal(self):
        action = ClawAction(skill="telegram_send", payload={"chat_id": "42", "text": "hi"}, rationale="test")
        proposal = action.to_kernel_proposal()
        assert proposal["action"] == "claw_external"
        assert proposal["authority"] is False
        assert proposal["target"] == "telegram_send"
        assert proposal["payload"]["require_approval"] is True

    def test_claw_governor_gate_known_returns_pending(self):
        for skill in CLAW_KNOWN_SKILLS:
            action = ClawAction(skill=skill, payload={}, rationale="test")
            assert claw_governor_gate(action) == "PENDING"

    def test_claw_governor_gate_unknown_returns_deny(self):
        action = ClawAction(skill="launch_missiles", payload={}, rationale="nope")
        assert claw_governor_gate(action) == "DENY"

    def test_plan_telegram_send(self, base_state):
        claw = ClawAgent()
        action = claw.plan("send hello to telegram", base_state)
        assert action.skill == "telegram_send"
        assert action.require_approval is True
        assert action.authority is False

    def test_plan_telegram_read(self, base_state):
        claw = ClawAgent()
        action = claw.plan("read telegram messages", base_state)
        assert action.skill == "telegram_read"

    def test_plan_web_fetch(self, base_state):
        claw = ClawAgent()
        action = claw.plan("fetch https://example.com", base_state)
        assert action.skill == "web_fetch"
        assert action.payload["url"] == "https://example.com"

    def test_plan_notify(self, base_state):
        claw = ClawAgent()
        action = claw.plan("notify me about the build", base_state)
        assert action.skill == "notify"

    def test_plan_ping(self, base_state):
        claw = ClawAgent()
        action = claw.plan("ping google.com", base_state)
        assert action.skill == "ping"
        assert action.payload["host"] == "google.com"

    def test_plan_unknown_defaults_to_ping_localhost(self, base_state):
        claw = ClawAgent()
        action = claw.plan("do something weird", base_state)
        assert action.skill == "ping"
        assert action.payload["host"] == "localhost"

    def test_plan_description_telegram_send(self, base_state):
        claw = ClawAgent()
        action = ClawAction(skill="telegram_send", payload={"chat_id": "42", "text": "hi"}, rationale="")
        desc = claw.plan_description(action)
        assert desc["type"] == "telegram_send"
        assert desc["status"] == "PLANNED"

    def test_plan_description_web_fetch(self):
        claw = ClawAgent()
        action = ClawAction(skill="web_fetch", payload={"url": "https://x.com", "method": "GET"}, rationale="")
        desc = claw.plan_description(action)
        assert desc["type"] == "web_fetch"
        assert desc["status"] == "PLANNED"

    def test_plan_description_unknown_skill(self):
        claw = ClawAgent()
        action = ClawAction(skill="unknown_skill", payload={}, rationale="")
        desc = claw.plan_description(action)
        assert desc["status"] == "UNKNOWN_SKILL"

    def test_gate_method(self, base_state):
        claw = ClawAgent()
        action = claw.plan("send a telegram message", base_state)
        assert claw.gate(action) == "PENDING"

    def test_claw_known_skills(self):
        required = {"telegram_send", "telegram_read", "web_fetch", "notify", "ping"}
        assert required == CLAW_KNOWN_SKILLS

    def test_claw_write_skills_are_subset(self):
        assert CLAW_WRITE_SKILLS.issubset(CLAW_KNOWN_SKILLS)


# ═══════════════════════════════════════════════════════════════════════
# Kernel integration: claw_external
# ═══════════════════════════════════════════════════════════════════════


class TestKernelClawIntegration:
    """claw_external must be registered in KNOWN_ACTIONS + WRITE_ACTIONS."""

    def test_claw_external_in_known_actions(self):
        assert "claw_external" in KNOWN_ACTIONS

    def test_claw_external_in_write_actions(self):
        assert "claw_external" in WRITE_ACTIONS

    def test_claw_external_yields_pending_from_governor(self, base_state):
        proposal = {
            "action": "claw_external",
            "target": "telegram_send",
            "payload": {"skill": "telegram_send", "params": {}, "require_approval": True},
            "authority": False,
        }
        verdict = governor(proposal, base_state)
        assert verdict == "PENDING"

    def test_claw_proposal_to_kernel_routing(self, base_state):
        claw = ClawAgent()
        action = claw.plan("send telegram message", base_state)
        proposal = action.to_kernel_proposal()
        verdict = governor(proposal, base_state)
        assert verdict == "PENDING"

    def test_authority_claim_in_claw_proposal_denied(self, base_state):
        proposal = {
            "action": "claw_external",
            "target": "telegram_send",
            "payload": {},
            "authority": True,  # constitutional violation
        }
        verdict = governor(proposal, base_state)
        assert verdict == "DENY"


# ═══════════════════════════════════════════════════════════════════════
# TempleSandbox tests
# ═══════════════════════════════════════════════════════════════════════


def _make_her_stub(action="write_code", confidence=0.8) -> HerCoder:
    """HerCoder stub that always returns the same proposal (no Ollama)."""
    her = MagicMock(spec=HerCoder)
    her.propose.return_value = {
        "action": action,
        "target": "test_module.py",
        "payload": {
            "description": f"Stub proposal for {action}",
            "code": "def stub(): pass",
            "rationale": "Stub",
        },
        "confidence": confidence,
        "authority": False,
        "model": "stub",
        "fallback": False,
    }
    return her


def _make_hal_stub(verdict="APPROVE", confidence=0.85) -> HalReviewer:
    """HalReviewer stub that always returns the same review (no Ollama)."""
    hal = MagicMock(spec=HalReviewer)
    hal.review.return_value = {
        "verdict": verdict,
        "kernel_verdict": VERDICT_TO_KERNEL.get(verdict, "DENY"),
        "rationale": f"Stub review: {verdict}",
        "issues": [],
        "confidence": confidence,
        "authority": False,
        "model": "stub",
        "fallback": False,
    }
    return hal


class TestTempleSandbox:
    """Tests for TEMPLE SANDBOX brainstorming loop."""

    def test_brainstorm_returns_temple_session(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("design a governor gate", iterations=3)
        assert isinstance(session, TempleSession)

    def test_receipt_chain_length_equals_iterations_times_two(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        for n in [1, 3, 5, 7]:
            session = temple.brainstorm("task", iterations=n)
            assert len(session.receipt_chain) == n * 2, f"failed for n={n}"

    def test_claims_count_equals_iterations(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=4)
        assert len(session.claims) == 4

    def test_eligible_claims_on_approve_above_threshold(self):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.9)
        temple = TempleSandbox(her, hal, approval_threshold=0.7)
        session = temple.brainstorm("task", iterations=3)
        assert len(session.eligible_claims) == 3
        for claim in session.eligible_claims:
            assert claim.eligible is True

    def test_no_eligible_claims_on_reject(self):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="REJECT", confidence=0.9)
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=3)
        assert len(session.eligible_claims) == 0

    def test_no_eligible_claims_below_threshold(self):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.5)
        temple = TempleSandbox(her, hal, approval_threshold=0.7)
        session = temple.brainstorm("task", iterations=3)
        assert len(session.eligible_claims) == 0

    def test_base_state_never_mutated(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        state = init_session()
        state_copy = copy.deepcopy(state)
        temple.brainstorm("task", state=state, iterations=5)
        assert state == state_copy  # state unchanged

    def test_authority_always_false_in_receipts(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=3)
        for receipt in session.receipt_chain:
            assert receipt["authority"] is False

    def test_receipt_chain_integrity(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=4)
        assert temple.verify_session(session) is True

    def test_verify_session_detects_tampering(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=3)

        # Mutate a receipt's content (not the hash) — should fail verification
        mutated = list(session.receipt_chain)
        mutated[0] = {**mutated[0], "task": "tampered task"}
        tampered = TempleSession(
            task=session.task,
            iterations=session.iterations,
            threshold=session.threshold,
            claims=session.claims,
            eligible_claims=session.eligible_claims,
            receipt_chain=tuple(mutated),
            session_hash=session.session_hash,
        )
        assert temple.verify_session(tampered) is False

    def test_verify_session_detects_wrong_chain_length(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=3)
        # Drop one receipt
        truncated = TempleSession(
            task=session.task,
            iterations=session.iterations,
            threshold=session.threshold,
            claims=session.claims,
            eligible_claims=session.eligible_claims,
            receipt_chain=session.receipt_chain[:-1],
            session_hash=session.session_hash,
        )
        assert temple.verify_session(truncated) is False

    def test_genesis_hash_starts_chain(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=2)
        assert session.receipt_chain[0]["previous_hash"] == GENESIS_HASH

    def test_session_hash_is_string(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=2)
        assert isinstance(session.session_hash, str)
        assert len(session.session_hash) == 64  # SHA-256 hex

    def test_claim_fields(self):
        her = _make_her_stub(action="refactor", confidence=0.75)
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.85)
        temple = TempleSandbox(her, hal, approval_threshold=0.7)
        session = temple.brainstorm("task", iterations=1)
        claim = session.claims[0]
        assert claim.id == 0
        assert claim.turn == 0
        assert claim.action == "refactor"
        assert claim.confidence == 0.85  # from HAL, not HER
        assert claim.verdict == "APPROVE"
        assert claim.eligible is True
        assert isinstance(claim.receipt_hash, str)

    def test_claim_is_frozen(self):
        claim = Claim(id=0, turn=0, text="test", action="chat",
                      confidence=0.9, verdict="APPROVE", eligible=True, receipt_hash="abc")
        with pytest.raises((AttributeError, TypeError)):
            claim.eligible = False  # type: ignore[misc]

    def test_session_is_frozen(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=1)
        with pytest.raises((AttributeError, TypeError)):
            session.task = "tampered"  # type: ignore[misc]

    def test_temple_receipt_types(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=2)
        # Alternating: TEMPLE_PROPOSAL, TEMPLE_REVIEW, TEMPLE_PROPOSAL, TEMPLE_REVIEW
        types = [r["type"] for r in session.receipt_chain]
        assert types == ["TEMPLE_PROPOSAL", "TEMPLE_REVIEW", "TEMPLE_PROPOSAL", "TEMPLE_REVIEW"]

    def test_approval_threshold_clamped(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal, approval_threshold=1.5)
        assert temple.approval_threshold == 1.0
        temple2 = TempleSandbox(her, hal, approval_threshold=-0.5)
        assert temple2.approval_threshold == 0.0

    def test_session_task_stored(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("my special task", iterations=1)
        assert session.task == "my special task"

    def test_session_iterations_stored(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        session = temple.brainstorm("task", iterations=7)
        assert session.iterations == 7

    def test_her_called_once_per_iteration(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        temple.brainstorm("task", iterations=5)
        assert her.propose.call_count == 5

    def test_hal_called_once_per_iteration(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        temple = TempleSandbox(her, hal)
        temple.brainstorm("task", iterations=5)
        assert hal.review.call_count == 5


# ── Receipt unit tests ────────────────────────────────────────────────


class TestTempleReceipts:
    """Unit tests for temple receipt construction."""

    def test_proposal_receipt_has_correct_type(self):
        r = _make_proposal_receipt(0, "task", {"action": "chat"}, GENESIS_HASH)
        assert r["type"] == "TEMPLE_PROPOSAL"

    def test_proposal_receipt_previous_hash(self):
        r = _make_proposal_receipt(0, "task", {}, GENESIS_HASH)
        assert r["previous_hash"] == GENESIS_HASH

    def test_proposal_receipt_authority_false(self):
        r = _make_proposal_receipt(0, "task", {}, GENESIS_HASH)
        assert r["authority"] is False

    def test_proposal_receipt_has_hash(self):
        r = _make_proposal_receipt(0, "task", {}, GENESIS_HASH)
        assert "receipt_hash" in r
        assert len(r["receipt_hash"]) == 64

    def test_proposal_receipt_deterministic(self):
        r1 = _make_proposal_receipt(0, "task", {"action": "chat"}, GENESIS_HASH)
        r2 = _make_proposal_receipt(0, "task", {"action": "chat"}, GENESIS_HASH)
        assert r1["receipt_hash"] == r2["receipt_hash"]

    def test_review_receipt_has_correct_type(self):
        r = _make_review_receipt(0, "task", {"action": "chat"},
                                 {"verdict": "APPROVE", "confidence": 0.9, "rationale": "ok"},
                                 GENESIS_HASH)
        assert r["type"] == "TEMPLE_REVIEW"

    def test_review_receipt_authority_false(self):
        r = _make_review_receipt(0, "task", {}, {"verdict": "REJECT", "confidence": 0.0, "rationale": ""}, GENESIS_HASH)
        assert r["authority"] is False

    def test_review_receipt_chain_link(self):
        p_receipt = _make_proposal_receipt(0, "task", {}, GENESIS_HASH)
        r_receipt = _make_review_receipt(0, "task", {}, {"verdict": "APPROVE", "confidence": 0.9, "rationale": ""}, p_receipt["receipt_hash"])
        assert r_receipt["previous_hash"] == p_receipt["receipt_hash"]


# ═══════════════════════════════════════════════════════════════════════
# EvolutionLoop tests
# ═══════════════════════════════════════════════════════════════════════


def _alternating_hal(n_approve: int = 0, n_reject: int = 0) -> HalReviewer:
    """HAL stub that alternates REJECT/APPROVE based on call count."""
    hal = MagicMock(spec=HalReviewer)
    call_count = [0]

    def side_effect(proposal, state):
        i = call_count[0]
        call_count[0] += 1
        # First n_reject calls → REJECT, then → APPROVE
        if i < n_reject:
            return {
                "verdict": "REJECT",
                "kernel_verdict": "DENY",
                "rationale": f"Iteration {i}: needs improvement",
                "issues": [f"issue at turn {i}"],
                "confidence": 0.3,
                "authority": False,
                "model": "stub",
                "fallback": False,
            }
        return {
            "verdict": "APPROVE",
            "kernel_verdict": "ALLOW",
            "rationale": "Looks good",
            "issues": [],
            "confidence": 0.9,
            "authority": False,
            "model": "stub",
            "fallback": False,
        }

    hal.review.side_effect = side_effect
    return hal


class TestEvolutionLoop:
    """Tests for receipted self-evolution loop."""

    def test_run_returns_evolve_session(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        loop = EvolutionLoop(her, hal, iterations=3)
        session = loop.run("optimise the governor")
        assert isinstance(session, EvolveSession)

    def test_iterations_run_matches_requested(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        loop = EvolutionLoop(her, hal, iterations=5)
        session = loop.run("task")
        assert session.iterations_run == 5

    def test_trajectory_length_matches_iterations(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        loop = EvolutionLoop(her, hal, iterations=4)
        session = loop.run("task")
        assert len(session.trajectory) == 4

    def test_receipt_chain_grows_with_feedback(self):
        """Rejected turns add a feedback receipt → 3 receipts per rejected iteration."""
        her = _make_her_stub()
        hal = _alternating_hal(n_reject=3, n_approve=0)  # all reject
        loop = EvolutionLoop(her, hal, iterations=3)
        session = loop.run("task")
        # 3 turns × (proposal + review + feedback) = 9
        assert len(session.receipt_chain) == 9

    def test_approved_turns_two_receipts_only(self):
        """Approved turns: 2 receipts (proposal + review), no feedback receipt."""
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.9)
        loop = EvolutionLoop(her, hal, iterations=3)
        session = loop.run("task")
        # 3 turns × 2 receipts = 6
        assert len(session.receipt_chain) == 6

    def test_base_state_never_mutated(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        loop = EvolutionLoop(her, hal, iterations=5)
        state = init_session()
        state_before = copy.deepcopy(state)
        loop.run("task", state=state)
        assert state == state_before

    def test_authority_always_false_in_all_receipts(self):
        her = _make_her_stub()
        hal = _alternating_hal(n_reject=2, n_approve=0)
        loop = EvolutionLoop(her, hal, iterations=3)
        session = loop.run("task")
        for r in session.receipt_chain:
            assert r["authority"] is False

    def test_eligible_claims_on_approve_above_threshold(self):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.9)
        loop = EvolutionLoop(her, hal, iterations=4, approval_threshold=0.7)
        session = loop.run("task")
        assert len(session.eligible_claims) == 4
        for c in session.eligible_claims:
            assert c.eligible is True

    def test_no_eligible_claims_on_all_reject(self):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="REJECT", confidence=0.3)
        loop = EvolutionLoop(her, hal, iterations=4)
        session = loop.run("task")
        assert len(session.eligible_claims) == 0
        assert session.best_claim is None

    def test_promoted_claims_above_promotion_threshold(self):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.9)
        loop = EvolutionLoop(her, hal, approval_threshold=0.7, promotion_threshold=0.85)
        session = loop.run("task", )
        assert len(session.promoted_claims) > 0
        for c in session.promoted_claims:
            assert c.confidence >= 0.85

    def test_best_claim_is_highest_confidence(self):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.88)
        loop = EvolutionLoop(her, hal)
        session = loop.run("task")
        if session.best_claim:
            assert session.best_claim.confidence == max(c.confidence for c in session.eligible_claims)

    def test_failure_analysis_populated_on_reject(self):
        her = _make_her_stub()
        hal = _alternating_hal(n_reject=2, n_approve=0)
        loop = EvolutionLoop(her, hal, iterations=3)
        session = loop.run("task")
        assert len(session.failure_analysis) >= 2
        for turn_num, rationale in session.failure_analysis:
            assert isinstance(turn_num, int)
            assert isinstance(rationale, str)

    def test_feedback_used_after_first_rejection(self):
        her = _make_her_stub()
        hal = _alternating_hal(n_reject=1)  # reject first, approve rest
        loop = EvolutionLoop(her, hal, iterations=3)
        session = loop.run("task")
        # Turn 0: no prior feedback
        assert session.trajectory[0].feedback_used is False
        # Turn 1: received feedback from turn 0's rejection
        assert session.trajectory[1].feedback_used is True

    def test_feedback_prompt_includes_rejection_rationale(self):
        prompt_no_feedback = _build_evolve_prompt("task", 0, 5, None)
        prompt_with_feedback = _build_evolve_prompt("task", 1, 5, "Rationale: too vague")
        assert "too vague" in prompt_with_feedback
        assert "too vague" not in prompt_no_feedback

    def test_chain_integrity_verifiable(self):
        her = _make_her_stub()
        hal = _alternating_hal(n_reject=2)
        loop = EvolutionLoop(her, hal, iterations=5)
        session = loop.run("task")
        assert loop.verify_session(session) is True

    def test_chain_integrity_detects_tampering(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        loop = EvolutionLoop(her, hal, iterations=3)
        session = loop.run("task")
        # Tamper with a receipt
        mutated = list(session.receipt_chain)
        mutated[0] = {**mutated[0], "task": "tampered"}
        tampered = EvolveSession(
            task=session.task,
            iterations_run=session.iterations_run,
            threshold=session.threshold,
            promotion_threshold=session.promotion_threshold,
            trajectory=session.trajectory,
            all_claims=session.all_claims,
            eligible_claims=session.eligible_claims,
            promoted_claims=session.promoted_claims,
            best_claim=session.best_claim,
            failure_analysis=session.failure_analysis,
            receipt_chain=tuple(mutated),
            session_hash=session.session_hash,
        )
        assert loop.verify_session(tampered) is False

    def test_genesis_hash_starts_chain(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        loop = EvolutionLoop(her, hal, iterations=2)
        session = loop.run("task")
        assert session.receipt_chain[0]["previous_hash"] == GENESIS_HASH

    def test_session_is_frozen(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        loop = EvolutionLoop(her, hal, iterations=2)
        session = loop.run("task")
        with pytest.raises((AttributeError, TypeError)):
            session.task = "tampered"  # type: ignore[misc]

    def test_evolve_turn_is_frozen(self):
        her = _make_her_stub()
        hal = _make_hal_stub()
        loop = EvolutionLoop(her, hal, iterations=2)
        session = loop.run("task")
        turn = session.trajectory[0]
        with pytest.raises((AttributeError, TypeError)):
            turn.verdict = "TAMPERED"  # type: ignore[misc]

    def test_compose_feedback_with_rationale_and_issues(self):
        result = _compose_feedback("needs more detail", ["too vague", "missing test"])
        assert "needs more detail" in result
        assert "too vague" in result

    def test_compose_feedback_empty(self):
        result = _compose_feedback("", [])
        assert "without rationale" in result

    def test_feedback_receipt_authority_false(self):
        r = _make_feedback_receipt(0, "task", "rationale", GENESIS_HASH)
        assert r["authority"] is False
        assert r["type"] == "EVOLVE_FEEDBACK"

    def test_early_stop_on_consecutive_approvals(self):
        her = _make_her_stub()
        hal = _make_hal_stub(verdict="APPROVE", confidence=0.9)
        loop = EvolutionLoop(
            her, hal,
            iterations=100,
            approval_threshold=0.7,
            early_stop_on_n_consecutive_approvals=3,
        )
        session = loop.run("task")
        # Should stop at 3 iterations, not run all 100
        assert session.iterations_run == 3
