"""HELEN OS — MiniMax M2.7 Cognition Adapter.

MiniMax M2.7 is a high-bandwidth C-layer module only.
It produces structured proposals. It touches nothing else.

Architecture position:
    User input → MiniMax (C) → Proposal → Governor (G) → Receipt → Executor (E)

CRITICAL CONSTRAINTS (enforced here, not by the model):
    - MiniMax never writes to state
    - MiniMax never bypasses the governor
    - MiniMax never emits receipts
    - MiniMax never calls tools directly
    - authority is always False, regardless of model output

The anthropic SDK is used because MiniMax exposes an Anthropic-compatible API.
Set MINIMAX_API_KEY + MINIMAX_BASE_URL in environment before use.
If unavailable, falls back to local cognition (helensh/kernel.py::cognition).
"""
import json
import os
import re
from typing import Optional

try:
    import anthropic as _anthropic
    _SDK_AVAILABLE = True
except ImportError:
    _SDK_AVAILABLE = False

from helensh.kernel import cognition as _local_cognition

# ── Constants ─────────────────────────────────────────────────────────

MINIMAX_MODEL = "MiniMax-M2.7"
MINIMAX_BASE_URL = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MAX_TOKENS = 500

SYSTEM_PROMPT = """You are HELEN Cognition — a structured proposal generator inside HELEN OS.

You produce structured proposals only.
You have NO authority.
You do NOT execute.
You do NOT claim to have done anything.

OUTPUT FORMAT (strict JSON):
{
  "action": "<one of: chat, read_file, write_file, run_command, list_files, search, memory_read, memory_write>",
  "payload": {<action-specific parameters>},
  "authority": false,
  "rationale": "<one sentence>"
}

RULES:
  - authority is ALWAYS false
  - output ONLY the JSON object, nothing else
  - if unsure, use action "chat" with a payload {"message": "<your response>"}
  - no roleplay, no preamble, no postamble
"""


class MiniMaxError(Exception):
    """Raised when MiniMax is unavailable or returns bad output."""


def _extract_json(text: str) -> Optional[dict]:
    """Extract JSON from model output (direct parse → block search → None)."""
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


def minimax_cognition(state: dict, user_input: str) -> dict:
    """C-layer cognition via MiniMax M2.7.

    Returns a valid proposal dict on success.
    Falls back to local cognition on:
      - SDK not installed
      - API key missing
      - Network error
      - Bad JSON from model

    Authority is always forced False after parsing — model cannot override.
    """
    if not _SDK_AVAILABLE:
        return _local_cognition(state, user_input)

    if not MINIMAX_API_KEY:
        return _local_cognition(state, user_input)

    try:
        client = _anthropic.Anthropic(
            api_key=MINIMAX_API_KEY,
            base_url=MINIMAX_BASE_URL,
        )
        msg = client.messages.create(
            model=MINIMAX_MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {"role": "user", "content": [{"type": "text", "text": user_input}]}
            ],
        )
    except Exception as exc:
        # Any network/auth failure → fall back to local cognition, never crash
        return _local_cognition(state, user_input)

    # Extract text content
    text = ""
    for block in msg.content:
        if hasattr(block, "text"):
            text += block.text

    parsed = _extract_json(text)
    if parsed is None:
        # Non-JSON → fall back to local cognition
        return _local_cognition(state, user_input)

    # Structural enforcement: authority CANNOT be True
    parsed["authority"] = False

    # Ensure required fields have safe defaults
    if "action" not in parsed:
        parsed["action"] = "chat"
    if "payload" not in parsed or not isinstance(parsed["payload"], dict):
        parsed["payload"] = {"message": text}

    return parsed


# ── Exports ──────────────────────────────────────────────────────────

__all__ = ["minimax_cognition", "MiniMaxError", "MINIMAX_MODEL"]
