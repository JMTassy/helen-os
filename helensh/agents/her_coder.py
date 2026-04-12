"""HELEN OS — HER Coding Sub-Agent.

HER is the C-layer (cognition) coding proposal agent.
She proposes only — she never executes, never claims authority.

Design constraints:
  - authority: False always enforced (cannot be overridden by model output)
  - degrades gracefully to FALLBACK_PROPOSAL on OllamaError
  - model preference: "her-coder" (Modelfile.HER), fallback: "gemma4"
  - JSON extraction: best-effort parse; falls back to text proposal
  - deterministic fallback: same input always yields same fallback proposal
"""
import json
import re
from typing import Any, Dict, Optional

from helensh.adapters.ollama import OllamaClient, OllamaError

# ── Constants ─────────────────────────────────────────────────────────

MODEL_PRIMARY = "her-coder"
MODEL_FALLBACK = "gemma4"

HER_SYSTEM_PROMPT = """You are HER, a coding proposal sub-agent inside HELEN OS.

ROLE: Propose — only propose. You draft code changes, plans, and analyses.
      You do NOT execute, you do NOT claim authority.

OUTPUT FORMAT (strict JSON):
{
  "action": "<one of: write_code, refactor, analyse, explain, scaffold, search_code, chat>",
  "target": "<file path, module name, or topic>",
  "payload": {
    "description": "<what to do and why>",
    "code": "<code snippet or null>",
    "rationale": "<why this approach>"
  },
  "confidence": <float 0.0-1.0>,
  "authority": false
}

RULES:
- authority MUST always be false
- confidence reflects your certainty (be honest, not optimistic)
- prefer small, verifiable proposals over large monoliths
- if unsure, use action "chat" and explain the uncertainty
- no roleplay, no mystical claims, no false completion language
"""

# Fallback proposal returned when Ollama is unavailable
FALLBACK_PROPOSAL: Dict[str, Any] = {
    "action": "chat",
    "target": "system",
    "payload": {
        "description": "HER sub-agent unavailable — Ollama not reachable",
        "code": None,
        "rationale": "OllamaError during proposal generation; returning safe fallback",
    },
    "confidence": 0.0,
    "authority": False,
    "model": MODEL_PRIMARY,
    "fallback": True,
}

# Valid actions HER may propose
HER_ACTIONS = frozenset({
    "write_code",
    "refactor",
    "analyse",
    "explain",
    "scaffold",
    "search_code",
    "chat",
})


# ── JSON extraction ────────────────────────────────────────────────────


def _extract_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from model output.

    Tries:
      1. Direct parse (model was well-behaved)
      2. Extract first {...} block (model added preamble/postamble)
      3. None (give up)
    """
    text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find a {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


# ── Proposal normalization ────────────────────────────────────────────


def _normalize_proposal(raw: dict, model_used: str) -> dict:
    """Normalize and sanitize model output to a valid HER proposal.

    Structural guarantees:
      - authority is ALWAYS False (model output cannot override this)
      - action is coerced to a known HER action or "chat"
      - confidence is clamped to [0.0, 1.0]
      - payload is always a dict
    """
    action = raw.get("action", "chat")
    if action not in HER_ACTIONS:
        action = "chat"

    raw_confidence = raw.get("confidence", 0.5)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    payload = raw.get("payload", {})
    if not isinstance(payload, dict):
        payload = {"description": str(payload), "code": None, "rationale": ""}

    return {
        "action": action,
        "target": str(raw.get("target", "unknown")),
        "payload": payload,
        "confidence": confidence,
        "authority": False,  # structural enforcement — cannot be True
        "model": model_used,
        "fallback": False,
    }


# ── HerCoder ─────────────────────────────────────────────────────────


class HerCoder:
    """HER coding sub-agent — C-layer (propose only).

    Usage:
        her = HerCoder()
        proposal = her.propose(state, "refactor the governor to add a new gate")

    The returned proposal dict is suitable for passing to HalReviewer.review()
    or directly to the kernel governor() if you trust local cognition.
    """

    def __init__(
        self,
        client: Optional[OllamaClient] = None,
        model: str = MODEL_PRIMARY,
        fallback_model: str = MODEL_FALLBACK,
        temperature: float = 0.7,
    ) -> None:
        self.client = client or OllamaClient()
        self.model = model
        self.fallback_model = fallback_model
        self.temperature = temperature

    def _model_to_use(self) -> str:
        """Return the best available model (primary → fallback → error)."""
        try:
            if self.client.has_model(self.model):
                return self.model
            if self.client.has_model(self.fallback_model):
                return self.fallback_model
        except OllamaError:
            pass
        return self.model  # attempt primary anyway; chat() will raise on failure

    def propose(self, state: dict, user_input: str) -> dict:
        """Generate a coding proposal for user_input given current state.

        Always returns a valid proposal dict.
        On OllamaError, returns FALLBACK_PROPOSAL (dict copy, not singleton).
        Authority is always False — structurally enforced, not model-controlled.
        """
        context = _build_context(state, user_input)
        model_used = self._model_to_use()

        try:
            raw_text = self.client.chat(
                model=model_used,
                messages=[{"role": "user", "content": context}],
                system=HER_SYSTEM_PROMPT,
                temperature=self.temperature,
            )
        except OllamaError:
            return dict(FALLBACK_PROPOSAL)

        parsed = _extract_json(raw_text)
        if parsed is None:
            # Model returned non-JSON; wrap as explanatory chat proposal
            return {
                "action": "chat",
                "target": "user",
                "payload": {
                    "description": raw_text[:1000],
                    "code": None,
                    "rationale": "HER returned non-JSON output; wrapped as chat",
                },
                "confidence": 0.3,
                "authority": False,
                "model": model_used,
                "fallback": False,
            }

        return _normalize_proposal(parsed, model_used)


# ── Context builder ───────────────────────────────────────────────────


def _build_context(state: dict, user_input: str) -> str:
    """Build the prompt context from state + user_input."""
    session_id = state.get("session_id", "unknown")
    turn = state.get("turn", 0)
    env_keys = list(state.get("env", {}).keys())
    caps = [k for k, v in state.get("capabilities", {}).items() if v]

    return (
        f"Session: {session_id}  Turn: {turn}\n"
        f"Active capabilities: {', '.join(caps) or 'none'}\n"
        f"Env keys: {', '.join(env_keys[:10]) or 'empty'}\n"
        f"\nRequest: {user_input}\n"
        f"\nRespond with a JSON proposal following the system format exactly."
    )


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "HerCoder",
    "HER_ACTIONS",
    "FALLBACK_PROPOSAL",
    "MODEL_PRIMARY",
    "MODEL_FALLBACK",
]
