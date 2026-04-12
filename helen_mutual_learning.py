"""helen_mutual_learning.py — HELEN-compatible mutual learning kernel.

Single-file reference implementation of the two-loop architecture:

  Loop 1  Human → AI   learn(state, input, output, feedback)
  Loop 2  AI → Human   retrieve(state, target) / insight(state)

Core guarantees (zero hidden learning):
  • No self-modification: learn() is a human-gated API, never AI-called.
  • Every learning signal — approved or rejected — lands in the receipt chain.
  • Only 'approve' signals with confidence ≥ 0.5 enter the learning_index.
  • The receipt chain is the audit log; the index is the governed surface.
  • Governor gates LEARN intent at PENDING so step() cannot bypass the gate.

Architecture: F = E ∘ G ∘ C
  C  cognition  — total, deterministic parser  (never raises)
  G  governor   — 4-gate fail-closed chain
  E  execution  — sole state-mutating layer, only on ALLOW

Receipt chain structure:
  genesis → PROPOSAL₁ → EXECUTION₁ → LEARNING* → PROPOSAL₂ → …

Note on determinism: ts_ns is stored in receipts but excluded from the
hash payload so replayed chains produce identical hashes regardless of
when they are run.
"""
from __future__ import annotations

from collections import Counter as _PyCounter
from dataclasses import dataclass, asdict
from typing import Any, Literal
import copy
import hashlib
import json


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
# Core Types
# ============================================================

ReceiptType  = Literal["PROPOSAL", "EXECUTION", "LEARNING"]
Verdict      = Literal["ALLOW", "DENY", "PENDING"]
EffectStatus = Literal["MATERIALIZED", "FAILED", "NONE", "DENIED", "DEFERRED"]
FeedbackType = Literal["approve", "reject", "edit"]

KNOWN_INTENTS   = frozenset({"OBSERVE", "PLAN", "ECHO", "LEARN", "RETRIEVE", "CHAT"})
PENDING_INTENTS = frozenset({"LEARN"})   # governor always returns PENDING for these
GENESIS_HASH    = "genesis"


@dataclass(slots=True)
class PolicyVerdict:
    verdict: Verdict
    reasons: list[str]
    policy_version: str = "v0.1-alpha+"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Proposal:
    proposal_id: str
    intent: str
    target: str
    payload: dict[str, Any]
    confidence: float
    created_at: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Receipt:
    receipt_type: ReceiptType
    proposal_hash: str
    previous_hash: str
    receipt_hash: str
    authority: bool
    ts_ns: int

    # proposal-related
    verdict: dict[str, Any] | None = None

    # execution-related
    effect_status: EffectStatus | None = None
    intent_status:    str | None = None
    state_hash_before: str | None = None
    state_hash_after:  str | None = None

    # learning-related
    source:      str   | None = None
    input_hash:  str   | None = None
    output_hash: str   | None = None
    feedback:    str   | None = None
    confidence:  float | None = None

    # optional metadata
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# State
# ============================================================

def initial_state() -> dict[str, Any]:
    return {
        "turn": 0,
        "env": {},
        "receipts": [],
        "learning_index": [],   # governed surface: approved signals only
    }


def state_hash(state: dict[str, Any]) -> str:
    """Hash of the material state surface (receipts excluded)."""
    material = {
        "turn":           state["turn"],
        "env":            state["env"],
        "learning_index": state["learning_index"],
    }
    return sha256_hex(material)


# ============================================================
# Deterministic pseudo-clock  (counter-based, replay-safe)
# ============================================================

class _Counter:
    __slots__ = ("_n",)

    def __init__(self) -> None:
        self._n = 0

    def next_int(self) -> int:
        self._n += 1
        return self._n

    def next_id(self, prefix: str) -> str:
        return f"{prefix}_{self.next_int():06d}"


class Counter:
    """Public counter with the interface the user's code expects."""
    def __init__(self) -> None:
        self.value = 0

    def next(self, prefix: str) -> str:
        self.value += 1
        return f"{prefix}_{self.value:06d}"


CLOCK_NS = Counter()
ID_GEN   = Counter()


def monotonic_ns() -> int:
    # Deterministic pseudo-time for replay-safe demos.
    # ts_ns advances per call but is NOT part of the hash payload,
    # so replay produces identical hashes regardless of wall time.
    CLOCK_NS.next("t")
    return CLOCK_NS.value


# ============================================================
# C: Cognition  (total, deterministic parser)
# ============================================================

def retrieve_similar(
    receipts: list[dict[str, Any]],
    target: str,
    approved_only: bool = True,
) -> list[dict[str, Any]]:
    """Minimal retrieval over past learning receipts by target match."""
    out: list[dict[str, Any]] = []
    for r in receipts:
        if r.get("receipt_type") != "LEARNING":
            continue
        if approved_only and r.get("feedback") != "approve":
            continue
        meta = r.get("metadata") or {}
        if meta.get("target") == target:
            out.append(r)
    return out[-5:]


def cognition(user_input: str, state: dict[str, Any]) -> Proposal:
    """
    Parse user input into a Proposal. Always returns a valid Proposal.

    Prefix dispatch:
      'observe X'  → OBSERVE  (record an environmental observation)
      'plan X'     → PLAN     (record a plan)
      'echo X'     → ECHO     (reflect back a message)
      'learn X'    → LEARN    (propose a learning event; governor gates at PENDING)
      'retrieve X' → RETRIEVE (surface past approved learnings)
      else         → CHAT     (general interaction)
    """
    text  = user_input.strip()
    lower = text.lower()

    proposal_id = ID_GEN.next("p")
    ts          = monotonic_ns()

    if lower.startswith("observe "):
        intent, rest = "OBSERVE", text[8:].strip()
        payload = {"observation": rest}

    elif lower.startswith("plan "):
        intent, rest = "PLAN", text[5:].strip()
        payload = {"plan": rest}

    elif lower.startswith("echo "):
        intent, rest = "ECHO", text[5:].strip()
        payload = {"message": rest}

    elif lower.startswith("learn "):
        intent, rest = "LEARN", text[6:].strip()
        payload = {"content": rest}

    elif lower.startswith("retrieve "):
        intent, rest = "RETRIEVE", text[9:].strip()
        # Attach retrieval context so cognition is context-aware
        prior = retrieve_similar(state.get("receipts", []), rest)
        payload = {"query": rest, "prior_cases": len(prior)}

    else:
        intent, rest = "CHAT", text
        payload = {"message": text}

    confidence = 0.90 if intent in {"ECHO", "CHAT", "RETRIEVE"} else 0.75

    return Proposal(
        proposal_id=proposal_id,
        intent=intent,
        target=rest,
        payload=payload,
        confidence=confidence,
        created_at=ts,
    )


# ============================================================
# G: Governor  (fail-closed, 4-gate chain)
# ============================================================

def governor(proposal: Proposal, state: dict[str, Any]) -> PolicyVerdict:
    """
    Gate chain (first match wins, fail-closed):
      1. Unknown intent            → DENY
      2. authority flag in payload → DENY   (non-sovereign guarantee)
      3. LEARN intent              → PENDING (human approval required)
      4. Otherwise                 → ALLOW
    """
    # Gate 1: unknown intent
    if proposal.intent not in KNOWN_INTENTS:
        return PolicyVerdict(
            verdict="DENY",
            reasons=[f"Unknown intent: {proposal.intent!r}"],
        )

    # Gate 2: authority claim (constitutional — non-sovereign)
    if proposal.payload.get("authority") is True:
        return PolicyVerdict(
            verdict="DENY",
            reasons=["Authority claim rejected (non-sovereign system)"],
        )

    # Gate 3: learning requires explicit human approval via learn()
    if proposal.intent in PENDING_INTENTS:
        return PolicyVerdict(
            verdict="PENDING",
            reasons=[f"{proposal.intent} requires human approval — use learn() directly"],
        )

    # Gate 4: default ALLOW
    return PolicyVerdict(verdict="ALLOW", reasons=["All gates passed"])


# ============================================================
# E: Execution  (sole state-mutating layer)
# ============================================================

def execute(
    proposal: Proposal,
    verdict: PolicyVerdict,
    state: dict[str, Any],
) -> tuple[dict[str, Any], EffectStatus]:
    """
    Apply proposal effects to state.
    Mutates only on ALLOW. Returns (new_state, effect_status).
    """
    s = copy.deepcopy(state)

    if verdict.verdict == "DENY":
        return s, "DENIED"
    if verdict.verdict == "PENDING":
        return s, "DEFERRED"

    # ALLOW — apply effects per intent
    intent = proposal.intent

    if intent == "OBSERVE":
        s["env"][f"obs:{proposal.target}"] = proposal.payload.get(
            "observation", proposal.target
        )

    elif intent == "PLAN":
        s["env"][f"plan:{proposal.target}"] = proposal.payload.get(
            "plan", proposal.target
        )

    elif intent == "ECHO":
        s["env"]["last_echo"] = proposal.payload.get("message", "")

    elif intent == "RETRIEVE":
        # Read-only: surface retrieval results into env
        hits = retrieve_similar(s.get("receipts", []), proposal.target)
        s["env"]["last_retrieval"] = {
            "query":  proposal.target,
            "hits":   len(hits),
            "hashes": [h["receipt_hash"][:12] for h in hits],
        }

    elif intent == "CHAT":
        s["env"]["last_message"] = proposal.payload.get("message", "")

    # LEARN is PENDING; execution layer never processes it.
    # Use the learn() function for approved human signals.

    s["turn"] += 1
    return s, "MATERIALIZED"


# ============================================================
# R: Receipt construction  (hash-chained, ts_ns excluded from hash)
# ============================================================

def _prev_hash(state: dict[str, Any]) -> str:
    receipts = state.get("receipts", [])
    return receipts[-1]["receipt_hash"] if receipts else GENESIS_HASH


def _proposal_content_hash(proposal: Proposal) -> str:
    """Stable hash of a proposal's semantic content — excludes proposal_id and ts."""
    return sha256_hex({
        "intent":     proposal.intent,
        "target":     proposal.target,
        "payload":    proposal.payload,
        "confidence": proposal.confidence,
    })


def _make_proposal_receipt(
    proposal: Proposal,
    verdict: PolicyVerdict,
    hash_before: str,
    prev_hash: str,
) -> Receipt:
    """PROPOSAL receipt — governance record."""
    # Use semantic content hash (not full to_dict) for replay determinism:
    # proposal_id and created_at are session-scoped counters, not content.
    p_hash = _proposal_content_hash(proposal)
    ts     = monotonic_ns()
    # ts_ns excluded from hash payload for replay determinism
    hash_payload = {
        "receipt_type":     "PROPOSAL",
        "proposal_hash":    p_hash,
        "previous_hash":    prev_hash,
        "authority":        False,
        "verdict":          verdict.to_dict(),
        "state_hash_before": hash_before,
    }
    return Receipt(
        receipt_type="PROPOSAL",
        proposal_hash=p_hash,
        previous_hash=prev_hash,
        receipt_hash=sha256_hex(hash_payload),
        authority=False,
        ts_ns=ts,
        verdict=verdict.to_dict(),
        state_hash_before=hash_before,
    )


def _make_execution_receipt(
    proposal: Proposal,
    verdict: PolicyVerdict,
    effect_status: EffectStatus,
    hash_before: str,
    hash_after: str,
    prev_hash: str,
) -> Receipt:
    """EXECUTION receipt — effect record."""
    p_hash = _proposal_content_hash(proposal)
    ts     = monotonic_ns()
    hash_payload = {
        "receipt_type":      "EXECUTION",
        "proposal_hash":     p_hash,
        "previous_hash":     prev_hash,
        "authority":         False,
        "effect_status":     effect_status,
        "state_hash_before": hash_before,
        "state_hash_after":  hash_after,
    }
    return Receipt(
        receipt_type="EXECUTION",
        proposal_hash=p_hash,
        previous_hash=prev_hash,
        receipt_hash=sha256_hex(hash_payload),
        authority=False,
        ts_ns=ts,
        verdict=verdict.to_dict(),
        effect_status=effect_status,
        state_hash_before=hash_before,
        state_hash_after=hash_after,
    )


def _make_learning_receipt(
    input_text: str,
    output: Any,
    feedback: FeedbackType,
    confidence: float,
    source: str,
    target: str,
    intent: str,
    prev_hash: str,
) -> Receipt:
    """LEARNING receipt — human experience record."""
    input_hash  = sha256_hex(input_text)
    output_hash = sha256_hex(canonical_json(output))
    # Stable learning hash — uses canonical output encoding, not raw object
    p_hash      = sha256_hex({"input": input_text, "output": canonical_json(output)})
    ts          = monotonic_ns()
    hash_payload = {
        "receipt_type": "LEARNING",
        "proposal_hash": p_hash,
        "previous_hash": prev_hash,
        "authority":     False,
        "source":        source,
        "input_hash":    input_hash,
        "output_hash":   output_hash,
        "feedback":      feedback,
        "confidence":    confidence,
        "metadata":      {"target": target, "intent": intent},
    }
    return Receipt(
        receipt_type="LEARNING",
        proposal_hash=p_hash,
        previous_hash=prev_hash,
        receipt_hash=sha256_hex(hash_payload),
        authority=False,
        ts_ns=ts,
        source=source,
        input_hash=input_hash,
        output_hash=output_hash,
        feedback=feedback,
        confidence=confidence,
        metadata={"target": target, "intent": intent},
    )


# ============================================================
# F = E ∘ G ∘ C   (the kernel step function)
# ============================================================

def step(
    state: dict[str, Any],
    user_input: str,
) -> tuple[dict[str, Any], Receipt, Receipt]:
    """
    One full kernel step.

    Returns (new_state, proposal_receipt, execution_receipt).
    Appends both receipts to new_state["receipts"].

    Chain per step: … → PROPOSAL → EXECUTION → …
    """
    s           = copy.deepcopy(state)
    hash_before = state_hash(s)
    prev_hash   = _prev_hash(s)

    # C: cognition
    proposal = cognition(user_input, s)

    # G: governor
    verdict = governor(proposal, s)

    # R1: proposal receipt (governance record, chained from previous)
    p_receipt = _make_proposal_receipt(proposal, verdict, hash_before, prev_hash)
    s["receipts"].append(p_receipt.to_dict())

    # E: execution (applied to state after receipt insertion)
    s, effect_status = execute(proposal, verdict, s)
    hash_after = state_hash(s)

    # R2: execution receipt (chained from proposal receipt)
    e_receipt = _make_execution_receipt(
        proposal, verdict, effect_status,
        hash_before, hash_after,
        p_receipt.receipt_hash,
    )
    s["receipts"].append(e_receipt.to_dict())

    return s, p_receipt, e_receipt


# ============================================================
# Loop 1: Human → AI   (learn)
# ============================================================

VALID_FEEDBACK = frozenset({"approve", "reject", "edit"})
MIN_CONFIDENCE = 0.5   # gate: only high-confidence approvals enter the index


def learn(
    state: dict[str, Any],
    input_text: str,
    output: Any,
    feedback: FeedbackType,
    confidence: float,
    source: str = "human",
) -> tuple[dict[str, Any], Receipt]:
    """
    Record a human learning signal.  Loop 1: Human → AI.

    Every call appends a LEARNING receipt to the chain (audit trail).
    Only 'approve' signals with confidence ≥ MIN_CONFIDENCE enter
    the learning_index (governed surface — the AI's actual memory).

    'reject' and 'edit' receipts are committed to the chain for
    governance but are NOT indexed — they cannot silently shape policy.

    The AI never calls learn() autonomously. The step() function
    gates LEARN intent at PENDING, enforcing the human approval path.
    """
    if feedback not in VALID_FEEDBACK:
        raise ValueError(f"Invalid feedback {feedback!r}. Must be one of {VALID_FEEDBACK}")
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be in [0.0, 1.0], got {confidence}")

    s         = copy.deepcopy(state)
    prev_hash = _prev_hash(s)

    # Parse intent/target from the input text (read-only cognition)
    stub = cognition(input_text, s)

    l_receipt = _make_learning_receipt(
        input_text=input_text,
        output=output,
        feedback=feedback,
        confidence=confidence,
        source=source,
        target=stub.target,
        intent=stub.intent,
        prev_hash=prev_hash,
    )

    # Gate: only approved, sufficiently-confident learnings enter the index
    if feedback == "approve" and confidence >= MIN_CONFIDENCE:
        s["learning_index"].append({
            "receipt_hash": l_receipt.receipt_hash,
            "target":       stub.target,
            "intent":       stub.intent,
            "confidence":   confidence,
            "source":       source,
            "input_hash":   l_receipt.input_hash,
            "output_hash":  l_receipt.output_hash,
        })

    # Append to receipt chain regardless of feedback
    s["receipts"].append(l_receipt.to_dict())
    return s, l_receipt


# ============================================================
# Loop 2: AI → Human   (retrieve + insight)
# ============================================================

def retrieve(
    state: dict[str, Any],
    target: str,
    top_n: int = 5,
) -> list[dict[str, Any]]:
    """
    Retrieve approved past learnings relevant to target.  Loop 2: AI → Human.

    Returns up to top_n LEARNING receipts (most recent first).
    Only 'approve' feedback receipts are returned — the AI surfaces
    only what humans explicitly endorsed.
    """
    return retrieve_similar(
        state.get("receipts", []), target, approved_only=True
    )[-top_n:]


def insight(
    state: dict[str, Any],
    intent_filter: str | None = None,
) -> dict[str, Any]:
    """
    Generate a pattern summary from the learning index.  Loop 2: AI → Human.

    Derives analytics exclusively from the governed learning_index
    (approved, high-confidence signals only).  No raw receipt content
    is exposed — only aggregate patterns.

    Returns:
      total         — count of indexed learnings
      top_targets   — most frequent targets (up to 5)
      avg_confidence — mean confidence across indexed entries
      recent        — last 5 indexed entries
      note          — data provenance statement
    """
    index = state.get("learning_index", [])

    if intent_filter is not None:
        index = [e for e in index if e.get("intent") == intent_filter.upper()]

    if not index:
        return {
            "total":          0,
            "top_targets":    [],
            "avg_confidence": 0.0,
            "recent":         [],
            "note":           "No approved learnings indexed yet.",
        }

    target_counts = _PyCounter(e["target"] for e in index)
    avg_conf      = sum(e["confidence"] for e in index) / len(index)

    return {
        "total":          len(index),
        "top_targets":    target_counts.most_common(5),
        "avg_confidence": round(avg_conf, 3),
        "recent":         [
            {"target": e["target"], "confidence": e["confidence"]}
            for e in index[-5:]
        ],
        "note": "Patterns extracted from approved human signals only.",
    }


# ============================================================
# Replay  (deterministic fold)
# ============================================================

def replay(
    initial: dict[str, Any],
    inputs: list[str],
) -> dict[str, Any]:
    """Deterministic replay of a list of user inputs from an initial state."""
    s = copy.deepcopy(initial)
    for u in inputs:
        s, _, _ = step(s, u)
    return s


# ============================================================
# Chain verification
# ============================================================

def verify_chain(receipts: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """
    Verify previous_hash links are unbroken: genesis → r₀ → r₁ → …

    Returns (ok, errors).
    """
    errors: list[str] = []
    if not receipts:
        return True, []

    if receipts[0].get("previous_hash") != GENESIS_HASH:
        errors.append(
            f"receipt[0].previous_hash={receipts[0].get('previous_hash')!r} "
            f"expected {GENESIS_HASH!r}"
        )

    for i in range(1, len(receipts)):
        expected = receipts[i - 1]["receipt_hash"]
        actual   = receipts[i].get("previous_hash")
        if actual != expected:
            errors.append(
                f"receipt[{i}].previous_hash={actual!r} "
                f"expected={expected!r}"
            )

    return len(errors) == 0, errors


# ============================================================
# Demo / Main
# ============================================================

if __name__ == "__main__":
    print("=" * 64)
    print("HELEN Mutual Learning Kernel — Two-Loop Demo")
    print("=" * 64)

    s = initial_state()

    # ── Kernel steps (F = E ∘ G ∘ C) ─────────────────────────
    print("\n[KERNEL STEPS]")
    kernel_inputs = [
        "echo hello world",
        "observe memory_pressure=high",
        "plan scale_out service_A",
        "chat what are the options?",
        "learn prune_cache when memory_pressure=high",   # PENDING — governor gate
    ]
    for inp in kernel_inputs:
        s, p_r, e_r = step(s, inp)
        verdict = p_r.verdict["verdict"]
        print(
            f"  {inp!r:<50s}  "
            f"verdict={verdict:<7s}  "
            f"effect={e_r.effect_status}"
        )

    # ── Loop 1: Human → AI ─────────────────────────────────────
    print("\n[LOOP 1 — Human → AI: experience ingestion]")
    learning_events = [
        ("observe memory_pressure=high", "prune_cache",   "approve", 0.92),
        ("observe memory_pressure=high", "restart_pod",   "reject",  0.40),
        ("observe memory_pressure=high", "scale_out",     "approve", 0.85),
        ("observe disk_full",            "archive_logs",  "approve", 0.95),
        ("observe disk_full",            "delete_data",   "reject",  0.20),
        ("observe disk_full",            "archive_logs",  "edit",    0.70),
    ]
    for inp, out, fb, conf in learning_events:
        s, l_r = learn(s, inp, out, fb, conf)
        indexed = (
            fb == "approve" and conf >= MIN_CONFIDENCE
        )
        tag = "✓ indexed" if indexed else "✗ not indexed"
        print(
            f"  learn({out!r:<18s} fb={fb:<7s} conf={conf:.2f}) → {tag}"
        )

    # ── Loop 2: AI → Human ─────────────────────────────────────
    print("\n[LOOP 2 — AI → Human: retrieval + insight]")

    hits = retrieve(s, "memory_pressure=high")
    print(f"  retrieve('memory_pressure=high') → {len(hits)} approved case(s)")
    for h in hits:
        meta = h.get("metadata", {})
        print(
            f"    hash={h['receipt_hash'][:16]}…  "
            f"confidence={h['confidence']}  "
            f"target={meta.get('target','?')}"
        )

    report = insight(s)
    print(f"\n  insight() summary:")
    print(f"    total indexed : {report['total']}")
    print(f"    avg_confidence: {report['avg_confidence']}")
    print(f"    top_targets   : {report['top_targets']}")
    print(f"    note          : {report['note']}")

    # RETRIEVE intent surfaced through the kernel (Loop 2 via F)
    s, p_r, e_r = step(s, "retrieve memory_pressure=high")
    last_ret = s["env"].get("last_retrieval", {})
    print(
        f"\n  step('retrieve ...') → "
        f"hits={last_ret.get('hits', 0)}  "
        f"hashes={last_ret.get('hashes', [])}"
    )

    # ── Chain verification ─────────────────────────────────────
    print("\n[CHAIN]")
    ok, errors = verify_chain(s["receipts"])
    print(
        f"  receipts={len(s['receipts'])}  "
        f"chain={'OK ✓' if ok else 'FAIL ✗'}"
    )
    for e in errors:
        print(f"  ✗ {e}")

    # ── Replay count check ─────────────────────────────────────
    print("\n[REPLAY]")
    s2 = replay(initial_state(), kernel_inputs)
    expected = len(kernel_inputs) * 2
    print(
        f"  replay({len(kernel_inputs)} inputs) → "
        f"{len(s2['receipts'])} receipts  "
        f"(expected {expected}: {'✓' if len(s2['receipts']) == expected else '✗'})"
    )
    # learning_index stays empty (replay only goes through step(), not learn())
    print(
        f"  learning_index after replay: "
        f"{len(s2['learning_index'])} (expected 0: "
        f"{'✓' if len(s2['learning_index']) == 0 else '✗'})"
    )

    print("\n" + "=" * 64)
