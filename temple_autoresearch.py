#!/usr/bin/env python3
"""HELEN OS — TEMPLE Autonomous Research Loop.

HER proposes. HAL reviews. N iterations. Best claim promoted.
Everything receipted. Everything witnessed. Pull, not push.

Usage:
    python temple_autoresearch.py [--iterations N] [--topic TOPIC]

Law: No receipt = no reality.
"""
from __future__ import annotations

import sys
import time
import json
from pathlib import Path

# Ensure helensh is importable
sys.path.insert(0, str(Path(__file__).parent))

from helensh.kernel import init_session, step
from helensh.agents.her_coder import HerCoder
from helensh.agents.hal_reviewer import HalReviewer
from helensh.sandbox.temple import TempleSandbox
from helensh.witness import witness_temple, build_witness_record, verify_witness
from helensh.memory import disclose, verify_memory, build_memory_packet, verify_memory_packet
from helensh.continuity import derive_tasks, build_continuity_packet, verify_continuity_packet
from helensh.claims import ClaimEngine
from helensh.state import governed_state_hash


def print_header():
    print("""
╔══════════════════════════════════════════════════════════════╗
║          HELEN OS — TEMPLE AUTORESEARCH LOOP                ║
║                                                             ║
║  HER proposes.  HAL reviews.  Receipts prove.               ║
║  Pull mode.  Authority: false.  No receipt = no reality.    ║
╚══════════════════════════════════════════════════════════════╝
""")


def run_temple_loop(topic: str, iterations: int = 100):
    """Run TEMPLE brainstorm, witness, and extract the claim of the day."""

    print_header()
    t0 = time.time()

    # ── 1. Boot state ──
    print(f"[BOOT] Initializing session...")
    state = init_session(session_id="temple-autoresearch", user="jmt")
    print(f"  State hash: {governed_state_hash(state)[:16]}...")
    print(f"  Authority: false")
    print()

    # ── 2. Register the research task ──
    print(f"[TASK] Registering research objective...")
    state, task_receipt = step(state, f"#task {topic}")
    print(f"  Task: {topic}")
    print(f"  Verdict: {task_receipt['verdict']}")
    print(f"  Receipt: {task_receipt['hash'][:16]}...")
    print()

    # ── 3. Initialize agents ──
    print(f"[AGENTS] Booting HER + HAL...")
    her = HerCoder()
    hal = HalReviewer()
    temple = TempleSandbox(her, hal, approval_threshold=0.6)
    print(f"  HER: C-layer (propose)")
    print(f"  HAL: G-layer (validate)")
    print(f"  Threshold: 0.6")
    print()

    # ── 4. BRAINSTORM ──
    print(f"[TEMPLE] Starting brainstorm — {iterations} iterations")
    print(f"  Topic: {topic}")
    print(f"  {'─' * 56}")

    session = temple.brainstorm(topic, state=state, iterations=iterations)

    # Print each claim
    approved_count = 0
    rejected_count = 0
    best_claim = None
    best_confidence = 0.0

    for i, claim in enumerate(session.claims):
        icon = "✓" if claim.verdict == "APPROVE" else "✗"
        conf = f"{claim.confidence:.2f}"
        eligible = "★" if claim.eligible else " "

        if i < 20 or i % 10 == 0 or claim.eligible:
            print(f"  [{i+1:3d}/{iterations}] {icon} {eligible} conf={conf} | {claim.text[:60]}...")

        if claim.verdict == "APPROVE":
            approved_count += 1
        else:
            rejected_count += 1

        if claim.eligible and claim.confidence > best_confidence:
            best_confidence = claim.confidence
            best_claim = claim

    print(f"  {'─' * 56}")
    print(f"  Total: {len(session.claims)} claims")
    print(f"  Approved: {approved_count} | Rejected: {rejected_count}")
    print(f"  Eligible: {len(session.eligible_claims)}")
    print(f"  Session hash: {session.session_hash[:16]}...")
    print()

    # ── 5. WITNESS the session ──
    print(f"[WITNESS] Recording observation into kernel...")
    new_state, record, w_receipt = witness_temple(state, session)
    w_ok, w_errors = verify_witness(new_state, record)
    print(f"  Witness hash: {record.witness_hash[:16]}...")
    print(f"  Verified: {w_ok}")
    print(f"  Receipts after witness: {len(new_state['receipts'])}")
    print()

    # ── 6. Memory verification ──
    print(f"[MEMORY] Verifying memory integrity...")
    mem_ok, mem_errors = verify_memory(new_state)
    mem = disclose(new_state)
    print(f"  Memory integrity: {'PASS' if mem_ok else 'FAIL'}")
    print(f"  Memory keys: {len(mem)}")
    if mem_errors:
        for e in mem_errors[:3]:
            print(f"    error: {e}")
    print()

    # ── 7. Continuity packet ──
    print(f"[CONTINUITY] Building continuity packet...")
    tasks = derive_tasks(new_state["receipts"])
    packet = build_continuity_packet(new_state["receipts"])
    pv = verify_continuity_packet(packet)
    print(f"  Tasks: {packet.task_count}")
    for tid, t in tasks.items():
        print(f"    {tid}: {t.goal} [{t.status}]")
    print(f"  Packet verified: {pv}")
    print()

    # ── 8. CLAIM OF THE DAY ──
    print(f"╔══════════════════════════════════════════════════════════════╗")
    print(f"║  CLAIM OF THE DAY                                          ║")
    print(f"╠══════════════════════════════════════════════════════════════╣")

    if best_claim:
        print(f"║  Text: {best_claim.text[:53]:<53}║")
        print(f"║  Confidence: {best_confidence:<47.2f}║")
        print(f"║  Receipt: {best_claim.receipt_hash[:16]}...{' ' * 33}║")
        print(f"║  Turn: {best_claim.turn:<53}║")
    else:
        print(f"║  No eligible claims produced.                              ║")
        print(f"║  HAL rejected all proposals. Governance held.              ║")

    print(f"╠══════════════════════════════════════════════════════════════╣")

    # ── 9. Build a VerifiableClaim from the state ──
    if new_state["receipts"]:
        engine = ClaimEngine(new_state)
        integrity_claim = engine.claim_ledger_integrity()
        print(f"║  Ledger integrity claim:                                   ║")
        print(f"║    hash: {integrity_claim.receipt_hash[:16]}...{' ' * 32}║")
        print(f"║    root: {integrity_claim.merkle_root[:16]}...{' ' * 32}║")

    elapsed = time.time() - t0
    print(f"╠══════════════════════════════════════════════════════════════╣")
    print(f"║  Time: {elapsed:.1f}s{' ' * (52 - len(f'{elapsed:.1f}s'))}║")
    print(f"║  Authority: false{' ' * 42}║")
    print(f"║  Law: No receipt = no reality.{' ' * 29}║")
    print(f"╚══════════════════════════════════════════════════════════════╝")

    # ── 10. Summary for boot memory ──
    summary = {
        "topic": topic,
        "iterations": iterations,
        "total_claims": len(session.claims),
        "approved": approved_count,
        "rejected": rejected_count,
        "eligible": len(session.eligible_claims),
        "best_claim": best_claim.text if best_claim else None,
        "best_confidence": best_confidence,
        "session_hash": session.session_hash,
        "witness_hash": record.witness_hash,
        "elapsed_seconds": round(elapsed, 1),
    }

    # Write summary to .state for continuity
    out_path = Path("helensh/.state/last_temple_summary.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nSummary written to: {out_path}")

    return summary


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="HELEN OS TEMPLE Autoresearch")
    parser.add_argument("--iterations", "-n", type=int, default=100)
    parser.add_argument("--topic", "-t", type=str,
                        default="Design the convergence proof for adversarial tournament selection dynamics with blind Borda judges")
    args = parser.parse_args()

    run_temple_loop(args.topic, args.iterations)
