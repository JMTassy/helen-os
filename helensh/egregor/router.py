"""HELEN OS — Egregor v0 Router.

Dumb deterministic classifier. No embeddings. No learned routing.
Same input → same street. Always.

    code keywords → "code"
    fast keywords → "fast"
    everything else → "chat"

"review" street is never auto-routed — it's called structurally
by the executor (HAL reviews every answer). You don't route to review;
review happens to you.
"""
from __future__ import annotations

from helensh.egregor.registry import DEFAULT_STREET

# ── Keywords ─────────────────────────────────────────────────────────────────

_CODE_KEYWORDS = (
    "code", "python", "bug", "function", "refactor", "implement",
    "class ", "def ", "import ", "test", "debug", "script", "api",
    "sql", "regex", "parse", "compile", "syntax", "error",
)

_FAST_KEYWORDS = (
    "quick", "fast", "brief", "one line", "short", "tldr", "yes or no",
    "one word",
)


# ── Classifier ───────────────────────────────────────────────────────────────

def classify(task: str) -> str:
    """Classify a task string into a street name.

    Deterministic: same input → same output. No randomness.
    Returns one of: "code", "fast", "chat".
    """
    t = task.lower()
    if any(kw in t for kw in _CODE_KEYWORDS):
        return "code"
    if any(kw in t for kw in _FAST_KEYWORDS):
        return "fast"
    return DEFAULT_STREET


__all__ = ["classify"]
