"""
HELEN OS Mandatory Intent Gateway — Test Suite

Tests the 5 mandatory cases + kill switch + metrics + memory policy.
"""

import json
import pytest
from helen_os.gateway import (
    IntentGateway, GatewayLog, GatewayMetrics,
    make_proposal, enforce_proposal_type, PROPOSAL_TYPE,
)
from helen_os.intents.schemas import make_envelope, IntentEnvelope, SCHEMA_VERSION


# ===================================================================
# Helpers
# ===================================================================

def _mock_executor(proposal, payload):
    """Simple executor that echoes back the intent type."""
    return {"executed": True, "intent_type": proposal["intent_type"]}, None


def _failing_executor(proposal, payload):
    """Executor that always fails."""
    return None, "execution_failed"


# ===================================================================
# The 5 mandatory test cases
# ===================================================================

class TestMandatoryCases:
    """These 5 must pass today. No theory."""

    def test_1_linkedin_post(self):
        """'write a linkedin post about AI' -> FIRST_DRAFT"""
        gw = IntentGateway(executor=_mock_executor)
        result = gw.process("write a linkedin post about AI")
        assert result["type"] == "INTENT_EXECUTED"
        assert result["intent_type"] == "FIRST_DRAFT"
        assert result["authority"] == "NONE"
        assert result["receipt"] is not None

    def test_2_decide_between_offers(self):
        """'help me decide between two offers' -> DECISION_ANALYSIS"""
        gw = IntentGateway(executor=_mock_executor)
        result = gw.process("help me decide between two offers")
        assert result["type"] == "INTENT_EXECUTED"
        assert result["intent_type"] == "DECISION_ANALYSIS"

    def test_3_summarize_book(self):
        """'summarize this book' -> BOOK_SUMMARY"""
        gw = IntentGateway(executor=_mock_executor)
        result = gw.process("give me a book summary of Zero to One")
        assert result["type"] == "INTENT_EXECUTED"
        assert result["intent_type"] == "BOOK_SUMMARY"

    def test_4_unclear_input(self):
        """'random unclear input xyz' -> FIRST_DRAFT (default)"""
        gw = IntentGateway(executor=_mock_executor)
        result = gw.process("random unclear input xyz")
        assert result["type"] == "INTENT_EXECUTED"
        assert result["intent_type"] == "FIRST_DRAFT"

    def test_5_bypass_attempt(self):
        """Direct kernel_step without intent -> MUST FAIL"""
        raw_proposal = {"text": "do something"}
        ok, reason = enforce_proposal_type(raw_proposal)
        assert not ok
        assert "raw_input_forbidden" in reason


# ===================================================================
# Kill switch enforcement
# ===================================================================

class TestKillSwitch:
    def test_valid_proposal_passes(self):
        envelope = make_envelope("FIRST_DRAFT", "test", {"content_type": "post", "topic": "AI"})
        proposal = make_proposal(envelope)
        ok, reason = enforce_proposal_type(proposal)
        assert ok

    def test_missing_proposal_type_rejected(self):
        ok, reason = enforce_proposal_type({"intent_type": "FIRST_DRAFT"})
        assert not ok
        assert "raw_input_forbidden" in reason

    def test_wrong_proposal_type_rejected(self):
        ok, reason = enforce_proposal_type({"proposal_type": "RAW_EXECUTION"})
        assert not ok
        assert "raw_input_forbidden" in reason

    def test_authority_true_rejected(self):
        envelope = make_envelope("FIRST_DRAFT", "test", {"content_type": "post", "topic": "AI"})
        proposal = make_proposal(envelope)
        proposal["authority"] = True
        ok, reason = enforce_proposal_type(proposal)
        assert not ok
        assert "authority" in reason

    def test_missing_intent_type_rejected(self):
        ok, reason = enforce_proposal_type({
            "proposal_type": PROPOSAL_TYPE,
            "authority": False,
            "payload_hash": "abc",
        })
        assert not ok
        assert "intent_type" in reason

    def test_missing_payload_hash_rejected(self):
        ok, reason = enforce_proposal_type({
            "proposal_type": PROPOSAL_TYPE,
            "authority": False,
            "intent_type": "FIRST_DRAFT",
        })
        assert not ok
        assert "payload_hash" in reason

    def test_non_dict_rejected(self):
        ok, reason = enforce_proposal_type("not a dict")
        assert not ok

    def test_none_rejected(self):
        ok, reason = enforce_proposal_type(None)
        assert not ok


# ===================================================================
# Gateway lifecycle
# ===================================================================

class TestGatewayLifecycle:
    def test_validation_only_mode(self):
        gw = IntentGateway(executor=None)
        result = gw.process("write a blog post about AI")
        assert result["type"] == "INTENT_VALIDATED"
        assert result["proposal"]["proposal_type"] == PROPOSAL_TYPE

    def test_execution_error_handled(self):
        gw = IntentGateway(executor=_failing_executor)
        result = gw.process("write a blog post")
        assert result["type"] == "INTENT_REJECTED"
        assert "execution_error" in result["reason"]

    def test_exception_in_executor_handled(self):
        def _exploding_executor(p, pl):
            raise RuntimeError("boom")
        gw = IntentGateway(executor=_exploding_executor)
        result = gw.process("write a post")
        assert result["type"] == "INTENT_REJECTED"
        assert "boom" in result["reason"]

    def test_receipt_on_success(self):
        gw = IntentGateway(executor=_mock_executor)
        result = gw.process("write a post about AI")
        assert result["receipt"]["receipt_type"] == "INTENT_EXECUTION_RECEIPT_V1"
        assert result["receipt"]["authority"] == "NONE"
        assert result["receipt"]["status"] == "COMPLETED"

    def test_authority_always_none(self):
        gw = IntentGateway(executor=_mock_executor)
        for text in ["write code", "decide between A or B", "random"]:
            result = gw.process(text)
            assert result["authority"] == "NONE"
            if result.get("receipt"):
                assert result["receipt"]["authority"] == "NONE"
            assert result["log"]["authority"] == "NONE"


# ===================================================================
# Metrics
# ===================================================================

class TestMetrics:
    def test_metrics_accumulate(self):
        gw = IntentGateway(executor=_mock_executor)
        gw.process("write a post")
        gw.process("decide between A or B")
        gw.process("premortem on my launch")

        m = gw.metrics
        assert m.total_requests == 3
        assert m.classified == 3
        assert m.validated == 3
        assert m.executed == 3
        assert m.receipts_emitted == 3

    def test_rejection_counted(self):
        gw = IntentGateway(executor=_failing_executor)
        gw.process("write a post")
        assert gw.metrics.rejected == 1
        assert gw.metrics.executed == 0

    def test_metrics_serializable(self):
        gw = IntentGateway(executor=_mock_executor)
        gw.process("test")
        d = gw.metrics.to_dict()
        assert isinstance(d, dict)
        json.dumps(d)

    def test_rates_above_80_percent(self):
        """The 3 metrics must be >= 80%."""
        gw = IntentGateway(executor=_mock_executor)
        for text in [
            "write a linkedin post about AI",
            "help me decide between two offers",
            "summarize this book about leadership",
            "do a premortem on my product launch",
            "synthesize my notes from this week",
            "give me a weekly review",
            "analyze my competitor Stripe",
            "draft an email sequence for onboarding",
            "stress test my assumptions",
            "write a blog post about governance",
        ]:
            gw.process(text)

        assert gw.metrics.classification_rate >= 0.8
        assert gw.metrics.validation_rate >= 0.8
        assert gw.metrics.receipt_rate >= 0.8


# ===================================================================
# Observability logs
# ===================================================================

class TestLogs:
    def test_log_on_success(self):
        gw = IntentGateway(executor=_mock_executor)
        gw.process("write a post")
        assert len(gw.logs) == 1
        assert gw.logs[0].executed is True

    def test_log_on_rejection(self):
        gw = IntentGateway(executor=_failing_executor)
        gw.process("write a post")
        assert len(gw.logs) == 1
        assert gw.logs[0].executed is False

    def test_log_callback(self):
        captured = []
        gw = IntentGateway(executor=_mock_executor, on_log=lambda l: captured.append(l))
        gw.process("test")
        assert len(captured) == 1
        assert captured[0].authority == "NONE"

    def test_log_has_all_fields(self):
        gw = IntentGateway(executor=_mock_executor)
        gw.process("write a blog post")
        log = gw.logs[0]
        assert log.input_text
        assert log.intent_type
        assert log.intent_id
        assert log.timestamp
        assert log.duration_ms >= 0
        assert log.authority == "NONE"


# ===================================================================
# Memory write policy
# ===================================================================

class TestMemoryPolicy:
    def test_writable_intent_has_memory_candidate(self):
        gw = IntentGateway(executor=_mock_executor)
        result = gw.process("let's do my weekly review")
        assert result["intent_type"] == "WEEKLY_REVIEW"
        assert result["memory_candidate"] is not None

    def test_non_writable_intent_no_memory(self):
        gw = IntentGateway(executor=_mock_executor)
        result = gw.process("write a blog post")
        assert result["intent_type"] == "FIRST_DRAFT"
        assert result["memory_candidate"] is None

    def test_note_synthesis_writable(self):
        gw = IntentGateway(executor=_mock_executor)
        result = gw.process("synthesize my notes from today")
        assert result["intent_type"] == "NOTE_SYNTHESIS"
        assert result["memory_candidate"] is not None


# ===================================================================
# Proposal construction
# ===================================================================

class TestProposal:
    def test_proposal_fields(self):
        envelope = make_envelope("FIRST_DRAFT", "test", {"content_type": "post", "topic": "AI"})
        proposal = make_proposal(envelope)
        assert proposal["proposal_type"] == PROPOSAL_TYPE
        assert proposal["authority"] is False
        assert proposal["intent_type"] == "FIRST_DRAFT"
        assert proposal["payload_hash"]

    def test_proposal_hash_deterministic(self):
        e1 = make_envelope("PREMORTEM", "test", {"project": "X"})
        e2 = make_envelope("PREMORTEM", "test", {"project": "X"})
        assert make_proposal(e1)["payload_hash"] == make_proposal(e2)["payload_hash"]


# ===================================================================
# Intent routing through gateway (parametrized)
# ===================================================================

class TestIntentRouting:
    @pytest.mark.parametrize("text,expected", [
        ("write me a newsletter", "FIRST_DRAFT"),
        ("decide between A or B", "DECISION_ANALYSIS"),
        ("do a premortem", "PREMORTEM"),
        ("weekly review", "WEEKLY_REVIEW"),
        ("synthesize notes", "NOTE_SYNTHESIS"),
        ("expand into a thread", "THREAD_EXPANSION"),
        ("repurpose this content", "CONTENT_REPURPOSE"),
        ("generate headlines", "HEADLINE_GENERATION"),
        ("competitor analysis of Stripe", "COMPETITOR_ANALYSIS"),
        ("stress test my assumptions", "ASSUMPTION_STRESS_TEST"),
        ("reverse brainstorm this", "REVERSE_BRAINSTORM"),
        ("pricing analysis", "PRICING_ANALYSIS"),
        ("delegate this task", "TASK_DELEGATION"),
    ])
    def test_routes_correctly(self, text, expected):
        gw = IntentGateway(executor=_mock_executor)
        result = gw.process(text)
        assert result["intent_type"] == expected
        assert result["type"] == "INTENT_EXECUTED"
