"""HELENSH Kernel — Deterministic Receipted Transition System.

Core invariants:
  I1  Determinism:           step(S, u) == step(S, u)
  I2  NoSilentEffect:        verdict != ALLOW => effect_footprint(S') == effect_footprint(S)
  I3  ReceiptCompleteness:   every step() appends exactly 2 receipts (proposal + execution)
  I4  ChainIntegrity:        previous_hash links unbroken from genesis
  I5  ByteStableReplay:      same inputs => same receipt hashes
  I6  AuthorityFalse:        every receipt has authority == False
  I7  GovernorGates:         capability revoke => DENY; write/CLAW => PENDING
  I8  StructuralAuthGuard:   authority=True proposals never mutate state
  I9  ReplayVerification:    rebuild_and_verify passes on valid chains
  I10 DenyPath:              revoked cap => DENY => chained => no env effect

Receipt law:
  1. No receipt = no reality.
  2. Every Proposal produces a Receipt.
  3. Every allowed effect produces an Execution Receipt.
  4. Materialized does not imply successful.
  5. Authority is false by default.

Architecture: F = E ∘ G ∘ C
  C: cognition (untrusted parser, total, deterministic)
  G: governor (closed verdict vocabulary, fail-closed)
  E: execution (sole state-mutating layer, only on ALLOW)
"""
import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical, canonical_hash, effect_footprint, governed_state_hash

# ── Constants ─────────────────────────────────────────────────────────

SCHEMA_VERSION = "HELENSH_RECEIPT_V1"

RECEIPT_TYPE_PROPOSAL = "PROPOSAL"
RECEIPT_TYPE_EXECUTION = "EXECUTION"

KNOWN_ACTIONS = frozenset({
    "chat",
    "read_file",
    "write_file",
    "run_command",
    "list_files",
    "search",
    "memory_read",
    "memory_write",
    "claw_external",   # CLAW skills agent — always PENDING (requires approval)
    "witness",         # TEMPLE/EVOLVE session observation — ALLOW (not a write)
    "task_create",     # project continuity — create a tracked task
    "task_update",     # project continuity — update task status
    "url_fetch",       # URL fetch — default DENIED (no capability until granted)
})

WRITE_ACTIONS = frozenset({
    "write_file",
    "run_command",
    "claw_external",   # external connections require explicit approval
})

# Actions that are DENIED by default (capability must be explicitly granted)
GATED_ACTIONS = frozenset({
    "url_fetch",       # no local fetch tool → DENY; requires claw or explicit grant
})

GENESIS_HASH = "genesis"

# URL detection pattern — used by cognition to gate URL-containing messages
_URL_PATTERN = re.compile(r'https?://\S+', re.IGNORECASE)

# ── Default capabilities ──────────────────────────────────────────────

DEFAULT_CAPABILITIES = {action: True for action in KNOWN_ACTIONS}
# Gated actions: recognized but DENIED until explicitly granted
for _a in GATED_ACTIONS:
    DEFAULT_CAPABILITIES[_a] = False

# ── Init ──────────────────────────────────────────────────────────────


def init_session(
    session_id: str = "S-default",
    user: str = "user",
    root: str = "/",
) -> dict:
    """Create a fresh session state. This is S₀."""
    return {
        "session_id": session_id,
        "user": user,
        "root": root,
        "turn": 0,
        "env": {},
        "capabilities": dict(DEFAULT_CAPABILITIES),
        "history": [],
        "working_memory": {},
        "receipts": [],
    }


# ── C: Cognition (total, deterministic parser) ───────────────────────


def cognition(state: dict, user_input: str) -> dict:
    """Parse user input into a proposal. Always returns a valid proposal dict.

    The cognition layer is untrusted but total — it cannot fail or produce
    partial output. It classifies input into an action + payload.
    """
    text = user_input.strip().lower() if user_input else ""

    # Classify by prefix
    if text.startswith("#read ") or text.startswith("read "):
        action = "read_file"
        payload = {"path": user_input.strip().split(None, 1)[-1] if " " in user_input.strip() else ""}
    elif text.startswith("#write ") or text.startswith("write "):
        action = "write_file"
        payload = {"content": user_input.strip().split(None, 1)[-1] if " " in user_input.strip() else ""}
    elif text.startswith("#run ") or text.startswith("run "):
        action = "run_command"
        payload = {"command": user_input.strip().split(None, 1)[-1] if " " in user_input.strip() else ""}
    elif text.startswith("#ls") or text.startswith("ls"):
        action = "list_files"
        payload = {"path": user_input.strip().split(None, 1)[-1] if " " in user_input.strip() else "."}
    elif text.startswith("#search ") or text.startswith("search "):
        action = "search"
        payload = {"query": user_input.strip().split(None, 1)[-1] if " " in user_input.strip() else ""}
    elif text.startswith("#remember "):
        action = "memory_write"
        payload = {"content": user_input.strip().split(None, 1)[-1] if " " in user_input.strip() else ""}
    elif text.startswith("#recall"):
        action = "memory_read"
        payload = {}
    elif text.startswith("#witness "):
        action = "witness"
        payload = {"content": user_input.strip().split(None, 1)[-1] if " " in user_input.strip() else ""}
    elif text.startswith("#task-update "):
        parts = user_input.strip().split(None, 2)
        if len(parts) >= 3:
            action = "task_update"
            payload = {"task_id": parts[1], "status": parts[2]}
        else:
            action = "chat"
            payload = {"message": user_input}
    elif text.startswith("#task "):
        action = "task_create"
        goal = user_input.strip().split(None, 1)[-1] if " " in user_input.strip() else ""
        payload = {"task_id": f"T-{state['turn']}", "goal": goal}
    else:
        action = "chat"
        payload = {"message": user_input}

    # ── URL gate ──
    # If the message contains a URL and would be routed to "chat":
    #   - URL-only message → url_fetch action (governor DENIEs: no capability)
    #   - URL embedded in text → rewrite payload for safe plain-text analysis
    # This prevents raw URLs from being sent to local fallback reasoning.
    if action == "chat" and user_input and _URL_PATTERN.search(user_input):
        urls = _URL_PATTERN.findall(user_input)
        text_without_urls = _URL_PATTERN.sub("", user_input).strip()
        if len(text_without_urls) < 10:
            # Message is essentially just a URL → route to url_fetch
            action = "url_fetch"
            payload = {"url": urls[0], "original": user_input}
        else:
            # URL embedded in larger text → rewrite as plain-text analysis
            payload["url_detected"] = True
            payload["urls"] = urls
            payload["message"] = (
                f"[URL detected — no fetch tool. Analyze as text only.] "
                f"{user_input}"
            )

    return {
        "action": action,
        "payload": payload,
        "authority": False,
    }


# ── G: Governor (deterministic, fail-closed, 5-gate chain) ───────────


def governor(proposal: dict, state: dict) -> str:
    """Evaluate proposal against state. Returns verdict in {ALLOW, DENY, PENDING}.

    Gate chain (first match wins, fail-closed):
      1. Unknown action        → DENY
      2. Authority claim        → DENY
      3. Missing capability     → DENY
      4. Write action           → PENDING
      5. Otherwise              → ALLOW
    """
    action = proposal.get("action", "")

    # Gate 1: unknown action
    if action not in KNOWN_ACTIONS:
        return "DENY"

    # Gate 2: authority claim (constitutional — non-sovereign system)
    if proposal.get("authority", False):
        return "DENY"

    # Gate 3: capability check
    if not state.get("capabilities", {}).get(action, False):
        return "DENY"

    # Gate 4: write actions require confirmation (PENDING)
    if action in WRITE_ACTIONS:
        return "PENDING"

    # Gate 5: default ALLOW
    return "ALLOW"


# ── R: Receipt construction (hash-chained, deterministic) ─────────────


def _receipt_hash_payload(
    schema: str,
    receipt_type: str,
    turn: int,
    user_input: str,
    proposal: dict,
    verdict: str,
    authority: bool,
    state_hash_before: str,
    previous_hash: str,
    **extra: Any,
) -> str:
    """Compute deterministic receipt hash from all load-bearing fields."""
    payload = {
        "schema": schema,
        "type": receipt_type,
        "turn": turn,
        "user_input": user_input,
        "proposal": proposal,
        "verdict": verdict,
        "authority": authority,
        "state_hash_before": state_hash_before,
        "previous_hash": previous_hash,
    }
    payload.update(extra)
    return canonical_hash(payload)


def make_proposal_receipt(
    proposal: dict,
    verdict: str,
    state: dict,
    user_input: str,
    previous_hash: str,
) -> dict:
    """Create a PROPOSAL receipt. This is the governance record."""
    state_hash = governed_state_hash(state)
    receipt_hash = _receipt_hash_payload(
        schema=SCHEMA_VERSION,
        receipt_type=RECEIPT_TYPE_PROPOSAL,
        turn=state["turn"],
        user_input=user_input,
        proposal=proposal,
        verdict=verdict,
        authority=False,
        state_hash_before=state_hash,
        previous_hash=previous_hash,
    )
    return {
        "schema": SCHEMA_VERSION,
        "type": RECEIPT_TYPE_PROPOSAL,
        "turn": state["turn"],
        "user_input": user_input,
        "proposal": proposal,
        "verdict": verdict,
        "authority": False,
        "state_hash_before": state_hash,
        "previous_hash": previous_hash,
        "hash": receipt_hash,
    }


def make_execution_receipt(
    proposal: dict,
    verdict: str,
    state_before: dict,
    state_after: dict,
    previous_hash: str,
    effect_status: str,
) -> dict:
    """Create an EXECUTION receipt. Records the mutation (or non-mutation)."""
    hash_before = governed_state_hash(state_before)
    hash_after = governed_state_hash(state_after)
    receipt_hash = _receipt_hash_payload(
        schema=SCHEMA_VERSION,
        receipt_type=RECEIPT_TYPE_EXECUTION,
        turn=state_before["turn"],
        user_input="",
        proposal=proposal,
        verdict=verdict,
        authority=False,
        state_hash_before=hash_before,
        previous_hash=previous_hash,
        state_hash_after=hash_after,
        effect_status=effect_status,
        intent_status="UNKNOWN",
    )
    return {
        "schema": SCHEMA_VERSION,
        "type": RECEIPT_TYPE_EXECUTION,
        "turn": state_before["turn"],
        "proposal": proposal,
        "verdict": verdict,
        "authority": False,
        "state_hash_before": hash_before,
        "state_hash_after": hash_after,
        "previous_hash": previous_hash,
        "hash": receipt_hash,
        "effect_status": effect_status,
        "intent_status": "UNKNOWN",
    }


# ── E: Execution (sole state-mutating layer) ─────────────────────────


def apply_receipt(state: dict, proposal: dict, verdict: str) -> dict:
    """Apply a receipted proposal to state. Only mutates on ALLOW.

    Structural authority guard: rejects any proposal with authority=True
    even if somehow the governor allowed it.
    """
    # Constitutional guard — non-sovereign system
    if proposal.get("authority", False):
        return state

    if verdict == "ALLOW":
        action = proposal["action"]

        if action == "chat":
            state["working_memory"]["last_message"] = proposal["payload"].get("message", "")

        elif action == "memory_write":
            content = proposal["payload"].get("content", "")
            key = f"mem_{state['turn']}"
            state["working_memory"][key] = content

        elif action == "memory_read":
            pass  # read-only, no mutation

        elif action == "read_file":
            state["env"][f"read:{proposal['payload'].get('path', '')}"] = True

        elif action == "list_files":
            state["env"][f"ls:{proposal['payload'].get('path', '.')}"] = True

        elif action == "search":
            state["env"][f"search:{proposal['payload'].get('query', '')}"] = True

        elif action == "witness":
            content = proposal["payload"].get("content", "")
            key = f"witness_{state['turn']}"
            state["working_memory"][key] = content

        elif action == "task_create":
            task_id = proposal["payload"].get("task_id", "")
            goal = proposal["payload"].get("goal", "")
            key = f"task_{state['turn']}"
            state["working_memory"][key] = canonical(
                {"task_id": task_id, "goal": goal, "status": "OPEN"}
            )

        elif action == "task_update":
            task_id = proposal["payload"].get("task_id", "")
            status = proposal["payload"].get("status", "")
            key = f"task_update_{state['turn']}"
            state["working_memory"][key] = canonical(
                {"task_id": task_id, "status": status}
            )

        # write_file and run_command go through PENDING, not ALLOW directly

    # DENY and PENDING: no state mutation (NoSilentEffect invariant)
    return state


# ── F = E ∘ G ∘ C: The step function ─────────────────────────────────


def step(state: dict, user_input: str) -> Tuple[dict, dict]:
    """Execute one complete kernel step.

    Returns (new_state, proposal_receipt).

    Chain structure per step:
      previous → proposal_receipt → execution_receipt
    Two receipts appended to state.receipts per step.
    """
    # Deep copy to prevent aliasing
    s = copy.deepcopy(state)

    # Determine chain link
    if s["receipts"]:
        previous_hash = s["receipts"][-1]["hash"]
    else:
        previous_hash = GENESIS_HASH

    # ── C: Cognition ──
    proposal = cognition(s, user_input)

    # ── G: Governor ──
    verdict = governor(proposal, s)

    # ── R1: Proposal receipt ──
    p_receipt = make_proposal_receipt(proposal, verdict, s, user_input, previous_hash)

    # Snapshot state before execution
    s_before_exec = copy.deepcopy(s)

    # ── E: Execution ──
    s = apply_receipt(s, proposal, verdict)

    # Determine effect status
    if verdict == "ALLOW":
        effect_status = "APPLIED"
    elif verdict == "DENY":
        effect_status = "DENIED"
    else:
        effect_status = "DEFERRED"

    # ── R2: Execution receipt ──
    e_receipt = make_execution_receipt(
        proposal, verdict, s_before_exec, s, p_receipt["hash"], effect_status,
    )

    # Append history
    s["history"].append({"input": user_input, "verdict": verdict, "action": proposal["action"]})

    # Append both receipts
    s["receipts"].append(p_receipt)
    s["receipts"].append(e_receipt)

    # Increment turn
    s["turn"] += 1

    return s, p_receipt


# ── Replay ────────────────────────────────────────────────────────────


def replay(initial_state: dict, inputs: List[str]) -> dict:
    """Replay a sequence of inputs from initial state. Deterministic fold."""
    s = copy.deepcopy(initial_state)
    for u in inputs:
        s, _ = step(s, u)
    return s


# ── Capability management ────────────────────────────────────────────


def grant_capability(state: dict, action: str) -> dict:
    """Grant a capability. Returns new state."""
    s = copy.deepcopy(state)
    if action in KNOWN_ACTIONS:
        s["capabilities"][action] = True
    return s


def revoke_capability(state: dict, action: str) -> dict:
    """Revoke a capability. Returns new state."""
    s = copy.deepcopy(state)
    if action in s["capabilities"]:
        s["capabilities"][action] = False
    return s


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "SCHEMA_VERSION",
    "RECEIPT_TYPE_PROPOSAL",
    "RECEIPT_TYPE_EXECUTION",
    "KNOWN_ACTIONS",
    "WRITE_ACTIONS",
    "GATED_ACTIONS",
    "GENESIS_HASH",
    "DEFAULT_CAPABILITIES",
    "init_session",
    "cognition",
    "governor",
    "make_proposal_receipt",
    "make_execution_receipt",
    "apply_receipt",
    "step",
    "replay",
    "grant_capability",
    "revoke_capability",
]
