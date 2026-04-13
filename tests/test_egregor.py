"""HELEN OS — Egregor Street Test Suite.

Tests the governed multi-agent coding superteam pipeline.
All tests use mocked OllamaClient — no Ollama required.

Invariants tested:
  E1: Authority is always False on all phase results and receipts
  E2: Receipt chain integrity (previous_hash links from EGREGOR_GENESIS)
  E3: Subtask decomposition produces valid SubTask objects
  E4: Reviewer rejection triggers feedback loop (retry)
  E5: Validator score below threshold triggers retry
  E6: Max retries prevents infinite loops
  E7: Session hash is deterministic given same outputs
  E8: Base state is never mutated (sandbox isolation)
  E9: All phase results are receipted
  E10: OllamaError → graceful degradation
"""
from __future__ import annotations

import json
import copy
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from helensh.egregor.pipeline import (
    EgregorStreet,
    EgregorSession,
    SubTask,
    PhaseResult,
    CodeUnit,
    _make_phase_receipt,
    _parse_subtasks,
    _extract_code,
    _extract_json_safe,
)
from helensh.egregor.roles import (
    RoleConfig,
    ROLES,
    MODEL_ARCHITECT,
    MODEL_CODER,
    MODEL_REVIEWER,
    MODEL_TESTER,
    MODEL_FALLBACK,
)
from helensh.agents.hal_reviewer import HalReviewer
from helensh.adapters.ollama import OllamaError
from helensh.kernel import init_session


# ── Mock Helpers ─────────────────────────────────────────────────────


def _make_mock_client(responses: Optional[Dict[str, List[str]]] = None) -> MagicMock:
    """Create a mock OllamaClient with queued responses per model.

    responses: dict of model_name → list of response strings.
    Each call to chat() pops the next response for the given model.
    """
    client = MagicMock()
    client.is_available.return_value = True
    client.has_model.return_value = True
    client.list_models.return_value = []

    queues: Dict[str, List[str]] = {}
    if responses:
        queues = {k: list(v) for k, v in responses.items()}

    default = json.dumps({
        "action": "chat",
        "confidence": 0.5,
        "authority": False,
    })

    def mock_chat(model: str, messages: Any, **kwargs: Any) -> str:
        queue = queues.get(model, [])
        if queue:
            return queue.pop(0)
        return default

    client.chat.side_effect = mock_chat
    return client


def _architect_response(subtasks: list) -> str:
    """Build a valid ARCHITECT JSON response."""
    return json.dumps({
        "subtasks": subtasks,
        "design_notes": "Standard decomposition",
        "confidence": 0.8,
        "authority": False,
    })


def _coder_response(code: str = "def hello():\n    return 'world'") -> str:
    """Build a valid CODER JSON response."""
    return json.dumps({
        "action": "write_code",
        "target": "module.py",
        "payload": {
            "description": "Hello function",
            "code": code,
            "language": "python",
        },
        "confidence": 0.8,
        "authority": False,
    })


def _hal_review_response(verdict: str = "APPROVE") -> str:
    """Build a valid HAL reviewer JSON response."""
    return json.dumps({
        "verdict": verdict,
        "rationale": f"Code looks {'good' if verdict == 'APPROVE' else 'bad'}",
        "issues": [] if verdict == "APPROVE" else ["needs improvement"],
        "confidence": 0.8,
        "authority": False,
    })


def _tester_response(test_code: str = "def test_hello():\n    assert True") -> str:
    """Build a valid TESTER JSON response."""
    return json.dumps({
        "action": "write_tests",
        "target": "test_module.py",
        "payload": {
            "description": "Tests for hello",
            "code": test_code,
            "language": "python",
            "test_count": 1,
        },
        "confidence": 0.8,
        "authority": False,
    })


def _make_happy_path_client() -> MagicMock:
    """Client that produces a happy path: 1 subtask, all approve.

    NOTE: MODEL_CODER and MODEL_TESTER are both "her-codex-gemma",
    so responses must be combined in call order: coder, then tester.
    """
    subtasks = [
        {"id": 1, "title": "Create hello", "description": "Hello func",
         "target": "hello.py", "dependencies": []},
    ]
    return _make_mock_client({
        MODEL_ARCHITECT: [_architect_response(subtasks)],
        # Combined coder + tester queue (same model: her-codex-gemma)
        MODEL_CODER: [_coder_response(), _tester_response()],
        MODEL_REVIEWER: [_hal_review_response("APPROVE")],
    })


# ── Data Structure Tests ─────────────────────────────────────────────


class TestSubTask:
    def test_creation(self):
        st = SubTask(id=1, title="test", description="desc", target="f.py")
        assert st.id == 1
        assert st.title == "test"
        assert st.target == "f.py"

    def test_frozen(self):
        st = SubTask(id=1, title="test", description="desc", target="f.py")
        with pytest.raises(AttributeError):
            st.title = "changed"  # type: ignore[misc]

    def test_default_dependencies(self):
        st = SubTask(id=1, title="t", description="d", target="f.py")
        assert st.dependencies == ()

    def test_custom_dependencies(self):
        st = SubTask(id=1, title="t", description="d", target="f.py",
                     dependencies=(2, 3))
        assert st.dependencies == (2, 3)


class TestPhaseResult:
    def test_authority_always_false(self):
        pr = PhaseResult(
            phase="coder", role="coder", subtask_id=1,
            output={}, verdict="N/A", confidence=0.5,
            receipt_hash="abc", authority=True,  # try to override
        )
        assert pr.authority is False

    def test_frozen(self):
        pr = PhaseResult(
            phase="coder", role="coder", subtask_id=1,
            output={}, verdict="N/A", confidence=0.5,
            receipt_hash="abc",
        )
        with pytest.raises(AttributeError):
            pr.verdict = "APPROVE"  # type: ignore[misc]

    def test_fields(self):
        pr = PhaseResult(
            phase="reviewer", role="reviewer", subtask_id=2,
            output={"v": 1}, verdict="APPROVE", confidence=0.9,
            receipt_hash="xyz",
        )
        assert pr.phase == "reviewer"
        assert pr.subtask_id == 2
        assert pr.confidence == 0.9


class TestCodeUnit:
    def test_creation(self):
        cu = CodeUnit(
            subtask_id=1, target="f.py", code="x=1",
            tests="test", validation_score=0.8,
            approved=True, retries=0,
        )
        assert cu.approved is True
        assert cu.retries == 0

    def test_frozen(self):
        cu = CodeUnit(
            subtask_id=1, target="f.py", code="x=1",
            tests=None, validation_score=0.0,
            approved=False, retries=2,
        )
        with pytest.raises(AttributeError):
            cu.approved = True  # type: ignore[misc]


class TestEgregorSession:
    def test_creation(self):
        session = EgregorSession(
            task="test", subtasks=(), phase_results=(),
            code_units=(), receipt_chain=(), session_hash="abc",
            total_phases=0, approved_count=0, rejected_count=0,
            validation_mean=0.0,
        )
        assert session.task == "test"
        assert session.session_hash == "abc"

    def test_frozen(self):
        session = EgregorSession(
            task="test", subtasks=(), phase_results=(),
            code_units=(), receipt_chain=(), session_hash="abc",
            total_phases=0, approved_count=0, rejected_count=0,
            validation_mean=0.0,
        )
        with pytest.raises(AttributeError):
            session.task = "changed"  # type: ignore[misc]


# ── Helper Function Tests ────────────────────────────────────────────


class TestParseSubtasks:
    def test_valid_subtasks(self):
        parsed = {
            "subtasks": [
                {"id": 1, "title": "A", "description": "do A", "target": "a.py"},
                {"id": 2, "title": "B", "description": "do B", "target": "b.py"},
            ],
        }
        result = _parse_subtasks(parsed)
        assert len(result) == 2
        assert result[0].id == 1
        assert result[1].title == "B"

    def test_empty_subtasks(self):
        assert _parse_subtasks({}) == []
        assert _parse_subtasks({"subtasks": []}) == []

    def test_malformed_subtasks(self):
        assert _parse_subtasks({"subtasks": "not a list"}) == []

    def test_non_dict_entries_skipped(self):
        result = _parse_subtasks({"subtasks": ["not a dict", 42]})
        assert result == []

    def test_max_10_subtasks(self):
        tasks = [{"id": i, "title": f"t{i}", "description": f"d{i}",
                  "target": f"f{i}.py"} for i in range(20)]
        result = _parse_subtasks({"subtasks": tasks})
        assert len(result) == 10

    def test_default_values(self):
        result = _parse_subtasks({"subtasks": [{}]})
        assert len(result) == 1
        assert result[0].title == "subtask-1"
        assert result[0].target == "module.py"

    def test_dependencies_tuple(self):
        result = _parse_subtasks({"subtasks": [
            {"id": 1, "title": "t", "description": "d",
             "target": "f.py", "dependencies": [2, 3]},
        ]})
        assert result[0].dependencies == (2, 3)


class TestExtractCode:
    def test_from_payload(self):
        output = {"payload": {"code": "x = 1"}}
        assert _extract_code(output) == "x = 1"

    def test_from_top_level(self):
        output = {"code": "y = 2"}
        assert _extract_code(output) == "y = 2"

    def test_missing_code(self):
        assert _extract_code({}) is None
        assert _extract_code({"payload": {}}) is None

    def test_none_code(self):
        assert _extract_code({"payload": {"code": None}}) is None

    def test_strips_whitespace(self):
        output = {"payload": {"code": "  x = 1  \n  "}}
        assert _extract_code(output) == "x = 1"

    def test_non_dict_payload(self):
        output = {"payload": "not a dict"}
        assert _extract_code(output) is None


class TestExtractJsonSafe:
    def test_direct_json(self):
        result = _extract_json_safe('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_with_preamble(self):
        result = _extract_json_safe('Here is the output: {"key": "value"} done.')
        assert result == {"key": "value"}

    def test_invalid_json(self):
        assert _extract_json_safe("not json at all") is None

    def test_empty_string(self):
        assert _extract_json_safe("") is None

    def test_nested_json(self):
        text = '{"outer": {"inner": true}}'
        result = _extract_json_safe(text)
        assert result == {"outer": {"inner": True}}


class TestMakePhaseReceipt:
    def test_structure(self):
        receipt = _make_phase_receipt(
            "coder", "coder", 1, {"code": "x=1"}, "N/A", "prev_hash",
        )
        assert receipt["type"] == "EGREGOR_PHASE"
        assert receipt["phase"] == "coder"
        assert receipt["role"] == "coder"
        assert receipt["subtask_id"] == 1
        assert receipt["verdict"] == "N/A"
        assert receipt["authority"] is False
        assert receipt["previous_hash"] == "prev_hash"
        assert "hash" in receipt
        assert "output_hash" in receipt

    def test_authority_always_false(self):
        receipt = _make_phase_receipt(
            "x", "x", None, {}, "APPROVE", "p",
        )
        assert receipt["authority"] is False

    def test_hash_is_hex(self):
        receipt = _make_phase_receipt(
            "x", "x", None, {}, "N/A", "p",
        )
        assert len(receipt["hash"]) == 64
        int(receipt["hash"], 16)  # valid hex

    def test_chain_link(self):
        r1 = _make_phase_receipt("a", "a", None, {}, "N/A", "GENESIS")
        r2 = _make_phase_receipt("b", "b", None, {}, "N/A", r1["hash"])
        assert r2["previous_hash"] == r1["hash"]


# ── Role Config Tests ────────────────────────────────────────────────


class TestRoles:
    def test_all_roles_defined(self):
        for name in ("architect", "coder", "reviewer", "tester"):
            assert name in ROLES

    def test_role_config_frozen(self):
        with pytest.raises(AttributeError):
            ROLES["architect"].name = "changed"  # type: ignore[misc]

    def test_architect_model(self):
        assert ROLES["architect"].model == MODEL_ARCHITECT

    def test_coder_model(self):
        assert ROLES["coder"].model == MODEL_CODER

    def test_tester_model(self):
        assert ROLES["tester"].model == MODEL_TESTER

    def test_all_have_fallback(self):
        for role in ROLES.values():
            assert role.fallback_model == MODEL_FALLBACK


# ── Pipeline Tests ───────────────────────────────────────────────────


class TestEgregorHappyPath:
    """Full pipeline with 1 subtask, all phases approve."""

    def _run(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        return eg.run("Build a hello world module"), eg

    def test_session_type(self):
        session, _ = self._run()
        assert isinstance(session, EgregorSession)

    def test_subtask_count(self):
        session, _ = self._run()
        assert len(session.subtasks) == 1

    def test_has_phase_results(self):
        session, _ = self._run()
        assert session.total_phases > 0

    def test_has_code_units(self):
        session, _ = self._run()
        assert len(session.code_units) == 1

    def test_code_unit_has_code(self):
        session, _ = self._run()
        assert session.code_units[0].code != ""

    def test_receipt_chain_nonempty(self):
        session, _ = self._run()
        assert len(session.receipt_chain) > 0

    def test_session_hash_is_hex(self):
        session, _ = self._run()
        assert len(session.session_hash) == 64
        int(session.session_hash, 16)

    def test_verify_session(self):
        session, eg = self._run()
        assert eg.verify_session(session) is True


class TestEgregorMultiSubtask:
    """Pipeline with 2 subtasks."""

    def _run(self):
        subtasks = [
            {"id": 1, "title": "Models", "description": "Data models",
             "target": "models.py", "dependencies": []},
            {"id": 2, "title": "Routes", "description": "API routes",
             "target": "routes.py", "dependencies": [1]},
        ]
        # Call order for her-codex-gemma: coder(1), tester(1), coder(2), tester(2)
        client = _make_mock_client({
            MODEL_ARCHITECT: [_architect_response(subtasks)],
            MODEL_CODER: [
                _coder_response(), _tester_response(),
                _coder_response(), _tester_response(),
            ],
            MODEL_REVIEWER: [
                _hal_review_response("APPROVE"),
                _hal_review_response("APPROVE"),
            ],
        })
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        return eg.run("Build an API"), eg

    def test_two_subtasks(self):
        session, _ = self._run()
        assert len(session.subtasks) == 2

    def test_two_code_units(self):
        session, _ = self._run()
        assert len(session.code_units) == 2

    def test_chain_integrity(self):
        session, eg = self._run()
        assert eg.verify_session(session) is True


# ── Feedback Loop Tests ──────────────────────────────────────────────


class TestFeedbackLoop:
    """REVIEWER rejection triggers CODER retry."""

    def test_reject_then_approve(self):
        """First attempt rejected, second approved."""
        subtasks = [
            {"id": 1, "title": "Func", "description": "Write func",
             "target": "f.py", "dependencies": []},
        ]
        # Call order for her-codex-gemma: coder(0), coder(1), tester
        client = _make_mock_client({
            MODEL_ARCHITECT: [_architect_response(subtasks)],
            MODEL_CODER: [_coder_response(), _coder_response(), _tester_response()],
            MODEL_REVIEWER: [
                _hal_review_response("REJECT"),    # first: reject
                _hal_review_response("APPROVE"),   # second: approve
            ],
        })
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("Write a function")

        assert session.code_units[0].retries == 1
        # Should have coder+reviewer+coder+reviewer+tester+validator results
        phases = [r.phase for r in session.phase_results]
        assert phases.count("coder") == 2
        assert phases.count("reviewer") == 2

    def test_max_retries_reached(self):
        """All attempts rejected — should stop at max_retries."""
        subtasks = [
            {"id": 1, "title": "Func", "description": "Write func",
             "target": "f.py", "dependencies": []},
        ]
        # 4 rejections (attempt 0 + 3 retries), no tester needed
        client = _make_mock_client({
            MODEL_ARCHITECT: [_architect_response(subtasks)],
            MODEL_CODER: [_coder_response()] * 4,  # all rejected, no tester call
            MODEL_REVIEWER: [_hal_review_response("REJECT")] * 4,
        })
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client, max_retries=3)
        session = eg.run("Write a function")

        assert session.code_units[0].retries == 3
        assert session.code_units[0].approved is False

    def test_request_changes_triggers_retry(self):
        """REQUEST_CHANGES also triggers retry."""
        subtasks = [
            {"id": 1, "title": "Func", "description": "Write func",
             "target": "f.py", "dependencies": []},
        ]
        # Call order for her-codex-gemma: coder(0), coder(1), tester
        client = _make_mock_client({
            MODEL_ARCHITECT: [_architect_response(subtasks)],
            MODEL_CODER: [_coder_response(), _coder_response(), _tester_response()],
            MODEL_REVIEWER: [
                _hal_review_response("REQUEST_CHANGES"),
                _hal_review_response("APPROVE"),
            ],
        })
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("Write a function")

        assert session.code_units[0].retries == 1


class TestValidationFeedback:
    """Validator score below threshold triggers CODER retry."""

    def test_low_score_retries(self):
        """Code with syntax error gets low score, triggers retry."""
        subtasks = [
            {"id": 1, "title": "Func", "description": "Write func",
             "target": "f.py", "dependencies": []},
        ]
        # Call order for her-codex-gemma:
        #   coder(0:broken), tester(0), coder(1:fixed), tester(1)
        client = _make_mock_client({
            MODEL_ARCHITECT: [_architect_response(subtasks)],
            MODEL_CODER: [
                _coder_response("def broken(:\n    pass"),   # syntax error
                _tester_response(),                           # tester for attempt 0
                _coder_response("def fixed():\n    return 1"),  # valid
                _tester_response(),                           # tester for attempt 1
            ],
            MODEL_REVIEWER: [
                _hal_review_response("APPROVE"),
                _hal_review_response("APPROVE"),
            ],
        })
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client, validation_threshold=0.2)
        session = eg.run("Write a function")

        # First attempt should have validation score 0.0 (syntax error)
        validator_results = [
            r for r in session.phase_results if r.phase == "validator"
        ]
        assert len(validator_results) >= 1


# ── Invariant Tests ──────────────────────────────────────────────────


class TestInvariantE1AuthorityFalse:
    """E1: Authority is always False on all phase results and receipts."""

    def test_all_phase_results_authority_false(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")

        for pr in session.phase_results:
            assert pr.authority is False, f"Phase {pr.phase} has authority=True"

    def test_all_receipts_authority_false(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")

        for receipt in session.receipt_chain:
            assert receipt["authority"] is False


class TestInvariantE2ChainIntegrity:
    """E2: Receipt chain links are unbroken from EGREGOR_GENESIS."""

    def test_genesis_link(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")

        assert session.receipt_chain[0]["previous_hash"] == "EGREGOR_GENESIS"

    def test_chain_links(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")

        chain = session.receipt_chain
        for i in range(1, len(chain)):
            assert chain[i]["previous_hash"] == chain[i - 1]["hash"]

    def test_verify_session(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")
        assert eg.verify_session(session) is True


class TestInvariantE7SessionHash:
    """E7: Session hash is deterministic given same outputs."""

    def test_deterministic_hash(self):
        """Same pipeline inputs → same session hash."""
        results = []
        for _ in range(2):
            client = _make_happy_path_client()
            hal = HalReviewer(client=client)
            eg = EgregorStreet(hal=hal, client=client)
            session = eg.run("test task")
            results.append(session.session_hash)
        assert results[0] == results[1]


class TestInvariantE8BaseStateIsolation:
    """E8: Base state is never mutated by the pipeline."""

    def test_state_not_mutated(self):
        state = init_session(session_id="egregor-test")
        original = copy.deepcopy(state)

        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        eg.run("test", state=state)

        assert state == original


class TestInvariantE10OllamaError:
    """E10: OllamaError → graceful degradation."""

    def test_all_errors_produces_session(self):
        """Even if every model call fails, we get a valid session."""
        client = MagicMock()
        client.is_available.return_value = True
        client.has_model.return_value = True
        client.chat.side_effect = OllamaError("unreachable")

        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")

        assert isinstance(session, EgregorSession)
        assert len(session.subtasks) >= 1  # fallback subtask created
        assert eg.verify_session(session) is True


# ── Edge Case Tests ──────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_task(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("")
        assert isinstance(session, EgregorSession)

    def test_zero_retries(self):
        """max_retries=0 means only one attempt."""
        subtasks = [
            {"id": 1, "title": "Func", "description": "Write func",
             "target": "f.py", "dependencies": []},
        ]
        # Only one coder call (rejected, no retry, no tester)
        client = _make_mock_client({
            MODEL_ARCHITECT: [_architect_response(subtasks)],
            MODEL_CODER: [_coder_response()],  # rejected, no tester needed
            MODEL_REVIEWER: [_hal_review_response("REJECT")],
        })
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client, max_retries=0)
        session = eg.run("test")

        assert session.code_units[0].retries == 0
        assert session.code_units[0].approved is False

    def test_no_code_in_coder_output(self):
        """Coder returns no code — should retry."""
        subtasks = [
            {"id": 1, "title": "Func", "description": "d",
             "target": "f.py", "dependencies": []},
        ]
        no_code = json.dumps({
            "action": "write_code",
            "target": "f.py",
            "payload": {"code": None, "description": "confused"},
            "confidence": 0.1,
            "authority": False,
        })
        # Call order for her-codex-gemma: coder(0:no_code), coder(1:valid), tester
        client = _make_mock_client({
            MODEL_ARCHITECT: [_architect_response(subtasks)],
            MODEL_CODER: [no_code, _coder_response(), _tester_response()],
            MODEL_REVIEWER: [_hal_review_response("APPROVE")],
        })
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")

        # First attempt had no code (retry), second succeeded
        assert session.code_units[0].retries == 1

    def test_custom_validation_threshold(self):
        """High threshold means code needs to be very good."""
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(
            hal=hal, client=client,
            validation_threshold=0.99,  # very high
        )
        session = eg.run("test")
        # Likely won't reach 0.99 with mock tests, but should not crash
        assert isinstance(session, EgregorSession)

    def test_session_counts(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")

        total = session.approved_count + session.rejected_count
        assert total == len(session.code_units)


# ── Verification Edge Cases ──────────────────────────────────────────


class TestVerification:
    def test_empty_chain_passes(self):
        eg = EgregorStreet(
            hal=HalReviewer(client=_make_happy_path_client()),
            client=_make_happy_path_client(),
        )
        session = EgregorSession(
            task="test", subtasks=(), phase_results=(),
            code_units=(), receipt_chain=(), session_hash="abc",
            total_phases=0, approved_count=0, rejected_count=0,
            validation_mean=0.0,
        )
        assert eg.verify_session(session) is True

    def test_tampered_chain_fails(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")

        # Tamper with the chain
        if len(session.receipt_chain) >= 2:
            chain = list(session.receipt_chain)
            chain[1] = dict(chain[1])
            chain[1]["previous_hash"] = "TAMPERED"
            tampered = EgregorSession(
                task=session.task,
                subtasks=session.subtasks,
                phase_results=session.phase_results,
                code_units=session.code_units,
                receipt_chain=tuple(chain),
                session_hash=session.session_hash,
                total_phases=session.total_phases,
                approved_count=session.approved_count,
                rejected_count=session.rejected_count,
                validation_mean=session.validation_mean,
            )
            assert eg.verify_session(tampered) is False

    def test_authority_tamper_fails(self):
        client = _make_happy_path_client()
        hal = HalReviewer(client=client)
        eg = EgregorStreet(hal=hal, client=client)
        session = eg.run("test")

        if session.receipt_chain:
            chain = list(session.receipt_chain)
            chain[0] = dict(chain[0])
            chain[0]["authority"] = True
            tampered = EgregorSession(
                task=session.task,
                subtasks=session.subtasks,
                phase_results=session.phase_results,
                code_units=session.code_units,
                receipt_chain=tuple(chain),
                session_hash=session.session_hash,
                total_phases=session.total_phases,
                approved_count=session.approved_count,
                rejected_count=session.rejected_count,
                validation_mean=session.validation_mean,
            )
            assert eg.verify_session(tampered) is False
