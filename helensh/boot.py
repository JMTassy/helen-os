"""HELEN OS — Boot sequence.

This module handles the Pull OS boot:
    1. Hydrate state from ledger (or genesis)
    2. Detect available sub-agents (Ollama models)
    3. Route intent through kernel
    4. Emit boot receipt

Usage:
    from helensh.boot import boot_helen, route_intent

    session = boot_helen()
    result = route_intent(session, "brainstorm a new governor gate")
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional, Tuple

from helensh.kernel import init_session, step, KNOWN_ACTIONS
from helensh.ledger import LedgerWriter, LedgerReader, hydrate_session, persisted_step
from helensh.state import governed_state_hash
from helensh.adapters.ollama import OllamaClient, OllamaError

# ── Paths ─────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
STATE_DIR = ROOT / "helensh" / ".state"
LEDGER_PATH = STATE_DIR / "boot_ledger.jsonl"

# ── Boot modes ────────────────────────────────────────────────────────

MODE_CONCIERGE = "concierge"
MODE_TEMPLE = "temple"
MODE_EVOLVE = "evolve"
MODE_WITNESS = "witness"
MODE_ORACLE = "oracle"

VALID_MODES = frozenset({MODE_CONCIERGE, MODE_TEMPLE, MODE_EVOLVE, MODE_WITNESS, MODE_ORACLE})


# ── Sub-agent detection ───────────────────────────────────────────────


def detect_agents() -> dict:
    """Detect which sub-agents are available (Ollama models)."""
    agents = {
        "her-coder": False,
        "hal-reviewer": False,
        "claw-agent": False,
        "gemma4": False,
        "ollama": False,
    }

    try:
        client = OllamaClient()
        if client.is_available():
            agents["ollama"] = True
            models = client.list_models()
            for name in agents:
                if name != "ollama":
                    agents[name] = name in models
    except Exception:
        pass

    return agents


# ── Intent classification ─────────────────────────────────────────────


def classify_intent(user_input: str) -> Tuple[str, str]:
    """Classify user intent into (mode, action_hint).

    Returns the best mode + a routing hint for the kernel.
    """
    text = user_input.strip().lower()

    if any(kw in text for kw in ("brainstorm", "temple", "sandbox", "ideas", "infinite loop")):
        return MODE_TEMPLE, "brainstorm"

    if any(kw in text for kw in ("evolve", "improve", "refine", "iterate", "self-improve")):
        return MODE_EVOLVE, "evolve"

    if any(kw in text for kw in ("status", "history", "what happened", "show receipts", "verify", "prove")):
        return MODE_WITNESS, "verify"

    if any(kw in text for kw in ("recall", "pattern", "insight", "learned", "oracle")):
        return MODE_ORACLE, "insight"

    if any(kw in text for kw in ("send", "telegram", "fetch", "notify", "ping", "download")):
        return MODE_CONCIERGE, "claw"

    if any(kw in text for kw in ("click", "navigate", "screenshot", "open app")):
        return MODE_CONCIERGE, "computer_use"

    if any(kw in text for kw in ("generate image", "create image", "draw", "render")):
        return MODE_CONCIERGE, "image_gen"

    return MODE_CONCIERGE, "general"


# ── Boot ──────────────────────────────────────────────────────────────


class HelenSession:
    """Live HELEN OS session — the concierge runtime."""

    def __init__(
        self,
        state: dict,
        ledger_path: Path = LEDGER_PATH,
        hydrated: bool = False,
        receipt_count: int = 0,
        agents: Optional[dict] = None,
        mode: str = MODE_CONCIERGE,
    ):
        self.state = state
        self.ledger_path = ledger_path
        self.writer = LedgerWriter(str(ledger_path))
        self.hydrated = hydrated
        self.receipt_count = receipt_count
        self.agents = agents or {}
        self.mode = mode

    def step(self, user_input: str) -> Tuple[dict, dict]:
        """Execute one governed step. Returns (new_state, proposal_receipt)."""
        self.state, p_receipt = persisted_step(self.state, user_input, self.writer)
        self.receipt_count += 2
        return self.state, p_receipt

    @property
    def state_hash(self) -> str:
        return governed_state_hash(self.state)

    @property
    def turn(self) -> int:
        return self.state.get("turn", 0)


def boot_helen(
    session_id: str = "helen-os",
    ledger_path: Optional[Path] = None,
) -> HelenSession:
    """Boot HELEN OS. Hydrate from ledger or start from genesis.

    Returns a HelenSession ready for intent routing.
    """
    ledger = ledger_path or LEDGER_PATH

    # Ensure state dir exists
    ledger.parent.mkdir(parents=True, exist_ok=True)

    # Initial state
    s0 = init_session(session_id=session_id, user="jmt", root=str(ROOT))

    # Attempt hydration
    state, ok, errors = hydrate_session(s0, str(ledger))
    receipt_count = len(LedgerReader(str(ledger)).all()) if ok else 0

    # Detect agents
    agents = detect_agents()

    return HelenSession(
        state=state,
        ledger_path=ledger,
        hydrated=ok and receipt_count > 0,
        receipt_count=receipt_count,
        agents=agents,
        mode=MODE_CONCIERGE,
    )


def boot_banner(session: HelenSession) -> str:
    """Generate the boot banner for terminal display."""
    lines = []
    lines.append("HELEN OS v0.3-alpha online.")

    if session.hydrated:
        lines.append(f"State hydrated from ledger: {session.receipt_count} receipts verified.")
        lines.append("Chain integrity: PASS")
    else:
        lines.append("Genesis state. No prior ledger found.")

    lines.append("Authority: false")
    lines.append(f"Mode: {session.mode}")

    # Sub-agents
    agent_status = []
    for name, available in session.agents.items():
        if name == "ollama":
            continue
        status = "✓" if available else "·"
        agent_status.append(f"{status} {name}")
    if agent_status:
        lines.append(f"Sub-agents: {' | '.join(agent_status)}")

    lines.append(f"State hash: {session.state_hash[:16]}...")
    lines.append("")
    lines.append("Law: No receipt = no reality.")
    lines.append("Ready.")

    return "\n".join(lines)


# ── Module exports ────────────────────────────────────────────────────

__all__ = [
    "boot_helen",
    "boot_banner",
    "classify_intent",
    "detect_agents",
    "HelenSession",
    "MODE_CONCIERGE",
    "MODE_TEMPLE",
    "MODE_EVOLVE",
    "MODE_WITNESS",
    "MODE_ORACLE",
]
