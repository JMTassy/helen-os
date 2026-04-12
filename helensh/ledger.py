"""Append-only NDJSON receipt ledger — live replay-safe persistence.

The ledger is the ground truth for a session. Boot hydration reads it,
verifies chain + hashes, then replays to reconstruct governed state.

Public API:
  LedgerWriter(path)                   — append receipts to NDJSON file
  LedgerReader(path)                   — read receipts back from NDJSON file
  persisted_step(state, input, writer) — step() + write both receipts
  hydrate_session(s0, path)            — rebuild verified state from ledger
"""
import json
from pathlib import Path
from typing import Iterator, List, Tuple

from helensh.kernel import step
from helensh.replay import replay_from_receipts, verify_chain, verify_receipt_hashes
from helensh.state import canonical


# ── Writer ────────────────────────────────────────────────────────────


class LedgerWriter:
    """Append receipts to an NDJSON ledger file.

    Each receipt is written as a single line of canonical JSON.
    The file is opened in append mode on each write to survive crashes.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, receipt: dict) -> None:
        """Append one receipt as a canonical NDJSON line."""
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(canonical(receipt) + "\n")

    def append_step(self, p_receipt: dict, e_receipt: dict) -> None:
        """Append both receipts from one kernel step (proposal then execution)."""
        self.append(p_receipt)
        self.append(e_receipt)


# ── Reader ────────────────────────────────────────────────────────────


class LedgerReader:
    """Read receipts from an NDJSON ledger file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[dict]:
        return self.receipts()

    def receipts(self) -> Iterator[dict]:
        """Yield receipts in ledger order. Empty if file does not exist."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def all(self) -> List[dict]:
        """Return all receipts as a list."""
        return list(self.receipts())

    def __len__(self) -> int:
        return sum(1 for _ in self.receipts())


# ── Persisted step ────────────────────────────────────────────────────


def persisted_step(
    state: dict,
    user_input: str,
    writer: LedgerWriter,
) -> Tuple[dict, dict]:
    """Run one kernel step and durably append both receipts to the ledger.

    Returns (new_state, proposal_receipt) — identical contract to step().
    The caller must not mutate the returned state between persisted steps,
    as the ledger and in-memory chain must stay in sync.
    """
    new_state, p_receipt = step(state, user_input)
    # Execution receipt is always the last receipt appended by step()
    e_receipt = new_state["receipts"][-1]
    writer.append_step(p_receipt, e_receipt)
    return new_state, p_receipt


# ── Boot hydration ────────────────────────────────────────────────────


def hydrate_session(
    initial_state: dict,
    ledger_path: str | Path,
) -> Tuple[dict, bool, list]:
    """Reconstruct verified governed state from a persisted ledger.

    Process:
      1. Read all receipts from the NDJSON ledger.
      2. Verify previous_hash chain (I4).
      3. Recompute and verify every receipt hash (I9 / tamper detection).
      4. Replay user_inputs from PROPOSAL receipts to reconstruct state.

    Returns (state, ok, errors):
      - state: the reconstructed final state, or initial_state if ledger is
        empty or verification fails.
      - ok: True iff the ledger passed all integrity checks.
      - errors: list of human-readable error strings (empty on success).

    The returned state's receipts list reflects the replayed chain, which
    is hash-identical to the ledger (determinism guarantees this).
    """
    reader = LedgerReader(ledger_path)
    receipts = reader.all()

    if not receipts:
        return initial_state, True, []

    errors: list = []

    # I4: chain integrity
    chain_ok, chain_errors = verify_chain(receipts)
    errors.extend(chain_errors)

    # Receipt hash validity (tamper detection)
    hash_ok, hash_errors = verify_receipt_hashes(receipts)
    errors.extend(hash_errors)

    if errors:
        return initial_state, False, errors

    # Deterministic state reconstruction
    final_state = replay_from_receipts(initial_state, receipts)
    return final_state, True, []


# ── Exports ───────────────────────────────────────────────────────────

__all__ = [
    "LedgerWriter",
    "LedgerReader",
    "persisted_step",
    "hydrate_session",
]
