"""
TEMPLE Autoresearch Loop — Business Plan V3 → V4 MAYOR SHIP

HER proposes improvements. HAL reviews ruthlessly.
Only BREAKTHROUGH claims (conf ≥ 0.85) are eligible.
MAYOR accepts only what HAL approves at breakthrough level.

Target: improve CONQUEST_HELEN_ULTIMATE_V3.md into a SHIPable V4.
"""

import json
import time
import hashlib
import urllib.request
from pathlib import Path

OLLAMA = "http://localhost:11434"
HER_MODEL = "her-coder"
HAL_MODEL = "hal-reviewer"
EPOCHS = 10
BREAKTHROUGH_THRESHOLD = 0.85

# Load the current document
DOC_PATH = Path(__file__).parent / "CONQUEST_HELEN_ULTIMATE_V3.md"
with open(DOC_PATH) as f:
    CURRENT_DOC = f.read()

# Extract key sections for focused improvement
IMPROVEMENT_TARGETS = [
    "Phrase-thèse — make it more inevitable, less descriptive",
    "Section 3 (deux actifs couplés) — sharper contrast with/without HELEN",
    "Section 7 (logique économique) — add unit economics per user",
    "Section 12 (concurrents) — add a third competitor category (AI agent frameworks like AutoGPT/CrewAI)",
    "Section 13 (pourquoi maintenant) — add regulatory timing (AI Act enforcement dates)",
    "Section 4.3 (P&L) — add gross margin percentage explicitly",
    "Section 9 (gates) — add kill conditions per gate, not just milestones",
    "Section 11 (risques) — add R4: key person risk (JMT = solo founder)",
    "Section 5 (use of funds) — add cash runway in months if zero revenue",
    "Add new section: Team + hiring plan (missing entirely from V3)",
]


def call_ollama(model, system, prompt, timeout=60):
    """Call Ollama and return response text."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 800},
    }).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
            return data.get("message", {}).get("content", "")
    except Exception as e:
        return f"[ERROR: {e}]"


def her_propose(epoch, target):
    """HER proposes a concrete improvement."""
    system = """You are HER — the creative proposal engine for HELEN OS.
You are reviewing a business plan for a 2.75M€ seed raise.
Your job: propose ONE concrete, specific improvement.
Be precise: give exact text to add or replace.
Max 200 words. No preamble. Just the improvement."""

    prompt = f"""EPOCH {epoch}/10 — IMPROVEMENT TARGET:
{target}

CURRENT DOCUMENT EXCERPT (relevant section):
{CURRENT_DOC[:3000]}

Propose ONE concrete improvement. Be specific — give exact wording."""

    return call_ollama(HER_MODEL, system, prompt)


def hal_review(epoch, proposal, target):
    """HAL reviews the proposal. Returns (verdict, confidence, rationale)."""
    system = """You are HAL — the constitutional skeptic.
Review this business plan improvement proposal.
Be ruthless. Only BREAKTHROUGH improvements pass.

Reply in EXACTLY this format:
VERDICT: APPROVE or REJECT
CONFIDENCE: 0.0 to 1.0
RATIONALE: one sentence why"""

    prompt = f"""EPOCH {epoch}/10
TARGET: {target}
PROPOSAL: {proposal}

Is this a BREAKTHROUGH improvement that makes the document materially stronger for investors?
A trivial rewording is REJECT. A structural insight is APPROVE."""

    response = call_ollama(HAL_MODEL, system, prompt)

    # Parse HAL's response
    verdict = "REJECT"
    confidence = 0.0
    rationale = response

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("VERDICT:"):
            v = line.split(":", 1)[1].strip().upper()
            if "APPROVE" in v:
                verdict = "APPROVE"
        elif line.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        elif line.startswith("RATIONALE:"):
            rationale = line.split(":", 1)[1].strip()

    return verdict, confidence, rationale


def main():
    print(f"{'='*60}")
    print(f"TEMPLE AUTORESEARCH — BUSINESS PLAN V3 → V4")
    print(f"Epochs: {EPOCHS} | Threshold: {BREAKTHROUGH_THRESHOLD}")
    print(f"HER: {HER_MODEL} | HAL: {HAL_MODEL}")
    print(f"{'='*60}")
    print(flush=True)

    results = []
    eligible = []

    for epoch in range(1, EPOCHS + 1):
        target = IMPROVEMENT_TARGETS[(epoch - 1) % len(IMPROVEMENT_TARGETS)]

        print(f"\n--- EPOCH {epoch}/{EPOCHS} ---")
        print(f"TARGET: {target[:80]}")
        print("HER proposing...", flush=True)

        t0 = time.time()
        proposal = her_propose(epoch, target)
        her_time = time.time() - t0

        print(f"HER response ({her_time:.1f}s): {proposal[:100]}...")
        print("HAL reviewing...", flush=True)

        t1 = time.time()
        verdict, confidence, rationale = hal_review(epoch, proposal, target)
        hal_time = time.time() - t1

        is_breakthrough = verdict == "APPROVE" and confidence >= BREAKTHROUGH_THRESHOLD

        record = {
            "epoch": epoch,
            "target": target,
            "proposal": proposal,
            "verdict": verdict,
            "confidence": confidence,
            "rationale": rationale,
            "breakthrough": is_breakthrough,
            "her_time": round(her_time, 1),
            "hal_time": round(hal_time, 1),
        }
        results.append(record)

        status = "★ BREAKTHROUGH" if is_breakthrough else ("✓ APPROVED" if verdict == "APPROVE" else "✗ REJECTED")
        print(f"HAL ({hal_time:.1f}s): {status} | conf={confidence} | {rationale[:80]}")

        if is_breakthrough:
            eligible.append(record)

    # Summary
    print(f"\n{'='*60}")
    print(f"TEMPLE SESSION COMPLETE")
    print(f"{'='*60}")
    print(f"Total epochs:     {EPOCHS}")
    print(f"Approved:         {sum(1 for r in results if r['verdict'] == 'APPROVE')}")
    print(f"Rejected:         {sum(1 for r in results if r['verdict'] == 'REJECT')}")
    print(f"BREAKTHROUGH:     {len(eligible)}")
    print(f"Threshold:        {BREAKTHROUGH_THRESHOLD}")

    if eligible:
        print(f"\n--- BREAKTHROUGH CLAIMS (MAYOR-ELIGIBLE) ---")
        for e in eligible:
            print(f"\nEPOCH {e['epoch']} | conf={e['confidence']}")
            print(f"TARGET: {e['target']}")
            print(f"PROPOSAL: {e['proposal'][:300]}")
            print(f"RATIONALE: {e['rationale']}")
    else:
        print("\nNo breakthrough claims. V3 stands as-is.")

    # Save results
    output = {
        "session": "temple_bp_v3_to_v4",
        "epochs": EPOCHS,
        "threshold": BREAKTHROUGH_THRESHOLD,
        "total_approved": sum(1 for r in results if r["verdict"] == "APPROVE"),
        "total_rejected": sum(1 for r in results if r["verdict"] == "REJECT"),
        "breakthroughs": len(eligible),
        "eligible_claims": eligible,
        "all_results": results,
        "session_hash": hashlib.sha256(json.dumps(results, sort_keys=True).encode()).hexdigest()[:16],
    }

    out_path = Path(__file__).parent / "helensh" / ".state" / "temple_bp_v4_session.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nSession saved: {out_path}")
    print(f"Session hash: {output['session_hash']}")

    return output


if __name__ == "__main__":
    main()
