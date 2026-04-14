"""HELEN OS — Egregor v0 Registry.

4 streets. That's it.

    chat   → helen-chat, helen-core
    code   → her-coder, qwen2.5-coder:7b
    review → hal-reviewer
    fast   → helen-ship, qwen2.5:3b

Each street has an ordered fallback chain.
Primary fails → next in list. All fail → governed failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

# ── The 4 streets ────────────────────────────────────────────────────────────

EGREGOR_ROUTES: Dict[str, List[str]] = {
    "chat": ["helen-chat:latest", "helen-core:latest"],
    "code": ["her-coder:latest", "qwen2.5-coder:7b"],
    "review": ["hal-reviewer:latest"],
    "fast": ["helen-ship:latest", "qwen2.5:3b"],
}

# Default street when classifier finds nothing
DEFAULT_STREET = "chat"

# All models referenced by the registry (union of all chains)
REGISTRY_MODELS = sorted({m for chain in EGREGOR_ROUTES.values() for m in chain})


def get_chain(street: str) -> List[str]:
    """Return the fallback chain for a street. Empty list if unknown."""
    return list(EGREGOR_ROUTES.get(street, []))


def list_streets() -> List[str]:
    """Return all registered street names."""
    return sorted(EGREGOR_ROUTES.keys())


__all__ = [
    "EGREGOR_ROUTES",
    "DEFAULT_STREET",
    "REGISTRY_MODELS",
    "get_chain",
    "list_streets",
]
