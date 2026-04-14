"""Deterministic task → street classifier.

No embeddings. No LLM. Just rules.
Same input → same output. Always.

Smart routing comes later. First: correctness.
"""
from __future__ import annotations


def classify(task: str) -> str:
    """Classify a task into a street name. Deterministic."""
    t = task.lower()

    if any(k in t for k in ("code", "function", "python", "bug", "fix")):
        return "code"

    if any(k in t for k in ("why", "explain", "reason", "proof")):
        return "reason"

    if any(k in t for k in ("quick", "fast", "short")):
        return "fast"

    return "chat"


__all__ = ["classify"]
