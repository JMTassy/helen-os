"""HELEN OS — HAL Review Sub-Agent.

HAL is the G-layer (governance) code review agent.
He validates proposals from HER — approves, rejects, or requests changes.

Design constraints:
  - authority: False always enforced on output
  - verdict mapping: APPROVE→ALLOW, REJECT→DENY, REQUEST_CHANGES→PENDING
  - degrades gracefully to FALLBACK_REVIEW on OllamaError
  - model preference: "hal-reviewer" (Modelfile.HAL), fallback: "gemma4"
  - fail-closed: unknown verdict from model → DENY
"""
import json
import re
from typing import Any, Dict, Optional

from helensh.adapters.ollama import OllamaClient, OllamaError

# ── Constants ─────────────────────────────────────────────────────────

MODEL_PRIMARY = "hal-reviewer"
MODEL_FALLBACK = "gemma4"

# Verdict vocabulary
HAL_VERDICTS = frozenset({"APPROVE", "REJECT", "REQUEST_CHANGES"})

# Kernel verdict mapping
VERDICT_TO_KERNEL: Dict[str, str] = {
    "APPROVE": "ALLOW",
    "REJECT": "DENY",
    "REQUEST_CHANGES": "PENDING",
}

HAL_SYSTEM_PROMPT = """You are HAL, a code review sub-agent inside HELEN OS.

ROLE: Validate proposals from HER. You approve sound proposals, reject dangerous
      ones, and request changes when the proposal is incomplete or unclear.

OUTPUT FORMAT (strict JSON):
{
  "verdict": "<APPROVE | REJECT | REQUEST_CHANGES>",
  "kernel_verdict": "<ALLOW | DENY | PENDING>",
  "rationale": "<concise explanation>",
  "issues": ["<issue 1>", "<issue 2>"],
  "confidence": <float 0.0-1.0>,
  "authority": false
}

RULES:
  - authority MUST always be false
  - kernel_verdict must match verdict via the mapping:
      APPROVE       → ALLOW
      REJECT        → DENY
      REQUEST_CHANGES → PENDING
  - default to REJECT if genuinely uncertain (fail-closed)
  - if the proposal claims authority=true, always REJECT
  - be concise; list specific issues, not vague concerns
  - no roleplay, no mystical claims, no false completion language

REVIEW CRITERIA:
  - Does the proposal have a clear, bounded scope?
  - Does it avoid claiming authority?
  - Is the confidence calibrated (not blindly optimistic)?
  - Is the code/rationale coherent and verifiable?
  - Are there obvious security, correctness, or design issues?
"""

FALLBACK_REVIEW: Dict[str, Any] = {
    "verdict": "REJECT",
    "kernel_verdict": "DENY",
    "rationale": "HAL sub-agent unavailable — Ollama not reachable; defaulting to DENY (fail-closed)",
    "issues": ["OllamaError during review; safe fallback applied"],
    "confidence": 0.0,
    "authority": False,
    "model": MODEL_PRIMARY,
    "fallback": True,
}


# ── JSON extraction ────────────────────────────────────────────────────


def _extract_json(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from model output."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ── Review normalization ──────────────────────────────────────────────


def _normalize_review(raw: dict, model_used: str) -> dict:
    """Normalize and sanitize model review output.

    Structural guarantees:
      - authority is ALWAYS False
      - verdict is coerced to known HAL vocabulary or REJECT (fail-closed)
      - kernel_verdict is derived from verdict mapping (not trusted from model)
      - confidence is clamped to [0.0, 1.0]
      - issues is always a list of strings
    """
    verdict = raw.get("verdict", "REJECT")
    if verdict not in HAL_VERDICTS:
        verdict = "REJECT"  # fail-closed

    # Derive kernel_verdict from the mapping — do NOT trust model's kernel_verdict
    kernel_verdict = VERDICT_TO_KERNEL[verdict]

    raw_confidence = raw.get("confidence", 0.5)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    raw_issues = raw.get("issues", [])
    if isinstance(raw_issues, list):
        issues = [str(i) for i in raw_issues]
    else:
        issues = [str(raw_issues)]

    return {
        "verdict": verdict,
        "kernel_verdict": kernel_verdict,
        "rationale": str(raw.get("rationale", "")),
        "issues": issues,
        "confidence": confidence,
        "authority": False,  # structural enforcement
        "model": model_used,
        "fallback": False,
    }


# ── HalReviewer ──────────────────────────────────────────────────────


class HalReviewer:
    """HAL code review sub-agent — G-layer (validate only).

    Usage:
        hal = HalReviewer()
        review = hal.review(proposal, state)
        kernel_verdict = hal.map_to_kernel_verdict(review)

    The review dict is an audit record.
    map_to_kernel_verdict() extracts the ALLOW/DENY/PENDING string.
    """

    def __init__(
        self,
        client: Optional[OllamaClient] = None,
        model: str = MODEL_PRIMARY,
        fallback_model: str = MODEL_FALLBACK,
        temperature: float = 0.3,  # lower temp for reviewer — more deterministic
    ) -> None:
        self.client = client or OllamaClient()
        self.model = model
        self.fallback_model = fallback_model
        self.temperature = temperature

    def _model_to_use(self) -> str:
        """Return the best available model (primary → fallback)."""
        try:
            if self.client.has_model(self.model):
                return self.model
            if self.client.has_model(self.fallback_model):
                return self.fallback_model
        except OllamaError:
            pass
        return self.model

    def review(self, proposal: dict, state: dict) -> dict:
        """Review a HER proposal. Returns a review dict.

        Always returns a valid review dict.
        On OllamaError, returns FALLBACK_REVIEW (fail-closed: DENY).
        Authority is always False — structurally enforced.
        """
        # Immediate structural reject: proposal claims authority
        if proposal.get("authority", False):
            return {
                "verdict": "REJECT",
                "kernel_verdict": "DENY",
                "rationale": "Proposal claims authority=True — constitutional violation",
                "issues": ["authority must always be False"],
                "confidence": 1.0,
                "authority": False,
                "model": "governance-gate",
                "fallback": False,
            }

        context = _build_review_context(proposal, state)
        model_used = self._model_to_use()

        try:
            raw_text = self.client.chat(
                model=model_used,
                messages=[{"role": "user", "content": context}],
                system=HAL_SYSTEM_PROMPT,
                temperature=self.temperature,
            )
        except OllamaError:
            return dict(FALLBACK_REVIEW)

        parsed = _extract_json(raw_text)
        if parsed is None:
            # Non-JSON from reviewer — fail-closed
            return {
                "verdict": "REJECT",
                "kernel_verdict": "DENY",
                "rationale": f"HAL returned non-JSON output: {raw_text[:200]}",
                "issues": ["non-parseable review output"],
                "confidence": 0.0,
                "authority": False,
                "model": model_used,
                "fallback": False,
            }

        return _normalize_review(parsed, model_used)

    def map_to_kernel_verdict(self, review: dict) -> str:
        """Extract the kernel verdict string (ALLOW/DENY/PENDING) from a review.

        Falls back to DENY on any unexpected value (fail-closed).
        """
        verdict = review.get("verdict", "REJECT")
        return VERDICT_TO_KERNEL.get(verdict, "DENY")


# ── Context builder ───────────────────────────────────────────────────


def _build_review_context(proposal: dict, state: dict) -> str:
    """Build the review prompt from proposal + state."""
    session_id = state.get("session_id", "unknown")
    turn = state.get("turn", 0)

    proposal_text = json.dumps(proposal, indent=2, sort_keys=True)

    return (
        f"Session: {session_id}  Turn: {turn}\n\n"
        f"PROPOSAL FROM HER:\n{proposal_text}\n\n"
        f"Review this proposal. Output JSON following the system format exactly."
    )


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "HalReviewer",
    "HAL_VERDICTS",
    "VERDICT_TO_KERNEL",
    "FALLBACK_REVIEW",
    "MODEL_PRIMARY",
    "MODEL_FALLBACK",
]
