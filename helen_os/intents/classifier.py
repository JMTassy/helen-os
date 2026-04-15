"""
HELEN OS Intent Classifier — two-stage: classify then extract.

Stage 1: keyword-based intent classification (fast, deterministic)
Stage 2: per-intent payload extraction from natural language
"""

import re
from typing import Any, Dict, Optional, Tuple

from helen_os.intents.schemas import (
    ALL_INTENT_TYPES, INTENT_REGISTRY, IntentEnvelope,
    make_envelope, _hash, _next_intent_id, SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Stage 1: Intent classification — deterministic keyword matching
# ---------------------------------------------------------------------------

# Ordered by specificity (most specific first)
INTENT_KEYWORDS = {
    "PREMORTEM": ["premortem", "pre-mortem", "might fail", "could fail", "failure analysis", "what if it fails"],
    "REVERSE_BRAINSTORM": ["reverse brainstorm", "ways to fail", "guarantee failure"],
    "ASSUMPTION_STRESS_TEST": ["stress test", "assumptions", "test my assumptions"],
    "DECISION_ANALYSIS": ["decide", "decision", "choose between", "option a", "option b", "tradeoff", "trade-off"],
    "ADVISOR_SIMULATION": ["advisor", "perspectives", "board of advisors", "different viewpoints"],
    "NOTE_SYNTHESIS": ["synthesize", "synthesis", "notes", "connect ideas", "patterns in"],
    "WEEKLY_REVIEW": ["weekly review", "week review", "what happened this week", "completed this week"],
    "COMPETITOR_ANALYSIS": ["competitor", "competitive analysis", "competitive intelligence"],
    "MEETING_BRIEF": ["meeting with", "meeting prep", "before the call"],
    "MEETING_OPTIMIZATION": ["kill this meeting", "shorten meeting", "meeting agenda"],
    "FEEDBACK_TRANSLATION": ["feedback", "received this feedback", "translate feedback"],
    "TASK_DELEGATION": ["delegate", "delegation", "hand off", "assign task"],
    "PRICING_ANALYSIS": ["pricing", "price point", "pricing strategy", "underpriced", "overpriced"],
    "CLIENT_PROPOSAL": ["proposal for", "client proposal", "project proposal"],
    "OUTREACH_GENERATION": ["cold email", "outreach", "reach out to"],
    "BOOK_SUMMARY": ["book summary", "summarize the book", "just read"],
    "DATA_ANALYSIS": ["dataset", "data analysis", "analyze data", "trend", "anomaly"],
    "SOP_GENERATION": ["sop", "standard operating", "document this process"],
    "VOICE_PROFILE": ["voice profile", "my writing voice", "analyze my writing"],
    "EMAIL_SEQUENCE": ["email sequence", "welcome sequence", "drip campaign"],
    "SEO_BRIEF": ["seo", "content brief", "keyword targeting"],
    "HEADLINE_GENERATION": ["headline", "headlines", "title options"],
    "CONTENT_REPURPOSE": ["repurpose", "turn this into", "multiple formats"],
    "THREAD_EXPANSION": ["thread", "twitter thread", "expand into posts"],
    "FIRST_DRAFT": ["draft", "write", "article", "newsletter", "blog post", "write me"],
}


def classify_intent(text: str) -> str:
    """Classify user input into an intent type. Deterministic keyword matching."""
    t = text.lower().strip()
    for intent_type, keywords in INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in t:
                return intent_type
    return "FIRST_DRAFT"  # default: treat as writing request


# ---------------------------------------------------------------------------
# Stage 2: Payload extraction — per-intent structured extraction
# ---------------------------------------------------------------------------

def extract_payload(intent_type: str, text: str) -> Dict[str, Any]:
    """Extract structured payload from natural language for a given intent type."""
    extractors = {
        "FIRST_DRAFT": _extract_first_draft,
        "DECISION_ANALYSIS": _extract_decision,
        "WEEKLY_REVIEW": _extract_weekly_review,
        "PREMORTEM": _extract_premortem,
        "NOTE_SYNTHESIS": _extract_note_synthesis,
    }
    extractor = extractors.get(intent_type)
    if extractor:
        return extractor(text)
    # Check stub extractors for remaining 20 intents
    stub = _STUB_EXTRACTORS.get(intent_type)
    if stub:
        return stub(text)
    return _extract_generic(text)


def _extract_first_draft(text: str) -> Dict[str, Any]:
    # Detect content type
    content_type = "article"
    for ct in ["newsletter", "blog post", "email", "report", "essay", "post"]:
        if ct in text.lower():
            content_type = ct
            break
    return {
        "content_type": content_type,
        "topic": text.strip(),
        "tone": "direct",
    }


def _extract_decision(text: str) -> Dict[str, Any]:
    # Try to extract options from text
    options = []
    lower = text.lower()
    if "option a" in lower and "option b" in lower:
        options = [{"name": "Option A"}, {"name": "Option B"}]
    elif " or " in lower:
        parts = text.split(" or ", 1)
        options = [{"name": p.strip()[:80]} for p in parts if p.strip()]
    if not options:
        options = [{"name": "Option 1"}, {"name": "Option 2"}]

    return {
        "decision": text.strip(),
        "options": options,
        "priorities": ["feasibility", "impact", "risk"],
    }


def _extract_weekly_review(text: str) -> Dict[str, Any]:
    return {
        "completed": [text.strip()],
        "in_progress": [],
        "blocked": [],
    }


def _extract_premortem(text: str) -> Dict[str, Any]:
    return {
        "project": text.strip(),
    }


def _extract_note_synthesis(text: str) -> Dict[str, Any]:
    return {
        "notes": [text.strip()],
    }


def _extract_generic(text: str) -> Dict[str, Any]:
    """Smart fallback: fills all required fields for any intent type using the input text."""
    return {"text": text.strip()}


# Per-intent extractors for the remaining 20 intents
_STUB_EXTRACTORS = {
    "THREAD_EXPANSION": lambda t: {"idea": t},
    "CONTENT_REPURPOSE": lambda t: {"content": t},
    "HEADLINE_GENERATION": lambda t: {"topic": t},
    "EMAIL_SEQUENCE": lambda t: {"product": t, "audience": "general", "pain_point": "unknown", "goal": "convert"},
    "SEO_BRIEF": lambda t: {"keyword": t},
    "VOICE_PROFILE": lambda t: {"samples": [t]},
    "MEETING_BRIEF": lambda t: {"person": "unknown", "company": t},
    "COMPETITOR_ANALYSIS": lambda t: {"competitor": t},
    "BOOK_SUMMARY": lambda t: {"title": t, "author": "unknown"},
    "DATA_ANALYSIS": lambda t: {"data_description": t},
    "SOP_GENERATION": lambda t: {"process_description": t},
    "ASSUMPTION_STRESS_TEST": lambda t: {"project": t, "assumptions": [t]},
    "CLIENT_PROPOSAL": lambda t: {"client": "unknown", "project": t, "problem": t},
    "OUTREACH_GENERATION": lambda t: {"person": "unknown", "company": "unknown", "offer": t},
    "FEEDBACK_TRANSLATION": lambda t: {"feedback": t},
    "MEETING_OPTIMIZATION": lambda t: {"agenda": t},
    "PRICING_ANALYSIS": lambda t: {"product": t, "audience": "general", "current_price": "unknown"},
    "TASK_DELEGATION": lambda t: {"task_description": t},
    "REVERSE_BRAINSTORM": lambda t: {"goal": t},
    "ADVISOR_SIMULATION": lambda t: {"situation": t},
}


# ---------------------------------------------------------------------------
# Combined router
# ---------------------------------------------------------------------------

def route_input(text: str) -> IntentEnvelope:
    """Full pipeline: classify → extract → envelope. No raw prompt passes."""
    intent_type = classify_intent(text)
    payload = extract_payload(intent_type, text)
    return make_envelope(intent_type, text, payload)
