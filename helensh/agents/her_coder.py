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

# Sub-agent models — HER dispatches to specialists
MODEL_CODEX = "her-codex-gemma"          # code generation specialist
MODEL_CLAUDECODE = "her-claudecode-gemma" # reasoning & analysis specialist

# Actions routed to CODEX (code generation)
CODEX_ACTIONS = frozenset({"write_code", "refactor", "scaffold", "search_code"})

# Actions routed to CLAUDECODE (reasoning/analysis)
CLAUDECODE_ACTIONS = frozenset({"analyse", "explain", "chat"})

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

    HER dispatches to two specialists:
      - HER-CODEX:      code generation (write, refactor, scaffold)
      - HER-CLAUDECODE:  reasoning & analysis (analyse, explain, plan)

    If sub-agents are unavailable, falls back to monolithic her-coder.

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
        self._sub_agents: Optional[Dict[str, bool]] = None

    def _detect_sub_agents(self) -> Dict[str, bool]:
        """Detect which sub-agent models are available."""
        if self._sub_agents is not None:
            return self._sub_agents
        self._sub_agents = {
            "codex": False,
            "claudecode": False,
        }
        try:
            if self.client.is_available():
                self._sub_agents["codex"] = self.client.has_model(MODEL_CODEX)
                self._sub_agents["claudecode"] = self.client.has_model(MODEL_CLAUDECODE)
        except OllamaError:
            pass
        return self._sub_agents

    @property
    def has_sub_agents(self) -> bool:
        """True if at least one sub-agent is available."""
        agents = self._detect_sub_agents()
        return agents.get("codex", False) or agents.get("claudecode", False)

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

    def _route_to_sub_agent(self, user_input: str) -> Optional[str]:
        """Route input to the best sub-agent model, or None for monolithic.

        Routing heuristic:
          - Code-related keywords → CODEX
          - Analysis/reasoning keywords → CLAUDECODE
          - Mixed/ambiguous → monolithic her-coder
        """
        agents = self._detect_sub_agents()
        text = user_input.strip().lower()

        # Code signals
        code_signals = ("write", "implement", "refactor", "scaffold", "code", "function",
                        "class ", "def ", "test", "fix", "bug", "import", "module")
        # Reasoning signals
        reason_signals = ("analyse", "analyze", "explain", "why", "design", "plan",
                          "research", "compare", "risk", "architecture", "proof",
                          "formalize", "theorem", "converge")

        code_score = sum(1 for kw in code_signals if kw in text)
        reason_score = sum(1 for kw in reason_signals if kw in text)

        if code_score > reason_score and agents.get("codex"):
            return MODEL_CODEX
        if reason_score > code_score and agents.get("claudecode"):
            return MODEL_CLAUDECODE
        if agents.get("codex") and code_score > 0:
            return MODEL_CODEX
        if agents.get("claudecode") and reason_score > 0:
            return MODEL_CLAUDECODE

        return None  # fall back to monolithic

    def propose(self, state: dict, user_input: str) -> dict:
        """Generate a coding proposal for user_input given current state.

        Routing: tries sub-agents first (CODEX/CLAUDECODE), falls back to monolithic.
        Always returns a valid proposal dict.
        On OllamaError, returns FALLBACK_PROPOSAL (dict copy, not singleton).
        Authority is always False — structurally enforced, not model-controlled.
        """
        context = _build_context(state, user_input)

        # Try sub-agent routing first
        sub_model = self._route_to_sub_agent(user_input)
        model_used = sub_model or self._model_to_use()

        try:
            raw_text = self.client.chat(
                model=model_used,
                messages=[{"role": "user", "content": context}],
                system=HER_SYSTEM_PROMPT,
                temperature=self.temperature,
            )
        except OllamaError:
            # If sub-agent failed, retry with monolithic
            if sub_model:
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
            else:
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
