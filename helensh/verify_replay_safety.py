"""HELEN OS — Replay Safety Verification.

Verifies that ALL ledger paths are replay-safe:
  - web_api_ledger.jsonl  (web UI path)
  - boot_ledger.jsonl     (CLI boot path)
  - live_ledger.jsonl     (CLI runtime path)

Run as:
    python -m helensh.verify_replay_safety

Checks per ledger:
  1. Chain integrity  — previous_hash links unbroken from genesis
  2. Hash validity    — recomputed receipt hashes match stored
  3. Authority        — authority: false on every receipt
  4. State replay     — rebuild_and_verify produces identical receipts

Law: No receipt = no reality.
     If the ledger cannot be replayed, the path is not replay-safe.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from helensh.kernel import init_session, GENESIS_HASH
from helensh.ledger import LedgerReader
from helensh.replay import verify_chain, verify_receipt_hashes, rebuild_and_verify


# ── Paths ────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
STATE_DIR = ROOT / "helensh" / ".state"

LEDGER_PATHS = {
    "web_api": STATE_DIR / "web_api_ledger.jsonl",
    "boot":    STATE_DIR / "boot_ledger.jsonl",
    "live":    STATE_DIR / "live_ledger.jsonl",
}

# Session IDs used by each ledger path (must match what created the receipts)
LEDGER_SESSION_IDS = {
    "web_api": "helen-web-api",
    "boot":    "helen-os",
    "live":    "helen-os",
}


# ── Verification ─────────────────────────────────────────────────────


def verify_authority_invariant(receipts: list) -> Tuple[bool, List[str]]:
    """Check that authority == False on every receipt (I6)."""
    errors = []
    for i, r in enumerate(receipts):
        if r.get("authority") is not False:
            errors.append(f"receipt[{i}] authority={r.get('authority')!r}, expected False")
    return len(errors) == 0, errors


def verify_ledger(
    name: str,
    path: Path,
    session_id: str = "replay-verify",
) -> Dict:
    """Verify a single ledger file. Returns a status dict.

    Status dict:
      name: str           — ledger name
      path: str           — file path
      exists: bool        — file exists
      receipt_count: int  — number of receipts
      chain_ok: bool      — chain integrity
      hash_ok: bool       — hash validity
      authority_ok: bool  — authority invariant
      replay_ok: bool     — full rebuild_and_verify
      errors: list        — all error messages
      status: str         — OK | EMPTY | MISSING | INTEGRITY_FAILURE
    """
    result = {
        "name": name,
        "path": str(path),
        "exists": path.exists(),
        "receipt_count": 0,
        "chain_ok": False,
        "hash_ok": False,
        "authority_ok": False,
        "replay_ok": False,
        "errors": [],
        "status": "UNKNOWN",
    }

    if not path.exists():
        result["status"] = "MISSING"
        return result

    # Read receipts
    reader = LedgerReader(str(path))
    receipts = reader.all()
    result["receipt_count"] = len(receipts)

    if not receipts:
        result["status"] = "EMPTY"
        result["chain_ok"] = True
        result["hash_ok"] = True
        result["authority_ok"] = True
        result["replay_ok"] = True
        return result

    # 1. Chain integrity
    chain_ok, chain_errors = verify_chain(receipts)
    result["chain_ok"] = chain_ok
    result["errors"].extend(chain_errors)

    # 2. Hash validity
    hash_ok, hash_errors = verify_receipt_hashes(receipts)
    result["hash_ok"] = hash_ok
    result["errors"].extend(hash_errors)

    # 3. Authority invariant
    auth_ok, auth_errors = verify_authority_invariant(receipts)
    result["authority_ok"] = auth_ok
    result["errors"].extend(auth_errors)

    # 4. Full replay verification
    s0 = init_session(session_id=session_id)
    replay_ok, replay_errors = rebuild_and_verify(s0, receipts)
    result["replay_ok"] = replay_ok
    result["errors"].extend(replay_errors)

    if chain_ok and hash_ok and auth_ok and replay_ok:
        result["status"] = "OK"
    else:
        result["status"] = "INTEGRITY_FAILURE"

    return result


def verify_all_ledgers() -> List[Dict]:
    """Verify all known ledger paths. Returns list of status dicts."""
    results = []
    for name, path in LEDGER_PATHS.items():
        sid = LEDGER_SESSION_IDS.get(name, "replay-verify")
        results.append(verify_ledger(name, path, session_id=sid))
    return results


# ── CLI ──────────────────────────────────────────────────────────────


def _print_report(results: List[Dict]) -> bool:
    """Print human-readable verification report. Returns True if all OK."""
    all_ok = True
    print("=" * 60)
    print("HELEN OS — Replay Safety Verification")
    print("=" * 60)

    for r in results:
        status_icon = {
            "OK": "PASS",
            "EMPTY": "EMPTY",
            "MISSING": "SKIP",
            "INTEGRITY_FAILURE": "FAIL",
        }.get(r["status"], "????")

        print(f"\n  [{status_icon}] {r['name']}")
        print(f"         path: {r['path']}")
        print(f"     receipts: {r['receipt_count']}")

        if r["status"] == "MISSING":
            print("       (file not found — skipped)")
            continue

        if r["status"] == "EMPTY":
            print("       (empty ledger — trivially safe)")
            continue

        print(f"        chain: {'OK' if r['chain_ok'] else 'FAIL'}")
        print(f"       hashes: {'OK' if r['hash_ok'] else 'FAIL'}")
        print(f"    authority: {'OK' if r['authority_ok'] else 'FAIL'}")
        print(f"       replay: {'OK' if r['replay_ok'] else 'FAIL'}")

        if r["errors"]:
            all_ok = False
            print(f"       errors:")
            for e in r["errors"][:5]:
                print(f"         - {e}")
            if len(r["errors"]) > 5:
                print(f"         ... and {len(r['errors']) - 5} more")

        if r["status"] == "INTEGRITY_FAILURE":
            all_ok = False

    print("\n" + "=" * 60)
    overall = "ALL PATHS REPLAY-SAFE" if all_ok else "INTEGRITY FAILURE DETECTED"
    print(f"  Result: {overall}")
    print(f"  Authority: false")
    print(f"  Law: No receipt = no reality.")
    print("=" * 60)

    return all_ok


# ── Module entry point ───────────────────────────────────────────────

def main() -> int:
    """Entry point for `python -m helensh.verify_replay_safety`."""
    results = verify_all_ledgers()
    ok = _print_report(results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "verify_ledger",
    "verify_all_ledgers",
    "verify_authority_invariant",
    "LEDGER_PATHS",
    "main",
]
