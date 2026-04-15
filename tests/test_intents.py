"""
HELEN OS Intent Layer — Test Suite

Tests: schemas, classification, extraction, governor, envelope, receipt, memory write rules.
"""

import pytest
from helen_os.intents.schemas import (
    INTENT_REGISTRY, ALL_INTENT_TYPES, MEMORY_WRITABLE_INTENTS,
    IntentEnvelope, IntentResult, IntentReceipt,
    validate_payload, make_envelope, make_receipt,
    SCHEMA_VERSION, WRITING_INTENTS, ANALYSIS_INTENTS,
    EXECUTION_INTENTS, STRATEGY_INTENTS,
)
from helen_os.intents.classifier import classify_intent, extract_payload, route_input
from helen_os.intents.governor import govern_intent


# ===================================================================
# Schema registry
# ===================================================================

class TestIntentRegistry:
    def test_all_25_intents_registered(self):
        assert len(INTENT_REGISTRY) == 25

    def test_all_types_in_registry(self):
        for t in ALL_INTENT_TYPES:
            assert t in INTENT_REGISTRY, f"{t} missing from registry"

    def test_families_cover_all(self):
        assert len(WRITING_INTENTS) == 7
        assert len(ANALYSIS_INTENTS) == 7
        assert len(EXECUTION_INTENTS) == 7
        assert len(STRATEGY_INTENTS) == 4
        assert len(ALL_INTENT_TYPES) == 25

    def test_every_intent_has_required_fields(self):
        for name, schema in INTENT_REGISTRY.items():
            assert "required" in schema, f"{name} missing required"
            assert "result_fields" in schema, f"{name} missing result_fields"
            assert "family" in schema, f"{name} missing family"
            assert "description" in schema, f"{name} missing description"

    def test_no_duplicate_families(self):
        all_in_families = WRITING_INTENTS | ANALYSIS_INTENTS | EXECUTION_INTENTS | STRATEGY_INTENTS
        assert all_in_families == ALL_INTENT_TYPES

    def test_memory_writable_subset(self):
        assert MEMORY_WRITABLE_INTENTS.issubset(ALL_INTENT_TYPES)
        assert "VOICE_PROFILE" in MEMORY_WRITABLE_INTENTS
        assert "FIRST_DRAFT" not in MEMORY_WRITABLE_INTENTS


# ===================================================================
# Payload validation
# ===================================================================

class TestPayloadValidation:
    def test_valid_first_draft(self):
        ok, errors = validate_payload("FIRST_DRAFT", {"content_type": "newsletter", "topic": "AI"})
        assert ok
        assert errors == []

    def test_missing_required_field(self):
        ok, errors = validate_payload("FIRST_DRAFT", {"content_type": "newsletter"})
        assert not ok
        assert any("topic" in e for e in errors)

    def test_empty_required_field(self):
        ok, errors = validate_payload("FIRST_DRAFT", {"content_type": "newsletter", "topic": ""})
        assert not ok
        assert any("Empty" in e for e in errors)

    def test_valid_decision_analysis(self):
        ok, errors = validate_payload("DECISION_ANALYSIS", {
            "decision": "A or B",
            "options": [{"name": "A"}, {"name": "B"}],
            "priorities": ["speed"],
        })
        assert ok

    def test_valid_weekly_review(self):
        ok, errors = validate_payload("WEEKLY_REVIEW", {
            "completed": ["task1"], "in_progress": [], "blocked": [],
        })
        assert ok

    def test_valid_premortem(self):
        ok, errors = validate_payload("PREMORTEM", {"project": "Launch X"})
        assert ok

    def test_valid_note_synthesis(self):
        ok, errors = validate_payload("NOTE_SYNTHESIS", {"notes": ["note1", "note2"]})
        assert ok

    def test_unknown_intent_type(self):
        ok, errors = validate_payload("NONEXISTENT", {})
        assert not ok
        assert any("Unknown" in e for e in errors)


# ===================================================================
# Intent classification
# ===================================================================

class TestClassification:
    def test_first_draft(self):
        assert classify_intent("write me a blog post about AI") == "FIRST_DRAFT"

    def test_decision_analysis(self):
        assert classify_intent("help me decide between two business ideas") == "DECISION_ANALYSIS"

    def test_premortem(self):
        assert classify_intent("do a premortem on my product launch") == "PREMORTEM"

    def test_weekly_review(self):
        assert classify_intent("let's do my weekly review") == "WEEKLY_REVIEW"

    def test_note_synthesis(self):
        assert classify_intent("synthesize these notes for me") == "NOTE_SYNTHESIS"

    def test_competitor(self):
        assert classify_intent("analyze our competitor Stripe") == "COMPETITOR_ANALYSIS"

    def test_meeting_brief(self):
        assert classify_intent("I have a meeting with Alex from Acme") == "MEETING_BRIEF"

    def test_assumption_stress_test(self):
        assert classify_intent("stress test my assumptions about this launch") == "ASSUMPTION_STRESS_TEST"

    def test_book_summary(self):
        assert classify_intent("give me a book summary of Zero to One") == "BOOK_SUMMARY"

    def test_sop(self):
        assert classify_intent("document this process as an SOP") == "SOP_GENERATION"

    def test_default_is_first_draft(self):
        assert classify_intent("hello world") == "FIRST_DRAFT"

    def test_reverse_brainstorm(self):
        assert classify_intent("reverse brainstorm ways to fail at this") == "REVERSE_BRAINSTORM"

    def test_advisor_simulation(self):
        assert classify_intent("give me 5 advisor perspectives on this") == "ADVISOR_SIMULATION"

    def test_pricing(self):
        assert classify_intent("analyze my pricing strategy") == "PRICING_ANALYSIS"

    def test_task_delegation(self):
        assert classify_intent("help me delegate this task") == "TASK_DELEGATION"

    def test_deterministic(self):
        """Same input always produces same classification."""
        for _ in range(10):
            assert classify_intent("help me decide between A or B") == "DECISION_ANALYSIS"


# ===================================================================
# Payload extraction
# ===================================================================

class TestExtraction:
    def test_first_draft_detects_newsletter(self):
        p = extract_payload("FIRST_DRAFT", "write me a newsletter about AI safety")
        assert p["content_type"] == "newsletter"
        assert "AI safety" in p["topic"]

    def test_decision_extracts_or_options(self):
        p = extract_payload("DECISION_ANALYSIS", "choose between building a SaaS or a marketplace")
        assert len(p["options"]) == 2
        assert p["priorities"]  # non-empty defaults

    def test_premortem_extracts_project(self):
        p = extract_payload("PREMORTEM", "premortem on launching HELEN OS commercially")
        assert "HELEN OS" in p["project"]

    def test_weekly_review_preserves_text(self):
        p = extract_payload("WEEKLY_REVIEW", "completed: fixed CI, deployed v2")
        assert p["completed"]

    def test_note_synthesis_preserves_notes(self):
        p = extract_payload("NOTE_SYNTHESIS", "synthesize my notes on governance")
        assert p["notes"]


# ===================================================================
# Intent envelope
# ===================================================================

class TestEnvelope:
    def test_envelope_creation(self):
        env = make_envelope("FIRST_DRAFT", "write a post", {"content_type": "post", "topic": "AI"})
        assert env.schema_version == SCHEMA_VERSION
        assert env.intent_type == "FIRST_DRAFT"
        assert env.authority == "NONE"
        assert env.intent_id.startswith("intent_")

    def test_envelope_is_frozen(self):
        env = make_envelope("PREMORTEM", "test", {"project": "X"})
        with pytest.raises(AttributeError):
            env.authority = "ADMIN"

    def test_envelope_to_dict(self):
        env = make_envelope("PREMORTEM", "test", {"project": "X"})
        d = env.to_dict()
        assert d["authority"] == "NONE"
        assert d["schema_version"] == SCHEMA_VERSION

    def test_unique_ids(self):
        ids = set()
        for _ in range(20):
            env = make_envelope("FIRST_DRAFT", "test", {"content_type": "x", "topic": "y"})
            ids.add(env.intent_id)
        assert len(ids) == 20


# ===================================================================
# Governor
# ===================================================================

class TestGovernor:
    def test_valid_intent_allowed(self):
        env = make_envelope("FIRST_DRAFT", "write a post", {"content_type": "post", "topic": "AI"})
        ok, reason = govern_intent(env)
        assert ok
        assert reason is None

    def test_unknown_type_rejected(self):
        env = IntentEnvelope(SCHEMA_VERSION, "i1", "FAKE_TYPE", "x", {}, 0.8, "NONE")
        ok, reason = govern_intent(env)
        assert not ok
        assert "Unknown" in reason

    def test_authority_not_none_rejected(self):
        env = IntentEnvelope(SCHEMA_VERSION, "i2", "FIRST_DRAFT", "x",
                            {"content_type": "post", "topic": "AI"}, 0.8, "ADMIN")
        ok, reason = govern_intent(env)
        assert not ok
        assert "Authority" in reason

    def test_low_confidence_rejected(self):
        env = IntentEnvelope(SCHEMA_VERSION, "i3", "FIRST_DRAFT", "x",
                            {"content_type": "post", "topic": "AI"}, 0.01, "NONE")
        ok, reason = govern_intent(env)
        assert not ok
        assert "Confidence" in reason

    def test_missing_payload_field_rejected(self):
        env = make_envelope("DECISION_ANALYSIS", "decide", {"decision": "A or B"})
        ok, reason = govern_intent(env)
        assert not ok
        assert "options" in reason or "priorities" in reason

    def test_sovereign_field_rejected(self):
        env = IntentEnvelope(SCHEMA_VERSION, "i4", "FIRST_DRAFT", "x",
                            {"content_type": "post", "topic": "AI", "authority": "ROOT"}, 0.8, "NONE")
        ok, reason = govern_intent(env)
        assert not ok
        assert "Sovereign" in reason

    def test_all_5_core_intents_pass(self):
        cases = [
            ("FIRST_DRAFT", {"content_type": "post", "topic": "AI"}),
            ("DECISION_ANALYSIS", {"decision": "A or B", "options": [{"name": "A"}], "priorities": ["x"]}),
            ("WEEKLY_REVIEW", {"completed": ["x"], "in_progress": [], "blocked": []}),
            ("PREMORTEM", {"project": "Launch"}),
            ("NOTE_SYNTHESIS", {"notes": ["a", "b"]}),
        ]
        for intent_type, payload in cases:
            env = make_envelope(intent_type, "test", payload)
            ok, reason = govern_intent(env)
            assert ok, f"{intent_type} failed: {reason}"


# ===================================================================
# Route input (full pipeline)
# ===================================================================

class TestRouteInput:
    def test_full_pipeline(self):
        env = route_input("help me decide between launching a SaaS or a marketplace")
        assert env.intent_type == "DECISION_ANALYSIS"
        assert env.schema_version == SCHEMA_VERSION
        assert env.authority == "NONE"
        assert env.payload.get("options")

    def test_governor_passes_routed_input(self):
        env = route_input("do a premortem on my product launch")
        ok, reason = govern_intent(env)
        assert ok, reason

    def test_all_intents_route_correctly(self):
        test_cases = [
            ("write me a newsletter", "FIRST_DRAFT"),
            ("decide between A or B", "DECISION_ANALYSIS"),
            ("weekly review of my work", "WEEKLY_REVIEW"),
            ("premortem on launching X", "PREMORTEM"),
            ("synthesize my notes", "NOTE_SYNTHESIS"),
        ]
        for text, expected in test_cases:
            env = route_input(text)
            assert env.intent_type == expected, f"'{text}' -> {env.intent_type}, expected {expected}"


# ===================================================================
# Receipt
# ===================================================================

class TestReceipt:
    def test_receipt_creation(self):
        env = make_envelope("FIRST_DRAFT", "write a post", {"content_type": "post", "topic": "AI"})
        result = IntentResult(
            intent_id=env.intent_id,
            intent_type=env.intent_type,
            status="COMPLETED",
            output={"draft_text": "Hello world"},
            output_hash="abc123",
        )
        receipt = make_receipt(env, result)
        assert receipt.receipt_type == "INTENT_EXECUTION_RECEIPT_V1"
        assert receipt.intent_type == "FIRST_DRAFT"
        assert receipt.authority == "NONE"
        assert receipt.status == "COMPLETED"

    def test_receipt_authority_always_none(self):
        env = make_envelope("PREMORTEM", "test", {"project": "X"})
        result = IntentResult("i", "PREMORTEM", "COMPLETED", {}, "h")
        receipt = make_receipt(env, result)
        assert receipt.authority == "NONE"


# ===================================================================
# Memory write rules
# ===================================================================

class TestMemoryRules:
    def test_writable_intents_are_explicit(self):
        writable = {k for k, v in INTENT_REGISTRY.items() if v.get("memory_writable")}
        assert writable == MEMORY_WRITABLE_INTENTS

    def test_first_draft_not_writable(self):
        assert "FIRST_DRAFT" not in MEMORY_WRITABLE_INTENTS

    def test_voice_profile_writable(self):
        assert "VOICE_PROFILE" in MEMORY_WRITABLE_INTENTS

    def test_weekly_review_writable(self):
        assert "WEEKLY_REVIEW" in MEMORY_WRITABLE_INTENTS
