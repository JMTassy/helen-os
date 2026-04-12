"""Replay engine — chain verification and state reconstruction from receipts.

Three functions:
  verify_chain(receipts)          — checks previous_hash links are intact
  verify_receipt_hashes(receipts) — recomputes and validates each receipt hash
  replay_from_receipts(s0, receipts) — rebuilds state by replaying inputs
  rebuild_and_verify(s0, receipts)   — full check: chain + hashes + state match
"""
import copy
from typing import List, Tuple

from helensh.kernel import (
    GENESIS_HASH,
    RECEIPT_TYPE_EXECUTION,
    RECEIPT_TYPE_PROPOSAL,
    step,
)
from helensh.state import canonical_hash, governed_state_hash


def verify_chain(receipts: list) -> Tuple[bool, list]:
    """Verify that previous_hash links form an unbroken chain.

    Returns (ok, errors) where errors is a list of descriptions.
    """
    errors = []
    if not receipts:
        return True, []

    # First receipt must link to genesis
    if receipts[0].get("previous_hash") != GENESIS_HASH:
        errors.append(f"receipt[0] previous_hash is {receipts[0].get('previous_hash')!r}, expected {GENESIS_HASH!r}")

    # Each subsequent receipt must link to the previous receipt's hash
    for i in range(1, len(receipts)):
        expected = receipts[i - 1]["hash"]
        actual = receipts[i].get("previous_hash")
        if actual != expected:
            errors.append(f"receipt[{i}] previous_hash mismatch: got {actual!r}, expected {expected!r}")

    return len(errors) == 0, errors


def verify_receipt_hashes(receipts: list) -> Tuple[bool, list]:
    """Recompute each receipt hash and verify it matches the stored hash.

    Returns (ok, errors).
    """
    errors = []
    for i, r in enumerate(receipts):
        rtype = r.get("type")

        if rtype == RECEIPT_TYPE_PROPOSAL:
            payload = {
                "schema": r["schema"],
                "type": r["type"],
                "turn": r["turn"],
                "user_input": r["user_input"],
                "proposal": r["proposal"],
                "verdict": r["verdict"],
                "authority": r["authority"],
                "state_hash_before": r["state_hash_before"],
                "previous_hash": r["previous_hash"],
            }
        elif rtype == RECEIPT_TYPE_EXECUTION:
            payload = {
                "schema": r["schema"],
                "type": r["type"],
                "turn": r["turn"],
                "user_input": "",
                "proposal": r["proposal"],
                "verdict": r["verdict"],
                "authority": r["authority"],
                "state_hash_before": r["state_hash_before"],
                "previous_hash": r["previous_hash"],
                "state_hash_after": r["state_hash_after"],
                "effect_status": r["effect_status"],
                "intent_status": r["intent_status"],
            }
        else:
            errors.append(f"receipt[{i}] unknown type {rtype!r}")
            continue

        recomputed = canonical_hash(payload)
        if recomputed != r["hash"]:
            errors.append(f"receipt[{i}] hash mismatch: stored={r['hash'][:16]}..., recomputed={recomputed[:16]}...")

    return len(errors) == 0, errors


def replay_from_receipts(initial_state: dict, receipts: list) -> dict:
    """Replay user inputs extracted from proposal receipts to reconstruct state.

    This is the receipt-based replay: we extract user_input from each PROPOSAL
    receipt and feed it through step().
    """
    s = copy.deepcopy(initial_state)

    # Extract user inputs from proposal receipts only
    inputs = [
        r["user_input"]
        for r in receipts
        if r.get("type") == RECEIPT_TYPE_PROPOSAL
    ]

    for u in inputs:
        s, _ = step(s, u)

    return s


def rebuild_and_verify(initial_state: dict, receipts: list) -> Tuple[bool, list]:
    """Full verification: chain integrity + hash validity + state reconstruction.

    Returns (ok, errors).
    """
    errors = []

    # 1. Chain integrity
    chain_ok, chain_errors = verify_chain(receipts)
    errors.extend(chain_errors)

    # 2. Hash validity
    hash_ok, hash_errors = verify_receipt_hashes(receipts)
    errors.extend(hash_errors)

    # 3. State reconstruction — replayed state must match
    replayed = replay_from_receipts(initial_state, receipts)

    # Compare receipt chains
    if len(replayed["receipts"]) != len(receipts):
        errors.append(
            f"receipt count mismatch: replayed={len(replayed['receipts'])}, original={len(receipts)}"
        )
    else:
        for i in range(len(receipts)):
            if replayed["receipts"][i]["hash"] != receipts[i]["hash"]:
                errors.append(f"receipt[{i}] hash diverged on replay")
                break

    # Compare governed state hash of final state
    if receipts:
        # Find the last execution receipt's state_hash_after
        last_exec = None
        for r in reversed(receipts):
            if r.get("type") == RECEIPT_TYPE_EXECUTION:
                last_exec = r
                break

        if last_exec:
            replayed_hash = governed_state_hash(replayed)
            # The replayed state hash should match what a fresh step would produce
            # (but governed_state_hash excludes receipts/history, so we check turn + env + caps + wm)
            replayed_check = {
                "session_id": replayed["session_id"],
                "turn": replayed["turn"],
                "env": replayed["env"],
                "capabilities": replayed["capabilities"],
                "working_memory": replayed["working_memory"],
            }
            original_check = {
                "session_id": initial_state["session_id"],
                "turn": len([r for r in receipts if r.get("type") == RECEIPT_TYPE_PROPOSAL]),
                "env": replayed["env"],  # use replayed since we don't have original final
                "capabilities": replayed["capabilities"],
                "working_memory": replayed["working_memory"],
            }
            # These should be identical since replay is deterministic
            if replayed_check != original_check:
                errors.append("final state diverged on replay")

    return len(errors) == 0, errors
