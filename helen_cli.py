#!/usr/bin/env python3
"""helen_cli.py — HELEN terminal interface.

Architecture:
  HER = propose only  (cognition layer — total, deterministic parser)
  HAL = validate      (governor layer — fail-closed policy gate)
  E   = execute       (sole state-mutation path — only on ALLOW)
  R   = receipt       (hash-chained audit log — proposal + execution per step)

Guarantees:
  • No fake memory: continuity comes only from explicit files
    (session_resume.json, runtime_state.json, live_ledger.jsonl)
  • No constitutional theatre: no "I acknowledge…", no "As HER…"
  • No implicit state: all context loaded explicitly at boot
  • All authority == False on every receipt (non-sovereign)
  • Chain integrity verifiable at any time via verify_chain()

Files created automatically under helensh/.state/:
  runtime_state.json    — current governed state (env, topic, turn)
  session_resume.json   — explicit resume packet for next session
  live_ledger.jsonl     — append-only NDJSON receipt ledger
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Literal


# ============================================================
# Paths
# ============================================================

ROOT      = Path(".")
STATE_DIR = ROOT / "helensh" / ".state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

LEDGER_PATH        = STATE_DIR / "live_ledger.jsonl"
SESSION_RESUME_PATH = STATE_DIR / "session_resume.json"
STATE_PATH         = STATE_DIR / "runtime_state.json"


# ============================================================
# Canonical / Hash
# ============================================================

def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: Any) -> str:
    if isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = canonical_json(data).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# ============================================================
# Types
# ============================================================

Verdict     = Literal["ALLOW", "DENY", "PENDING"]
ReceiptType = Literal["PROPOSAL", "EXECUTION"]


@dataclass(slots=True)
class Proposal:
    proposal_id:   str
    from_role:     str
    intent:        str
    target:        str
    payload:       dict[str, Any]
    confidence:    float
    created_at_ns: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PolicyVerdict:
    verdict:        Verdict
    reasons:        list[str]
    policy_version: str = "helen-kernel-v0.1-alpha+"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Receipt:
    receipt_type: ReceiptType
    previous_hash: str
    proposal_hash: str
    receipt_hash:  str
    authority:     bool
    ts_ns:         int

    verdict:           dict[str, Any] | None = None
    effect_status:     str             | None = None
    intent_status:     str             | None = None
    state_hash_before: str             | None = None
    state_hash_after:  str             | None = None
    metadata:          dict[str, Any]  | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# Deterministic counter clock
# ============================================================

class Counter:
    def __init__(self, start: int = 0) -> None:
        self.value = start

    def next(self) -> int:
        self.value += 1
        return self.value


CLOCK = Counter()
IDS   = Counter()


def monotonic_ns() -> int:
    return CLOCK.next()


def next_id(prefix: str) -> str:
    return f"{prefix}_{IDS.next():06d}"


# ============================================================
# State
# ============================================================

def initial_state() -> dict[str, Any]:
    return {
        "turn":        0,
        "env":         {},
        "receipts":    [],
        "topic":       "",
        "last_action": "",
        "open_loop":   "",
        "next_step":   "",
    }


def material_state_hash(state: dict[str, Any]) -> str:
    """Hash of the governed surface: everything except the receipt list."""
    material = {
        "turn":        state["turn"],
        "env":         state["env"],
        "topic":       state["topic"],
        "last_action": state["last_action"],
        "open_loop":   state["open_loop"],
        "next_step":   state["next_step"],
    }
    return sha256_hex(material)


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return initial_state()
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return initial_state()


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(canonical_json(state), encoding="utf-8")


# ============================================================
# Resume packet  (explicit session continuity — no implicit memory)
# ============================================================

def load_resume() -> dict[str, Any] | None:
    if not SESSION_RESUME_PATH.exists():
        return None
    try:
        return json.loads(SESSION_RESUME_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_resume(state: dict[str, Any]) -> None:
    """Write only the four continuity fields — nothing inferred."""
    data = {
        "last_topic":  state.get("topic", ""),
        "last_action": state.get("last_action", ""),
        "open_loop":   state.get("open_loop", ""),
        "next_step":   state.get("next_step", ""),
    }
    SESSION_RESUME_PATH.write_text(canonical_json(data), encoding="utf-8")


# ============================================================
# Git continuity
# ============================================================

def get_git_context() -> dict[str, str]:
    def run(cmd: str) -> str:
        try:
            return subprocess.check_output(
                cmd, shell=True, stderr=subprocess.DEVNULL
            ).decode("utf-8").strip()
        except Exception:
            return ""

    return {
        "branch":       run("git branch --show-current"),
        "last_commits": run("git log --oneline -n 3"),
        "status":       run("git status --short"),
    }


# ============================================================
# Ledger  (append-only NDJSON)
# ============================================================

def append_ledger(receipt: Receipt) -> None:
    with LEDGER_PATH.open("a", encoding="utf-8") as f:
        f.write(canonical_json(receipt.to_dict()) + "\n")


def load_ledger() -> list[dict[str, Any]]:
    if not LEDGER_PATH.exists():
        return []
    rows = []
    with LEDGER_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def last_receipt_hash(state: dict[str, Any]) -> str:
    if not state["receipts"]:
        return "genesis"
    return state["receipts"][-1]["receipt_hash"]


# ============================================================
# Receipt construction
# ============================================================

def finalize_receipt(core: dict[str, Any]) -> Receipt:
    """Hash the core payload and return a Receipt with receipt_hash set.

    Normalises all optional fields to their None defaults before hashing so
    that verify_chain — which recomputes from the stored dict (which always
    includes all dataclass fields) — produces the same hash.
    """
    normalised = {
        "receipt_type":      core.get("receipt_type"),
        "previous_hash":     core.get("previous_hash"),
        "proposal_hash":     core.get("proposal_hash"),
        "authority":         core.get("authority", False),
        "ts_ns":             core.get("ts_ns"),
        "verdict":           core.get("verdict"),
        "effect_status":     core.get("effect_status"),
        "intent_status":     core.get("intent_status"),
        "state_hash_before": core.get("state_hash_before"),
        "state_hash_after":  core.get("state_hash_after"),
        "metadata":          core.get("metadata"),
    }
    receipt_hash = sha256_hex(normalised)
    return Receipt(
        receipt_type=normalised["receipt_type"],
        previous_hash=normalised["previous_hash"],
        proposal_hash=normalised["proposal_hash"],
        receipt_hash=receipt_hash,
        authority=normalised["authority"],
        ts_ns=normalised["ts_ns"],
        verdict=normalised["verdict"],
        effect_status=normalised["effect_status"],
        intent_status=normalised["intent_status"],
        state_hash_before=normalised["state_hash_before"],
        state_hash_after=normalised["state_hash_after"],
        metadata=normalised["metadata"],
    )


def make_proposal_receipt(
    proposal: Proposal,
    verdict: PolicyVerdict,
    prev_hash: str,
) -> Receipt:
    core = {
        "receipt_type":  "PROPOSAL",
        "previous_hash": prev_hash,
        "proposal_hash": sha256_hex(proposal.to_dict()),
        "authority":     False,
        "ts_ns":         monotonic_ns(),
        "verdict":       verdict.to_dict(),
        "metadata": {
            "proposal_id": proposal.proposal_id,
            "from_role":   proposal.from_role,
            "intent":      proposal.intent,
            "target":      proposal.target,
        },
    }
    return finalize_receipt(core)


def make_execution_receipt(
    proposal: Proposal,
    prev_hash: str,
    effect_status: str,
    state_hash_before: str,
    state_hash_after: str,
    metadata: dict[str, Any] | None = None,
) -> Receipt:
    core = {
        "receipt_type":      "EXECUTION",
        "previous_hash":     prev_hash,
        "proposal_hash":     sha256_hex(proposal.to_dict()),
        "authority":         False,
        "ts_ns":             monotonic_ns(),
        "effect_status":     effect_status,
        "intent_status":     "UNKNOWN",
        "state_hash_before": state_hash_before,
        "state_hash_after":  state_hash_after,
        "metadata":          metadata or {},
    }
    return finalize_receipt(core)


def append_receipt(state: dict[str, Any], receipt: Receipt) -> None:
    """Append to in-memory receipt list and persist to ledger.

    Constitutional guard: raises if authority is True.
    This is a structural enforcement, not a policy check.
    """
    if receipt.authority is not False:
        raise ValueError("authority_true_forbidden")
    state["receipts"].append(receipt.to_dict())
    append_ledger(receipt)


# ============================================================
# HER: Cognition  (propose only — total, deterministic parser)
# ============================================================

def cognition(user_input: str, state: dict[str, Any]) -> Proposal:
    """
    Parse user input into a Proposal. Never raises.

    Dispatch table:
      /status     → STATUS   (local telemetry)
      /init       → OBSERVE  (boot context load)
      observe X   → OBSERVE  (environmental observation)
      plan X      → PLAN     (planning, non-mutating)
      else        → ECHO     (reflect back, safe default)
    """
    text = user_input.strip()
    low  = text.lower()

    if low in {"/status"}:
        intent = "STATUS"
        target = "local.status"
        payload: dict[str, Any] = {}

    elif low.startswith("/init"):
        intent = "OBSERVE"
        target = "boot.context"
        payload = {"command": "/init"}

    elif low.startswith("observe "):
        intent = "OBSERVE"
        target = low.replace("observe ", "", 1).strip() or "unknown.target"
        payload = {"query": text}

    elif low.startswith("plan "):
        intent = "PLAN"
        target = low.replace("plan ", "", 1).strip() or "unknown.target"
        payload = {"task": text}

    else:
        intent = "ECHO"
        target = "local.echo"
        payload = {"text": text}

    return Proposal(
        proposal_id=next_id("prop"),
        from_role="HER",
        intent=intent,
        target=target,
        payload=payload,
        confidence=0.8,
        created_at_ns=monotonic_ns(),
    )


# ============================================================
# HAL: Governor  (validate — fail-closed policy gate)
# ============================================================

ALLOWED_INTENTS = frozenset({"ECHO", "OBSERVE", "PLAN", "STATUS"})
DENIED_TARGETS  = frozenset({
    "filesystem.delete",
    "payments.execute",
    "credentials.read_raw",
})


def governor(proposal: Proposal, state: dict[str, Any]) -> PolicyVerdict:
    """
    Gate chain (first match wins, fail-closed):
      1. Unknown intent    → DENY
      2. Denied target     → DENY
      3. PLAN              → ALLOW  (non-mutating planning)
      4. OBSERVE           → ALLOW  (read-only observation)
      5. STATUS            → ALLOW  (local telemetry)
      6. Otherwise         → ALLOW  (ECHO and safe defaults)
    """
    if proposal.intent not in ALLOWED_INTENTS:
        return PolicyVerdict(
            verdict="DENY",
            reasons=[f"intent_not_allowed:{proposal.intent}"],
        )

    if proposal.target in DENIED_TARGETS:
        return PolicyVerdict(
            verdict="DENY",
            reasons=[f"target_denied:{proposal.target}"],
        )

    if proposal.intent == "PLAN":
        return PolicyVerdict(verdict="ALLOW", reasons=["planning_non_mutating"])

    if proposal.intent == "OBSERVE":
        return PolicyVerdict(verdict="ALLOW", reasons=["read_only_observation"])

    if proposal.intent == "STATUS":
        return PolicyVerdict(verdict="ALLOW", reasons=["local_status_safe"])

    return PolicyVerdict(verdict="ALLOW", reasons=["local_safe_action"])


# ============================================================
# E: Executor  (sole state-mutation path)
# ============================================================

def execute(
    state: dict[str, Any],
    proposal: Proposal,
) -> tuple[dict[str, Any], Receipt]:
    """Apply proposal effects. Returns (new_state, execution_receipt).

    Deep-copies state before mutation so caller's reference is unaffected.
    """
    new_state    = copy.deepcopy(state)
    before_hash  = material_state_hash(new_state)
    effect_status = "FAILED"
    metadata: dict[str, Any] = {"target": proposal.target}

    if proposal.intent == "ECHO":
        text = proposal.payload.get("text", "")
        new_state["env"]["last_output"] = text
        new_state["topic"]       = "chat"
        new_state["last_action"] = f"echo:{text[:60]}"
        new_state["open_loop"]   = ""
        new_state["next_step"]   = "wait_for_input"
        effect_status = "MATERIALIZED"

    elif proposal.intent == "STATUS":
        new_state["env"]["last_status"] = {
            "turn":     new_state["turn"],
            "receipts": len(new_state["receipts"]),
        }
        new_state["topic"]       = "status"
        new_state["last_action"] = "status"
        new_state["open_loop"]   = ""
        new_state["next_step"]   = "wait_for_input"
        effect_status = "NONE"

    elif proposal.intent == "OBSERVE":
        resume  = load_resume() or {}
        git_ctx = get_git_context()
        new_state["env"]["last_observation"] = {
            "resume": resume,
            "git":    git_ctx,
            "target": proposal.target,
        }
        is_boot = proposal.target == "boot.context"
        new_state["topic"]       = "boot" if is_boot else "observation"
        new_state["last_action"] = f"observe:{proposal.target}"
        new_state["open_loop"]   = "context_loaded" if is_boot else ""
        new_state["next_step"]   = "ask_user"
        effect_status = "NONE"

    elif proposal.intent == "PLAN":
        new_state["env"]["last_plan"] = {
            "target":  proposal.target,
            "payload": proposal.payload,
        }
        new_state["topic"]       = "planning"
        new_state["last_action"] = f"plan:{proposal.target}"
        new_state["open_loop"]   = proposal.target
        new_state["next_step"]   = "await_validation_or_execution"
        effect_status = "NONE"

    after_hash   = material_state_hash(new_state)
    exec_receipt = make_execution_receipt(
        proposal=proposal,
        prev_hash=last_receipt_hash(new_state),
        effect_status=effect_status,
        state_hash_before=before_hash,
        state_hash_after=after_hash,
        metadata=metadata,
    )
    return new_state, exec_receipt


# ============================================================
# Chain verification
# ============================================================

def verify_chain(receipts: list[dict[str, Any]]) -> bool:
    """Verify previous_hash links and receipt hash integrity.

    Returns True iff:
      - previous_hash links form an unbroken chain from 'genesis'
      - each receipt_hash matches the SHA-256 of its content fields
      - every receipt has authority == False
    """
    prev = "genesis"
    for r in receipts:
        if r.get("previous_hash") != prev:
            return False
        core = {k: v for k, v in r.items() if k != "receipt_hash"}
        if sha256_hex(core) != r.get("receipt_hash"):
            return False
        if r.get("authority") is not False:
            return False
        prev = r["receipt_hash"]
    return True


# ============================================================
# Response rendering
# ============================================================

def render_response(
    state: dict[str, Any],
    proposal: Proposal,
    verdict: PolicyVerdict,
) -> str:
    """Build the human-readable reply. No inference — only from explicit state."""
    if proposal.intent == "STATUS":
        return (
            "HELEN ▸ STATUS\n"
            f"- turn: {state['turn']}\n"
            f"- receipts: {len(state['receipts'])}\n"
            f"- topic: {state.get('topic', '')}\n"
            f"- next_step: {state.get('next_step', '')}"
        )

    if proposal.intent == "OBSERVE" and proposal.target == "boot.context":
        obs    = state["env"].get("last_observation", {})
        resume = obs.get("resume", {})
        git    = obs.get("git", {})
        parts  = ["HELEN ▸ BOOT"]
        if resume:
            parts += [
                f"- last_topic:  {resume.get('last_topic',  '')}",
                f"- last_action: {resume.get('last_action', '')}",
                f"- open_loop:   {resume.get('open_loop',   '')}",
                f"- next_step:   {resume.get('next_step',   '')}",
            ]
        if git:
            parts += [
                f"- branch: {git.get('branch', '') or '(none)'}",
                f"- status: {git.get('status', '') or '(clean/none)'}",
            ]
        if not resume and not git:
            parts.append("- no boot context available")
        return "\n".join(parts)

    if proposal.intent == "PLAN":
        return (
            "HELEN ▸ PLAN\n"
            f"- target:    {proposal.target}\n"
            f"- verdict:   {verdict.verdict}\n"
            f"- reasons:   {', '.join(verdict.reasons)}\n"
            f"- next_step: {state.get('next_step', '')}"
        )

    if proposal.intent == "ECHO":
        return f"HELEN ▸ {state['env'].get('last_output', '')}"

    return "HELEN ▸ OK"


# ============================================================
# Kernel step  (F = E ∘ G ∘ C)
# ============================================================

def step(
    state: dict[str, Any],
    user_input: str,
) -> tuple[dict[str, Any], str]:
    """One complete kernel step.

    Returns (new_state, rendered_response).
    Appends proposal + execution receipts to the state and ledger.

    Note: mutates the in-memory receipts list on state before execution
    so the proposal receipt is in the chain when execution_receipt is made.
    """
    proposal = cognition(user_input, state)
    verdict  = governor(proposal, state)

    prop_receipt = make_proposal_receipt(
        proposal=proposal,
        verdict=verdict,
        prev_hash=last_receipt_hash(state),
    )
    append_receipt(state, prop_receipt)   # proposal is always receipted

    if verdict.verdict == "ALLOW":
        updated, exec_receipt = execute(state, proposal)
        updated["turn"] += 1
        append_receipt(updated, exec_receipt)
        save_resume(updated)
        save_state(updated)
        return updated, render_response(updated, proposal, verdict)

    # DENY or PENDING: no state mutation
    denied = copy.deepcopy(state)
    denied["turn"] += 1
    deny_receipt = make_execution_receipt(
        proposal=proposal,
        prev_hash=last_receipt_hash(denied),
        effect_status="DENIED" if verdict.verdict == "DENY" else "DEFERRED",
        state_hash_before=material_state_hash(state),
        state_hash_after=material_state_hash(denied),
        metadata={"reasons": verdict.reasons},
    )
    append_receipt(denied, deny_receipt)
    save_resume(denied)
    save_state(denied)
    return denied, (
        "HELEN ▸ DENY\n"
        f"- target:  {proposal.target}\n"
        f"- reasons: {', '.join(verdict.reasons)}"
    )


# ============================================================
# Boot banner
# ============================================================

def boot_banner(state: dict[str, Any]) -> str:
    resume  = load_resume() or {}
    git_ctx = get_git_context()
    lines   = ["[chat] Connected to HERMES API on :8780"]
    lines.append("Commands: /status  /init  /quit")

    if resume:
        lines.append(
            f"[resume] topic={resume.get('last_topic', '')}  "
            f"action={resume.get('last_action', '')}  "
            f"next={resume.get('next_step', '')}"
        )

    if git_ctx.get("branch") or git_ctx.get("status"):
        lines.append(
            f"[git] branch={git_ctx.get('branch', '') or '(none)'}  "
            f"status={'dirty' if git_ctx.get('status') else 'clean/none'}"
        )

    if state["receipts"]:
        ok = verify_chain(state["receipts"])
        lines.append(
            f"[ledger] receipts={len(state['receipts'])}  "
            f"chain_ok={'yes' if ok else 'no'}"
        )

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

def run() -> None:
    state = load_state()
    print(boot_banner(state))

    while True:
        try:
            user_input = input("\nJMT ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nHELEN ▸ END")
            break

        if not user_input:
            continue

        if user_input in {"/quit", "quit", "exit"}:
            print("HELEN ▸ END")
            break

        state, out = step(state, user_input)
        print(out)


if __name__ == "__main__":
    run()
