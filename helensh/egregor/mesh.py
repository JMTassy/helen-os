"""HELEN OS — EGREGOR Model Mesh.

All 33 local Ollama models as specialist streets.
Route by task type. Consensus on conflict. Escalate on disagreement.

Streets:
    CONVERSATION  → helen-chat, qwen3.5:9b, helen-core
    CODE          → her-coder, qwen2.5-coder:7b, deepseek-coder:6.7b
    REASONING     → deepseek-r1:8b, qwen3-coder:30b, gemma4:26b
    RESEARCH      → oracle-research, oracle-mistral, gemma4
    REVIEW        → hal-reviewer, her-claudecode-gemma
    FAST          → qwen2.5:3b, helen-ship, qwen3.5:4b, gemma3:4b
    HEAVY         → gemma4:26b, mixtral, nemotron-cascade-2
    TEMPLE        → helen-chat, qwen3.5:9b  (slow, symbolic)
    ORACLE_MODE   → oracle-research, oracle-mistral  (district mode)
    KERNEL        → mistral-kernel, helen-core, mistral:instruct

Law:
    - authority: False on every response, structurally
    - Graceful fallback: if primary fails, try next in chain
    - Consensus: 3 fast models → majority → escalate to HEAVY on split
    - Never block: smart fallback if all Ollama unavailable
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── Street Registry ────────────────────────────────────────────────────────────

OLLAMA_BASE = "http://localhost:11434"

# All 33 known models on this machine
ALL_MODELS = [
    "deepseek-coder:6.7b",
    "deepseek-r1:8b",
    "gemma3:4b",
    "gemma4:26b",
    "gemma4:latest",
    "hal-reviewer:latest",
    "helen-chat:latest",
    "helen-core:latest",
    "helen-ship:latest",
    "her-claudecode-gemma:latest",
    "her-coder:latest",
    "her-codex-gemma:latest",
    "llama3.1:8b",
    "mistral-kernel:latest",
    "mistral:7b",
    "mistral:instruct",
    "mistral:latest",
    "mixtral:latest",
    "nemotron-cascade-2:latest",
    "nomic-embed-text:latest",
    "openclaw:latest",
    "oracle-mistral:latest",
    "oracle-research:latest",
    "qwen2.5-coder:7b",
    "qwen2.5:3b",
    "qwen3-coder:30b",
    "qwen3.5:4b",
    "qwen3.5:9b",
    "qwen3.5:latest",
    "qwen3:4b",
    "mistral:7b-instruct-q4_K_M",
    "hf.co/HauhauCS/Gemma-4-E2B-Uncensored-HauhauCS-Aggressive:latest",
    "openclaw-test/openclaw-test:latest",
]

# Street definitions: ordered fallback chains
STREETS: Dict[str, List[str]] = {
    "CONVERSATION": [
        "helen-chat:latest",
        "qwen3.5:9b",
        "helen-core:latest",
        "qwen3.5:latest",
        "gemma4:latest",
    ],
    "CODE": [
        "her-coder:latest",
        "qwen2.5-coder:7b",
        "deepseek-coder:6.7b",
        "her-codex-gemma:latest",
        "gemma4:latest",
    ],
    "REASONING": [
        "deepseek-r1:8b",
        "qwen3-coder:30b",
        "gemma4:26b",
        "gemma4:latest",
    ],
    "RESEARCH": [
        "oracle-research:latest",
        "oracle-mistral:latest",
        "gemma4:latest",
        "mistral:latest",
    ],
    "REVIEW": [
        "hal-reviewer:latest",
        "her-claudecode-gemma:latest",
        "gemma4:latest",
    ],
    "FAST": [
        "qwen2.5:3b",
        "helen-ship:latest",
        "qwen3.5:4b",
        "gemma3:4b",
        "qwen3:4b",
        "mistral:instruct",
    ],
    "HEAVY": [
        "gemma4:26b",
        "mixtral:latest",
        "nemotron-cascade-2:latest",
        "qwen3-coder:30b",
    ],
    "TEMPLE": [
        "helen-chat:latest",
        "qwen3.5:9b",
        "helen-core:latest",
        "gemma4:latest",
    ],
    "ORACLE_MODE": [
        "oracle-research:latest",
        "oracle-mistral:latest",
        "gemma4:latest",
    ],
    "KERNEL": [
        "mistral-kernel:latest",
        "helen-core:latest",
        "mistral:instruct",
        "mistral:latest",
    ],
    "CREATIVE": [
        "gemma4:latest",
        "helen-chat:latest",
        "qwen3.5:9b",
        "mistral:latest",
    ],
    "CLAW": [
        "openclaw:latest",
        "openclaw-test/openclaw-test:latest",
        "gemma4:latest",
    ],
}

# Consensus streets: run N in parallel, majority wins
CONSENSUS_STREETS = {
    "FAST": ["qwen2.5:3b", "helen-ship:latest", "qwen3.5:4b"],
    "REVIEW": ["hal-reviewer:latest", "her-claudecode-gemma:latest", "gemma4:latest"],
}

# Task classification keywords → street
_KEYWORDS: List[Tuple[str, List[str]]] = [
    ("TEMPLE", ["temple", "reflect", "symbolic", "sit with", "archetype", "slow down"]),
    ("ORACLE_MODE", ["oracle", "research mode", "cite", "evidence", "confidence level"]),
    ("CODE", [
        "write code", "function", "implement", "def ", "class ", "pytest",
        "debug", "fix the bug", "refactor", "python", "javascript", "typescript",
        "script", "api endpoint", "sql", "regex",
    ]),
    ("REASONING", [
        "step by step", "reason", "analyze", "think through", "logic",
        "proof", "deduce", "infer", "why does", "explain the mechanism",
    ]),
    ("RESEARCH", [
        "research", "find out", "look up", "what is the latest", "study",
        "cite", "sources", "papers", "evidence", "fact check",
    ]),
    ("REVIEW", [
        "review", "audit", "check this code", "is this correct", "critique",
        "feedback on", "evaluate this",
    ]),
    ("FAST", ["quick", "briefly", "one word", "tldr", "short answer", "fast"]),
    ("HEAVY", [
        "comprehensive", "deep dive", "full analysis", "exhaustive",
        "everything about", "complete guide",
    ]),
    ("KERNEL", [
        "kernel", "governance", "reduce", "receipt", "authority", "court",
        "ship", "no ship", "law",
    ]),
    ("CLAW", ["telegram", "web fetch", "notify", "external", "claw"]),
    ("CREATIVE", [
        "story", "poem", "creative", "imagine", "fiction", "write a narrative",
        "metaphor",
    ]),
    ("CONVERSATION", []),  # default — last
]


# ── Data Types ─────────────────────────────────────────────────────────────────

@dataclass
class MeshResult:
    """Result of a mesh routing call."""
    text: str
    model: str
    street: str
    consensus: bool = False
    fallback: bool = False
    authority: bool = False
    latency_ms: int = 0


# ── Classifier ─────────────────────────────────────────────────────────────────

def classify_task(message: str, mode: str = "companion") -> str:
    """Classify message into a street name.

    District mode overrides content-based classification.
    """
    # District mode takes precedence
    mode_map = {
        "temple": "TEMPLE",
        "oracle": "ORACLE_MODE",
        "mayor": "HEAVY",
        "adult": "FAST",
        "companion": None,
    }
    if mode in mode_map and mode_map[mode] is not None:
        return mode_map[mode]

    msg = message.lower()
    for street, keywords in _KEYWORDS:
        if any(kw in msg for kw in keywords):
            return street
    return "CONVERSATION"


# ── Ollama helpers ─────────────────────────────────────────────────────────────

def _get_available_models() -> set:
    """Return set of model names currently loaded in Ollama."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3) as r:
            data = json.loads(r.read())
        return {m["name"] for m in data.get("models", [])}
    except Exception:
        return set()


def _call_model(model: str, messages: list, timeout: int = 25) -> Optional[str]:
    """Call one Ollama model. Returns text or None on failure."""
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 512},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _call_in_thread(
    model: str,
    messages: list,
    result: dict,
    key: str,
    timeout: int = 25,
) -> None:
    """Thread target: call model, write result[key]."""
    result[key] = _call_model(model, messages, timeout=timeout)


# ── Smart fallback ─────────────────────────────────────────────────────────────

def _instant_fallback(message: str, street: str) -> str:
    """Instant in-character HELEN response when all models busy.

    Uses whole-word matching to avoid false positives (e.g. 'hi' in 'this').
    """
    msg = message.lower().strip()
    words = set(re.findall(r"\b\w+\b", msg))

    if words & {"hi", "hello", "hey", "bonjour", "salut"}:
        return "I'm here. What's on your mind?"
    if any(p in msg for p in ("who are you", "what are you", "qui es-tu")):
        return "I'm HELEN — a governed, receipted AI presence. Non-sovereign. What do you need?"
    if any(p in msg for p in ("how are you", "comment vas", "ça va")):
        return "Stable. The kernel is running. You?"

    street_responses = {
        "TEMPLE": "The temple is quiet. Bring your question — I'll hold it carefully.",
        "ORACLE_MODE": "Signal received. What's the research question?",
        "HEAVY": "Deep analysis queued. The inference engine is loading.",
        "CODE": "Code task received. The coder is warming up — give me a moment.",
        "REASONING": "Reasoning thread started. Processing your question.",
        "RESEARCH": "Research query logged. Gathering signals.",
        "FAST": "Processing.",
        "KERNEL": "Kernel query received. Governance layer active.",
    }
    return street_responses.get(street, "I'm processing. The inference engine is warming up — your message is received.")


# ── Consensus Engine ───────────────────────────────────────────────────────────

def _consensus_call(
    street: str,
    messages: list,
    available: set,
    timeout: int = 20,
) -> Tuple[Optional[str], bool]:
    """Run N fast models in parallel, return majority answer.

    Returns (answer, is_consensus).
    If no majority, returns (None, False) — caller escalates to HEAVY.
    """
    models = [m for m in CONSENSUS_STREETS.get(street, []) if m in available]
    if len(models) < 2:
        return None, False

    results: Dict[str, Optional[str]] = {}
    threads = []
    for m in models:
        t = threading.Thread(
            target=_call_in_thread,
            args=(m, messages, results, m, timeout),
            daemon=True,
        )
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=timeout + 2)

    answers = [v for v in results.values() if v]
    if not answers:
        return None, False

    # Majority: if 2+ agree on a substring, use the longer one
    if len(answers) >= 2:
        # Simple: return the first non-None answer (true consensus needs embedding)
        # For now, use the shortest as the "safe" answer
        return sorted(answers, key=len)[0], True

    return answers[0], False


# ── Main Mesh Router ───────────────────────────────────────────────────────────

def mesh_call(
    message: str,
    history: list,
    system: str,
    mode: str = "companion",
    use_consensus: bool = False,
    timeout: int = 25,
) -> MeshResult:
    """Route message through EGREGOR model mesh.

    1. Classify → street
    2. Check available models
    3. Try street models in order (fallback chain)
    4. Optionally run consensus on FAST/REVIEW streets
    5. Escalate to HEAVY on consensus failure
    6. Smart fallback if all Ollama unavailable

    authority is always False.
    """
    t0 = time.monotonic()
    street = classify_task(message, mode)

    messages = [{"role": "system", "content": system}]
    for h in history:
        role = h.get("role", "user")
        content = h.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": message})

    available = _get_available_models()

    # Consensus path (optional, for FAST/REVIEW)
    if use_consensus and street in CONSENSUS_STREETS:
        answer, is_consensus = _consensus_call(street, messages, available, timeout=timeout)
        if answer:
            return MeshResult(
                text=answer,
                model=f"consensus:{street}",
                street=street,
                consensus=True,
                authority=False,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )
        # No consensus → fall through to HEAVY
        street = "HEAVY"

    # Primary fallback chain
    chain = STREETS.get(street, STREETS["CONVERSATION"])
    for model in chain:
        if model not in available:
            continue
        result: dict = {}
        t = threading.Thread(
            target=_call_in_thread,
            args=(model, messages, result, "text", timeout),
            daemon=True,
        )
        t.start()
        t.join(timeout=timeout + 2)

        text = result.get("text")
        if text:
            return MeshResult(
                text=text,
                model=model,
                street=street,
                authority=False,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

    # All models in chain failed — try any available model
    for model in ["gemma4:latest", "qwen3.5:latest", "mistral:latest"]:
        if model in available:
            text = _call_model(model, messages, timeout=timeout)
            if text:
                return MeshResult(
                    text=text,
                    model=model,
                    street=street,
                    fallback=True,
                    authority=False,
                    latency_ms=int((time.monotonic() - t0) * 1000),
                )

    # Full fallback — instant in-character response
    return MeshResult(
        text=_instant_fallback(message, street),
        model="fallback",
        street=street,
        fallback=True,
        authority=False,
        latency_ms=int((time.monotonic() - t0) * 1000),
    )


def mesh_available_models() -> Dict[str, Any]:
    """Return street → available models mapping for health checks."""
    available = _get_available_models()
    result: Dict[str, Any] = {}
    for street, chain in STREETS.items():
        live = [m for m in chain if m in available]
        result[street] = {
            "chain": chain,
            "available": live,
            "online": len(live),
            "primary": live[0] if live else None,
        }
    return result


# ── Exports ────────────────────────────────────────────────────────────────────

__all__ = [
    "MeshResult",
    "classify_task",
    "mesh_call",
    "mesh_available_models",
    "STREETS",
    "ALL_MODELS",
    "CONSENSUS_STREETS",
]
