"""EGREGOR Registry — Source of truth for model routing.

Rules:
- Deterministic
- No dynamic mutation
- No hidden logic

This file must stay boring.
If this becomes dynamic, your system becomes non-deterministic.
"""
from __future__ import annotations

from typing import Dict, List

# Each street = ordered list (primary → fallback)
EGREGOR_ROUTES: Dict[str, List[str]] = {
    "chat": [
        "helen-chat",     # primary conversational identity
        "helen-core",     # fallback
    ],
    "code": [
        "her-coder",              # HELEN-aligned coder
        "qwen2.5-coder:7b",      # fallback
    ],
    "reason": [
        "deepseek-r1:8b",        # reasoning specialist
        "gemma4",                 # fallback generalist
    ],
    "fast": [
        "helen-ship",             # ultra fast HELEN
        "qwen2.5:3b",            # fallback
    ],
}

# Allowed streets (hard boundary)
VALID_STREETS = set(EGREGOR_ROUTES.keys())


def get_models_for_street(street: str) -> List[str]:
    """Return model chain for a street. Raises on unknown street."""
    if street not in EGREGOR_ROUTES:
        raise ValueError(f"Unknown street: {street}")
    return EGREGOR_ROUTES[street]


__all__ = [
    "EGREGOR_ROUTES",
    "VALID_STREETS",
    "get_models_for_street",
]
