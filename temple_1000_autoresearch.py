"""
TEMPLE 1000-Epoch Autoresearch — HELEN OS Self-Improvement

HER proposes improvements to HELEN OS. HAL reviews ruthlessly.
Only BREAKTHROUGH claims (conf ≥ 0.85) survive.
Runs as a background daemon. Progress saved every epoch.

Target: improve HELEN OS codebase, architecture, and capabilities.
"""

import json
import time
import hashlib
import urllib.request
import sys
from pathlib import Path
from datetime import datetime, timezone

OLLAMA = "http://localhost:11434"
HER_MODEL = "her-coder"
HAL_MODEL = "hal-reviewer"
TOTAL_EPOCHS = 1000
BREAKTHROUGH_THRESHOLD = 0.85
SAVE_EVERY = 5  # save progress every N epochs

STATE_DIR = Path(__file__).parent / "helensh" / ".state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = STATE_DIR / "temple_1000_progress.json"
RESULTS_FILE = STATE_DIR / "temple_1000_results.jsonl"

# 50 improvement targets — cycled through 1000 epochs (20 cycles)
TARGETS = [
    # Architecture
    "Propose a way to make /init/live load faster (currently reads all threads + all memory items)",
    "How can the intent gateway reject ambiguous inputs more precisely instead of defaulting to FIRST_DRAFT?",
    "Propose a caching strategy for provider health checks (currently hits Ollama /api/tags on every request)",
    "How should HELEN handle concurrent chat requests (Flask is single-threaded with gunicorn workers)?",
    "Propose a way to version the intent schemas so old clients don't break when new intents are added",
    # Memory
    "How can session continuity packets include a diff summary (what changed since last session)?",
    "Propose a memory compaction strategy — old committed items should be summarized, not accumulated forever",
    "How should HELEN decide which memory items to surface in /init vs suppress?",
    "Propose a way to link conversation history to specific threads (currently session_id only)",
    "How can the memory spine track which provider generated each response for quality analysis?",
    # UX
    "Propose keyboard shortcuts for the AIRI chat interface (currently only Enter to send)",
    "How should HELEN indicate confidence level in responses to the user?",
    "Propose a way for users to correct HELEN's intent classification inline",
    "How should district switching affect the avatar appearance beyond color change?",
    "Propose a notification system for unresolved tensions older than 24 hours",
    # Testing
    "What integration test is most critically missing from the 1776-test suite?",
    "Propose a way to test provider fallback cascade without real API keys",
    "How should the test suite verify that /init returns truthful data (not just structural correctness)?",
    "Propose a smoke test that runs in CI to verify Railway deployment works",
    "What property-based test would catch the most bugs in the intent classifier?",
    # Performance
    "Propose a way to reduce cold-start time when Ollama loads a model for the first time",
    "How can the circuit breaker recover more intelligently (currently waits 5 min flat)?",
    "Propose streaming for /gateway/process so users see partial results during LLM inference",
    "How should HELEN batch multiple intent classifications for efficiency?",
    "Propose a way to preload the most-used Ollama model at boot time",
    # Security
    "What is the most dangerous prompt injection vector in /v1/chat/completions?",
    "Propose rate limiting for the gateway endpoints",
    "How should HELEN log and detect abuse patterns (repeated REJECTED intents)?",
    "Propose a way to sanitize provider responses before persisting to conversations table",
    "What CORS configuration would be more secure than the current allow-all?",
    # Governance
    "Propose a way to make authority=NONE structurally enforced at the DB level (not just JSON labels)",
    "How should the Temple routing path actually influence HAL's review criteria?",
    "Propose a receipted mutation protocol for when threads change status",
    "How can the kernel distinguish between user-created and system-seeded threads permanently?",
    "Propose a way to prove that /init output matches reality (not just seeded data)",
    # Product
    "What is the single most impactful feature to add for the product wedge test?",
    "Propose a way for HELEN to auto-close stale sessions after inactivity",
    "How should HELEN generate a daily summary of all conversations?",
    "Propose a way to export all HELEN data for backup (threads + memory + conversations + receipts)",
    "What metric should HELEN track to prove it's better than notes after interruption?",
    # Integration
    "Propose how HELEN should integrate with Claude Code's memory system (.claude/memory/)",
    "How can the AIRI bridge module be activated without a WebSocket server running?",
    "Propose a way to sync HELEN's threads with GitHub Issues",
    "How should HELEN handle multiple users (currently single-user only)?",
    "Propose MCP server integration so Claude Code can call HELEN's intent gateway as a tool",
    # Meta
    "What is the biggest lie HELEN currently tells about itself in /init?",
    "Propose the most important invariant that is NOT currently tested",
    "What would break first if HELEN had 100 concurrent users?",
    "Propose the single change that would make HELEN most useful for JMT's daily workflow",
    "What architectural debt should be paid before adding any new features?",
]


def call_ollama(model, system, prompt, timeout=90):
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 600},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
            return data.get("message", {}).get("content", "")
    except Exception as e:
        return f"[ERROR: {e}]"


def her_propose(epoch, target):
    system = """You are HER — the creative proposal engine for HELEN OS.
Propose ONE concrete, specific improvement to the HELEN OS codebase.
Be precise: name exact files, functions, or endpoints to change.
Max 150 words. No preamble. Just the improvement."""
    prompt = f"EPOCH {epoch}/{TOTAL_EPOCHS}\nTARGET: {target}\nPropose ONE concrete improvement."
    return call_ollama(HER_MODEL, system, prompt)


def hal_review(epoch, proposal, target):
    system = """You are HAL — the constitutional skeptic.
Review this HELEN OS improvement proposal. Be ruthless.
Only BREAKTHROUGH improvements pass (materially improves the system).

Reply in EXACTLY this format:
VERDICT: APPROVE or REJECT
CONFIDENCE: 0.0 to 1.0
RATIONALE: one sentence"""
    prompt = f"EPOCH {epoch}\nTARGET: {target}\nPROPOSAL: {proposal}\nIs this a BREAKTHROUGH?"
    response = call_ollama(HAL_MODEL, system, prompt)

    verdict, confidence, rationale = "REJECT", 0.0, response
    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("VERDICT:"):
            if "APPROVE" in line.upper():
                verdict = "APPROVE"
        elif line.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("RATIONALE:"):
            rationale = line.split(":", 1)[1].strip()

    return verdict, confidence, rationale


def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE) as f:
            return json.load(f)
    return {"completed": 0, "breakthroughs": 0, "approved": 0, "rejected": 0, "errors": 0}


def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def append_result(record):
    with open(RESULTS_FILE, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main():
    progress = load_progress()
    start_epoch = progress["completed"] + 1

    print(f"{'='*60}", flush=True)
    print(f"TEMPLE 1000-EPOCH AUTORESEARCH — HELEN OS", flush=True)
    print(f"Starting from epoch {start_epoch}/{TOTAL_EPOCHS}", flush=True)
    print(f"HER: {HER_MODEL} | HAL: {HAL_MODEL}", flush=True)
    print(f"Threshold: {BREAKTHROUGH_THRESHOLD}", flush=True)
    print(f"{'='*60}", flush=True)

    for epoch in range(start_epoch, TOTAL_EPOCHS + 1):
        target = TARGETS[(epoch - 1) % len(TARGETS)]

        t0 = time.time()
        proposal = her_propose(epoch, target)
        is_error = proposal.startswith("[ERROR")

        if is_error:
            verdict, confidence, rationale = "ERROR", 0.0, proposal
            progress["errors"] += 1
        else:
            verdict, confidence, rationale = hal_review(epoch, proposal, target)

        elapsed = round(time.time() - t0, 1)
        is_breakthrough = verdict == "APPROVE" and confidence >= BREAKTHROUGH_THRESHOLD

        record = {
            "epoch": epoch,
            "target": target[:80],
            "proposal": proposal[:500],
            "verdict": verdict,
            "confidence": confidence,
            "rationale": rationale[:200],
            "breakthrough": is_breakthrough,
            "elapsed_s": elapsed,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        append_result(record)

        if verdict == "APPROVE":
            progress["approved"] += 1
        elif verdict == "REJECT":
            progress["rejected"] += 1

        if is_breakthrough:
            progress["breakthroughs"] += 1

        progress["completed"] = epoch

        status = "★ BT" if is_breakthrough else ("✓" if verdict == "APPROVE" else "✗" if verdict == "REJECT" else "⚠")
        sys.stdout.write(f"E{epoch:04d} {status} conf={confidence:.1f} {elapsed}s | {target[:50]}...\n")
        sys.stdout.flush()

        if epoch % SAVE_EVERY == 0:
            save_progress(progress)
            pct = epoch / TOTAL_EPOCHS * 100
            bt_rate = progress["breakthroughs"] / max(1, epoch) * 100
            sys.stdout.write(f"  [{pct:.0f}%] {progress['breakthroughs']} breakthroughs / {epoch} epochs ({bt_rate:.1f}%)\n")
            sys.stdout.flush()

    save_progress(progress)

    print(f"\n{'='*60}", flush=True)
    print(f"TEMPLE 1000-EPOCH COMPLETE", flush=True)
    print(f"Breakthroughs: {progress['breakthroughs']}", flush=True)
    print(f"Approved: {progress['approved']}", flush=True)
    print(f"Rejected: {progress['rejected']}", flush=True)
    print(f"Errors: {progress['errors']}", flush=True)
    print(f"{'='*60}", flush=True)


if __name__ == "__main__":
    main()
