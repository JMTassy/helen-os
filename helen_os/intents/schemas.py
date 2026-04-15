"""
HELEN OS Intent Schemas V1

Every prompt-shaped request must first become a typed intent.
No raw prompt executes directly.

Schema version: HELEN_INTENT_V1
"""

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple, FrozenSet


SCHEMA_VERSION = "HELEN_INTENT_V1"


# ---------------------------------------------------------------------------
# Intent type definitions — closed vocabulary
# ---------------------------------------------------------------------------

# Writing family
WRITING_INTENTS = frozenset({
    "FIRST_DRAFT",
    "THREAD_EXPANSION",
    "CONTENT_REPURPOSE",
    "HEADLINE_GENERATION",
    "EMAIL_SEQUENCE",
    "SEO_BRIEF",
    "VOICE_PROFILE",
})

# Analysis family
ANALYSIS_INTENTS = frozenset({
    "MEETING_BRIEF",
    "DECISION_ANALYSIS",
    "COMPETITOR_ANALYSIS",
    "BOOK_SUMMARY",
    "DATA_ANALYSIS",
    "SOP_GENERATION",
    "ASSUMPTION_STRESS_TEST",
})

# Execution-support family
EXECUTION_INTENTS = frozenset({
    "WEEKLY_REVIEW",
    "CLIENT_PROPOSAL",
    "OUTREACH_GENERATION",
    "FEEDBACK_TRANSLATION",
    "MEETING_OPTIMIZATION",
    "PRICING_ANALYSIS",
    "TASK_DELEGATION",
})

# Strategy family
STRATEGY_INTENTS = frozenset({
    "REVERSE_BRAINSTORM",
    "PREMORTEM",
    "NOTE_SYNTHESIS",
    "ADVISOR_SIMULATION",
})

ALL_INTENT_TYPES = WRITING_INTENTS | ANALYSIS_INTENTS | EXECUTION_INTENTS | STRATEGY_INTENTS

# Intents allowed to write memory
MEMORY_WRITABLE_INTENTS = frozenset({
    "VOICE_PROFILE",
    "WEEKLY_REVIEW",
    "NOTE_SYNTHESIS",
})


# ---------------------------------------------------------------------------
# Per-intent payload schemas — required and optional fields
# ---------------------------------------------------------------------------

INTENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "FIRST_DRAFT": {
        "family": "writing",
        "required": ["content_type", "topic"],
        "optional": ["audience", "tone", "word_count", "negative_constraints", "style_sample_ref"],
        "result_fields": ["draft_text", "word_count", "tone_used"],
        "description": "Generate a first draft of written content.",
    },
    "DECISION_ANALYSIS": {
        "family": "analysis",
        "required": ["decision", "options", "priorities"],
        "optional": [],
        "result_fields": ["recommended_option", "factor_scores", "top_risks", "reasoning_summary"],
        "description": "Score options against ranked priorities and recommend one.",
    },
    "WEEKLY_REVIEW": {
        "family": "execution",
        "required": ["completed", "in_progress", "blocked"],
        "optional": ["wins"],
        "result_fields": ["highest_impact", "time_waste", "blocker_pattern", "next_priorities", "stop_doing"],
        "memory_writable": True,
        "description": "Analyze a week's work and produce actionable priorities.",
    },
    "PREMORTEM": {
        "family": "strategy",
        "required": ["project"],
        "optional": ["timeline_months"],
        "result_fields": ["failure_points", "warning_signs", "wrong_assumptions", "affected_parties", "preventions"],
        "description": "Imagine failure 6 months from now and work backward.",
    },
    "NOTE_SYNTHESIS": {
        "family": "strategy",
        "required": ["notes"],
        "optional": ["timeframe"],
        "result_fields": ["themes", "connections", "key_insight", "action_items", "questions"],
        "memory_writable": True,
        "description": "Synthesize scattered notes into patterns and actionable insight.",
    },
    # --- Stub entries for remaining 20 intents (schema only, no executor yet) ---
    "THREAD_EXPANSION": {
        "family": "writing", "required": ["idea"], "optional": ["post_count"],
        "result_fields": ["posts"], "description": "Expand an idea into a thread.",
    },
    "CONTENT_REPURPOSE": {
        "family": "writing", "required": ["content"], "optional": ["platforms"],
        "result_fields": ["tweets", "linkedin", "instagram", "email_teaser"],
        "description": "Repurpose long-form content across platforms.",
    },
    "HEADLINE_GENERATION": {
        "family": "writing", "required": ["topic"], "optional": ["count"],
        "result_fields": ["headlines", "top_picks"],
        "description": "Generate headline variations across frameworks.",
    },
    "EMAIL_SEQUENCE": {
        "family": "writing", "required": ["product", "audience", "pain_point", "goal"],
        "optional": ["email_count"], "result_fields": ["emails"],
        "description": "Write a welcome email sequence.",
    },
    "SEO_BRIEF": {
        "family": "writing", "required": ["keyword"], "optional": [],
        "result_fields": ["title", "meta", "slug", "outline", "related_keywords"],
        "description": "Create an SEO content brief.",
    },
    "VOICE_PROFILE": {
        "family": "writing", "required": ["samples"], "optional": [],
        "result_fields": ["voice_profile", "sample_paragraph"],
        "memory_writable": True, "description": "Extract writing voice from samples.",
    },
    "MEETING_BRIEF": {
        "family": "analysis", "required": ["person", "company"], "optional": ["topic", "time"],
        "result_fields": ["background", "company_info", "talking_points", "questions", "common_ground"],
        "description": "Prepare a one-page meeting brief.",
    },
    "COMPETITOR_ANALYSIS": {
        "family": "analysis", "required": ["competitor"], "optional": ["our_product"],
        "result_fields": ["offering", "pricing", "positioning", "strengths", "weaknesses", "opportunities"],
        "description": "Competitive intelligence breakdown.",
    },
    "BOOK_SUMMARY": {
        "family": "analysis", "required": ["title", "author"], "optional": ["context"],
        "result_fields": ["thesis", "key_ideas", "strongest_argument", "weakest_argument", "applications", "quotes"],
        "description": "Structured book summary with applications.",
    },
    "DATA_ANALYSIS": {
        "family": "analysis", "required": ["data_description"], "optional": ["data"],
        "result_fields": ["trends", "anomalies", "correlations", "recommendations", "limitations"],
        "description": "Analyze data and produce executive + detailed summaries.",
    },
    "SOP_GENERATION": {
        "family": "analysis", "required": ["process_description"], "optional": [],
        "result_fields": ["purpose", "frequency", "prerequisites", "steps", "quality_checks", "common_mistakes", "time_estimate"],
        "description": "Convert a casual process description into a formal SOP.",
    },
    "ASSUMPTION_STRESS_TEST": {
        "family": "analysis", "required": ["project", "assumptions"], "optional": [],
        "result_fields": ["confidence_ratings", "conditions", "worst_cases", "validation_methods", "hidden_assumptions"],
        "description": "Stress-test explicit and hidden assumptions.",
    },
    "CLIENT_PROPOSAL": {
        "family": "execution", "required": ["client", "project", "problem"], "optional": ["timeline", "budget"],
        "result_fields": ["understanding", "solution", "scope", "timeline", "investment", "next_step"],
        "description": "Draft a client project proposal.",
    },
    "OUTREACH_GENERATION": {
        "family": "execution", "required": ["person", "company", "offer"], "optional": ["relevant_detail"],
        "result_fields": ["email_text"], "description": "Write personalized cold outreach.",
    },
    "FEEDBACK_TRANSLATION": {
        "family": "execution", "required": ["feedback"], "optional": ["response_tone"],
        "result_fields": ["actionable_points", "real_ask", "severity", "response_draft"],
        "description": "Translate emotional feedback into actionable points.",
    },
    "MEETING_OPTIMIZATION": {
        "family": "execution", "required": ["agenda"], "optional": [],
        "result_fields": ["can_be_async", "async_document", "decision_needed", "required_attendees", "minimum_time", "pre_read"],
        "description": "Kill or shorten a meeting.",
    },
    "PRICING_ANALYSIS": {
        "family": "execution", "required": ["product", "audience", "current_price"], "optional": ["competitors"],
        "result_fields": ["assessment", "recommended_model", "price_sensitivity", "objection_handler"],
        "description": "Analyze pricing strategy.",
    },
    "TASK_DELEGATION": {
        "family": "execution", "required": ["task_description"], "optional": [],
        "result_fields": ["summary", "definition_of_done", "constraints", "decision_authority", "check_ins", "common_mistakes"],
        "description": "Format a task for delegation.",
    },
    "REVERSE_BRAINSTORM": {
        "family": "strategy", "required": ["goal"], "optional": [],
        "result_fields": ["failure_modes", "inverted_strategies", "top_picks", "first_steps"],
        "description": "Brainstorm failure modes then invert into strategies.",
    },
    "ADVISOR_SIMULATION": {
        "family": "strategy", "required": ["situation"], "optional": [],
        "result_fields": ["perspectives", "synthesis", "tension_points"],
        "description": "Simulate 5 advisor perspectives on a situation.",
    },
}


# ---------------------------------------------------------------------------
# Core data structures
# ---------------------------------------------------------------------------

def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(data: Any) -> str:
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class IntentEnvelope:
    schema_version: str
    intent_id: str
    intent_type: str
    source_input: str
    payload: Dict[str, Any]
    confidence: float
    authority: str = "NONE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntentResult:
    intent_id: str
    intent_type: str
    status: str  # COMPLETED | FAILED | REJECTED
    output: Dict[str, Any]
    output_hash: str
    memory_candidate: Optional[Dict[str, Any]] = None
    authority: str = "NONE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IntentReceipt:
    receipt_type: str  # INTENT_EXECUTION_RECEIPT_V1
    intent_id: str
    intent_type: str
    module: str
    input_hash: str
    output_hash: str
    status: str
    authority: str = "NONE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INTENT_COUNTER = 0


def _next_intent_id() -> str:
    global _INTENT_COUNTER
    _INTENT_COUNTER += 1
    return f"intent_{_INTENT_COUNTER:04d}"


def validate_payload(intent_type: str, payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate payload against intent schema. Returns (valid, errors)."""
    if intent_type not in INTENT_REGISTRY:
        return False, [f"Unknown intent type: {intent_type}"]

    schema = INTENT_REGISTRY[intent_type]
    errors = []
    for field_name in schema["required"]:
        if field_name not in payload or payload[field_name] is None:
            errors.append(f"Missing required field: {field_name}")
        elif isinstance(payload[field_name], str) and not payload[field_name].strip():
            errors.append(f"Empty required field: {field_name}")

    return len(errors) == 0, errors


def make_envelope(intent_type: str, source_input: str, payload: Dict[str, Any],
                  confidence: float = 0.8) -> IntentEnvelope:
    """Create a typed intent envelope."""
    return IntentEnvelope(
        schema_version=SCHEMA_VERSION,
        intent_id=_next_intent_id(),
        intent_type=intent_type,
        source_input=source_input,
        payload=payload,
        confidence=confidence,
        authority="NONE",
    )


def make_receipt(envelope: IntentEnvelope, result: IntentResult) -> IntentReceipt:
    """Create an execution receipt for a completed intent."""
    return IntentReceipt(
        receipt_type="INTENT_EXECUTION_RECEIPT_V1",
        intent_id=envelope.intent_id,
        intent_type=envelope.intent_type,
        module=envelope.intent_type.lower(),
        input_hash=_hash(envelope.payload),
        output_hash=result.output_hash,
        status=result.status,
        authority="NONE",
    )
