"""
HELEN OS — Output Redaction Utilities

Firewall-grade sanitization for AIRI bridge output.
Strips authority tokens, secrets, hashes, and internal paths
before anything reaches the avatar layer.

Constitutional guarantee: NO RECEIPT → NO SHIP.
AIRI is UI only. The firewall holds.
"""

import re
from typing import Tuple, List, Dict


# ---------------------------------------------------------------------------
# Authority tokens — NEVER leak governance verdicts to AIRI
# ---------------------------------------------------------------------------
AUTHORITY_TOKENS = [
    "VERDICT", "SEALED", "SHIP", "APPROVED", "TERMINATION",
    "ALLOW", "DENY", "PENDING", "ROLLBACK",
    "GOVERNOR", "REDUCER", "SOVEREIGN",
]

AUTHORITY_PATTERN = re.compile(
    r'\b(' + '|'.join(AUTHORITY_TOKENS) + r')(?:\s*[:=]\s*\S+)?',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Secret patterns — API keys, Bearer tokens, passwords
# ---------------------------------------------------------------------------
SECRET_PATTERNS = [
    (re.compile(r'Bearer\s+\S+', re.IGNORECASE), "bearer_token"),
    (re.compile(r'api[_-]?key\s*[:=]\s*\S+', re.IGNORECASE), "api_key"),
    (re.compile(r'sk-[a-zA-Z0-9_-]{20,}'), "api_key"),
    (re.compile(r'password\s*[:=]\s*\S+', re.IGNORECASE), "password"),
    (re.compile(r'secret\s*[:=]\s*\S+', re.IGNORECASE), "secret"),
    (re.compile(r'token\s*[:=]\s*\S+', re.IGNORECASE), "token"),
]

# ---------------------------------------------------------------------------
# Hash patterns — SHA256, receipt IDs, cum_hash
# ---------------------------------------------------------------------------
HASH_PATTERN = re.compile(r'\b[a-f0-9]{64}\b')
RECEIPT_PATTERN = re.compile(r'\b(receipt|cum_hash|prev_hash)\s*[:=]\s*\S+', re.IGNORECASE)

# ---------------------------------------------------------------------------
# Path patterns — internal filesystem paths
# ---------------------------------------------------------------------------
PATH_PATTERNS = [
    re.compile(r'/town/\S+'),
    re.compile(r'\S+\.ndjson\b'),
    re.compile(r'\S+\.lock\b'),
    re.compile(r'\S+\.seq\b'),
    re.compile(r'\S+memory\.db\b'),
    re.compile(r'\S+/helensh/\S+'),
]

# ---------------------------------------------------------------------------
# Emotion mapping — text → avatar emotion state
# ---------------------------------------------------------------------------
EMOTION_KEYWORDS = {
    "concern": ["worry", "concern", "problem", "issue", "tension", "stuck", "fail", "error", "wrong"],
    "happy": ["great", "perfect", "excellent", "love", "beautiful", "wonderful", "success", "done", "shipped"],
    "thinking": ["hmm", "consider", "perhaps", "maybe", "wonder", "might", "interesting", "curious"],
    "neutral": [],
}


def map_emotion(text: str) -> str:
    """Map text content to an emotion state for AIRI avatar."""
    lower = text.lower()
    scores = {emotion: 0 for emotion in EMOTION_KEYWORDS}

    for emotion, keywords in EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                scores[emotion] += 1

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "neutral"


def redact_secrets(text: str) -> Tuple[str, List[str]]:
    """Redact API keys, Bearer tokens, passwords. Returns (clean_text, redaction_log)."""
    log = []
    for pattern, label in SECRET_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            log.append(f"redacted:{label}:{len(matches)}")
            text = pattern.sub("[REDACTED]", text)
    return text, log


def strip_authority_tokens(text: str) -> Tuple[str, List[str]]:
    """Strip governance authority tokens. Returns (clean_text, redaction_log)."""
    log = []
    matches = AUTHORITY_PATTERN.findall(text)
    if matches:
        log.append(f"stripped:authority_tokens:{len(matches)}")
        text = AUTHORITY_PATTERN.sub("[REDACTED]", text)
    return text, log


def redact_hashes(text: str) -> Tuple[str, List[str]]:
    """Redact SHA256 hashes and receipt IDs. Returns (clean_text, redaction_log)."""
    log = []
    hash_matches = HASH_PATTERN.findall(text)
    if hash_matches:
        log.append(f"redacted:hash:{len(hash_matches)}")
        text = HASH_PATTERN.sub("[HASH]", text)

    receipt_matches = RECEIPT_PATTERN.findall(text)
    if receipt_matches:
        log.append(f"redacted:receipt:{len(receipt_matches)}")
        text = RECEIPT_PATTERN.sub("[REDACTED]", text)
    return text, log


def redact_paths(text: str) -> Tuple[str, List[str]]:
    """Redact internal filesystem paths. Returns (clean_text, redaction_log)."""
    log = []
    for pattern in PATH_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            log.append(f"redacted:path:{len(matches)}")
            text = pattern.sub("[PATH]", text)
    return text, log


def sanitize_output_for_airi(text: str) -> Tuple[str, List[str]]:
    """
    Full sanitization pipeline for AIRI output.
    Applies all redaction layers in order.

    Returns:
        (sanitized_text, redaction_log)
    """
    all_log = []

    text, log = redact_secrets(text)
    all_log.extend(log)

    text, log = strip_authority_tokens(text)
    all_log.extend(log)

    text, log = redact_hashes(text)
    all_log.extend(log)

    text, log = redact_paths(text)
    all_log.extend(log)

    # Clean up multiple spaces and [REDACTED] chains
    text = re.sub(r'\[REDACTED\](\s*\[REDACTED\])+', '[REDACTED]', text)
    text = re.sub(r'\s{2,}', ' ', text).strip()

    return text, all_log
