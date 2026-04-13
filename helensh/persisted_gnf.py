"""HELEN OS — Persisted GNF Step.

Binds the GNF 5-layer governance pipeline to durable persistence:
    gnf_step() + LedgerWriter + ArtifactStore → persisted_gnf_step()

This is the primary execution surface for Week 1 KERNEL.

Every governed action now:
    1. Runs through S → P → V → T → E (GNF)
    2. Persists both receipts to the append-only ledger
    3. Stores tool artifacts in the content-addressed store
    4. Returns ArtifactRef for each stored artifact
    5. Supports boot hydration (replay from ledger + artifact verification)

Contract:
    - Receipt = governance witness (what was decided) → ledger
    - Artifact = execution witness (what was produced) → artifact store
    - These are separate. Receipts reference artifacts by hash.
    - Artifact IDs are NOT in receipt_hash (same boundary as trace).

Usage:
    ledger = LedgerWriter("session.jsonl")
    store = ArtifactStore("artifacts/")
    registry = default_registry()

    state, receipt, artifact_ref = persisted_gnf_step(
        state, "compute 2+2",
        ledger=ledger,
        artifact_store=store,
        tool_registry=registry,
    )
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from helensh.gnf import gnf_step, GNFReceipt
from helensh.ledger import LedgerWriter, LedgerReader
from helensh.artifacts import ArtifactStore, ArtifactRef
from helensh.tools import ToolRegistry
from helensh.state import canonical_hash
from helensh.replay import verify_chain, verify_receipt_hashes


# ── Persisted GNF Step ─────────────────────────────────────────────


def persisted_gnf_step(
    state: dict,
    user_input: Union[str, dict],
    ledger: LedgerWriter,
    artifact_store: Optional[ArtifactStore] = None,
    tool_registry: Optional[ToolRegistry] = None,
    stress_checks: Optional[List[Tuple[str, Callable]]] = None,
) -> Tuple[dict, GNFReceipt, Optional[ArtifactRef]]:
    """Execute one GNF step with durable persistence.

    Combines:
        gnf_step()    → governance + execution + tool artifact
        LedgerWriter  → append both receipts
        ArtifactStore → store tool result as content-addressed blob

    Returns (new_state, gnf_receipt, artifact_ref).
        artifact_ref is None if no tool executed or no artifact store provided.

    The execution order is:
        1. gnf_step() — full 5-layer governance
        2. Ledger write — proposal + execution receipts
        3. Artifact write — tool result (if any)

    Ledger write happens BEFORE artifact write so that the receipt
    chain is never gapped even if artifact storage fails.
    """
    # ── 1. GNF step (S → P → V → T → E) ──
    new_state, gnf_receipt = gnf_step(
        state, user_input,
        stress_checks=stress_checks,
        tool_registry=tool_registry,
    )

    # ── 2. Persist receipts to ledger ──
    # Proposal receipt is second-to-last, execution receipt is last
    p_receipt = new_state["receipts"][-2]
    e_receipt = new_state["receipts"][-1]
    ledger.append_step(p_receipt, e_receipt)

    # ── 3. Store artifact (if tool executed) ──
    artifact_ref = None
    if artifact_store is not None and gnf_receipt.tool_result is not None:
        artifact_ref = artifact_store.write(
            gnf_receipt.tool_result.to_dict(),
            artifact_type="tool_result",
            source=gnf_receipt.proposal.get("action", "unknown"),
        )

    return new_state, gnf_receipt, artifact_ref


# ── Batch execution ────────────────────────────────────────────────


def persisted_gnf_batch(
    state: dict,
    inputs: List[Union[str, dict]],
    ledger: LedgerWriter,
    artifact_store: Optional[ArtifactStore] = None,
    tool_registry: Optional[ToolRegistry] = None,
    stress_checks: Optional[List[Tuple[str, Callable]]] = None,
) -> Tuple[dict, List[GNFReceipt], List[Optional[ArtifactRef]]]:
    """Execute a batch of GNF steps with durable persistence.

    Convenience wrapper: runs persisted_gnf_step() for each input
    in sequence, threading state forward.

    Returns (final_state, receipts, artifact_refs).
    """
    receipts = []
    artifact_refs = []

    for user_input in inputs:
        state, receipt, artifact_ref = persisted_gnf_step(
            state, user_input,
            ledger=ledger,
            artifact_store=artifact_store,
            tool_registry=tool_registry,
            stress_checks=stress_checks,
        )
        receipts.append(receipt)
        artifact_refs.append(artifact_ref)

    return state, receipts, artifact_refs


# ── Boot hydration (GNF-aware) ────────────────────────────────────


def hydrate_gnf_session(
    initial_state: dict,
    ledger_path: Union[str, Path],
    artifact_store: Optional[ArtifactStore] = None,
) -> Tuple[dict, bool, List[str]]:
    """Reconstruct verified state from a persisted GNF ledger.

    Process:
        1. Read all receipts from NDJSON ledger
        2. Verify chain integrity (I4)
        3. Verify receipt hashes (tamper detection)
        4. Replay to reconstruct state
        5. Optionally verify artifact references

    Returns (state, ok, errors).
    """
    from helensh.replay import replay_from_receipts

    reader = LedgerReader(ledger_path)
    receipts = reader.all()

    if not receipts:
        return initial_state, True, []

    errors: list = []

    # I4: chain integrity
    chain_ok, chain_errors = verify_chain(receipts)
    errors.extend(chain_errors)

    # Receipt hash tamper detection
    hash_ok, hash_errors = verify_receipt_hashes(receipts)
    errors.extend(hash_errors)

    if errors:
        return initial_state, False, errors

    # Replay state from receipts
    final_state = replay_from_receipts(initial_state, receipts)

    # Optional: verify artifact references exist in store
    if artifact_store is not None:
        for r in receipts:
            if r.get("type") == "EXECUTION":
                tool_res = r.get("tool_result")
                if tool_res is not None:
                    # Compute what the artifact_id should be
                    expected_id = canonical_hash(tool_res)
                    if not artifact_store.exists(expected_id):
                        errors.append(
                            f"artifact {expected_id[:16]}... referenced in receipt "
                            f"but not found in store"
                        )

    ok = len(errors) == 0
    return final_state, ok, errors


# ── Exports ────────────────────────────────────────────────────────

__all__ = [
    "persisted_gnf_step",
    "persisted_gnf_batch",
    "hydrate_gnf_session",
]
