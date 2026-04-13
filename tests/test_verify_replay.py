"""Tests for Replay Safety Verification — Lock 4.

Law:
  If the ledger cannot be replayed, the path is not replay-safe.

Tests verify:
  - verify_authority_invariant checks authority: false on all receipts
  - verify_ledger handles MISSING, EMPTY, and populated ledgers
  - verify_ledger detects tampered chains
  - verify_all_ledgers scans all known paths
  - Module can be run as entrypoint
"""
import json
import pytest
from pathlib import Path

from helensh.kernel import init_session, step, GENESIS_HASH
from helensh.ledger import LedgerWriter
from helensh.verify_replay_safety import (
    verify_authority_invariant,
    verify_ledger,
    verify_all_ledgers,
    LEDGER_PATHS,
    main,
)


# ── Fixtures ──────────────────────────────────────────────────────────


# Use "replay-verify" as session_id to match verify_ledger default
VERIFY_SESSION_ID = "replay-verify"


@pytest.fixture
def s0():
    return init_session(session_id=VERIFY_SESSION_ID)


@pytest.fixture
def valid_ledger(tmp_path, s0):
    """Write a valid 3-step ledger."""
    path = tmp_path / "test_ledger.jsonl"
    writer = LedgerWriter(str(path))
    s = s0
    for u in ["hello", "world", "#remember test"]:
        s, p = step(s, u)
        e = s["receipts"][-1]
        writer.append_step(p, e)
    return path


@pytest.fixture
def tampered_ledger(tmp_path, s0):
    """Write a ledger then tamper with a receipt hash."""
    path = tmp_path / "tampered_ledger.jsonl"
    writer = LedgerWriter(str(path))
    s = s0
    for u in ["hello", "world"]:
        s, p = step(s, u)
        e = s["receipts"][-1]
        writer.append_step(p, e)

    # Read, tamper, rewrite
    lines = path.read_text().strip().split("\n")
    receipt = json.loads(lines[0])
    receipt["hash"] = "0" * 64  # tamper
    lines[0] = json.dumps(receipt)
    path.write_text("\n".join(lines) + "\n")
    return path


# ── Authority Invariant ───────────────────────────────────────────────


class TestAuthorityInvariant:
    def test_valid_receipts_pass(self, s0):
        s, _ = step(s0, "hello")
        ok, errors = verify_authority_invariant(s["receipts"])
        assert ok is True
        assert errors == []

    def test_tampered_authority_fails(self, s0):
        s, _ = step(s0, "hello")
        receipts = list(s["receipts"])
        receipts[0]["authority"] = True  # tamper
        ok, errors = verify_authority_invariant(receipts)
        assert ok is False
        assert len(errors) >= 1

    def test_empty_receipts_pass(self):
        ok, errors = verify_authority_invariant([])
        assert ok is True


# ── Verify Single Ledger ─────────────────────────────────────────────


class TestVerifyLedger:
    def test_missing_ledger(self, tmp_path):
        result = verify_ledger("test", tmp_path / "nonexistent.jsonl")
        assert result["status"] == "MISSING"
        assert result["exists"] is False

    def test_empty_ledger(self, tmp_path):
        path = tmp_path / "empty.jsonl"
        path.write_text("")
        result = verify_ledger("test", path)
        assert result["status"] == "EMPTY"
        assert result["chain_ok"] is True

    def test_valid_ledger_passes(self, valid_ledger):
        result = verify_ledger("test", valid_ledger)
        assert result["status"] == "OK"
        assert result["chain_ok"] is True
        assert result["hash_ok"] is True
        assert result["authority_ok"] is True
        assert result["replay_ok"] is True
        assert result["receipt_count"] == 6  # 3 steps × 2 receipts

    def test_tampered_ledger_fails(self, tampered_ledger):
        result = verify_ledger("test", tampered_ledger)
        assert result["status"] == "INTEGRITY_FAILURE"
        assert len(result["errors"]) > 0

    def test_result_structure(self, valid_ledger):
        result = verify_ledger("test", valid_ledger)
        assert "name" in result
        assert "path" in result
        assert "exists" in result
        assert "receipt_count" in result
        assert "chain_ok" in result
        assert "hash_ok" in result
        assert "authority_ok" in result
        assert "replay_ok" in result
        assert "errors" in result
        assert "status" in result


# ── Verify All Ledgers ───────────────────────────────────────────────


class TestVerifyAllLedgers:
    def test_returns_list(self):
        results = verify_all_ledgers()
        assert isinstance(results, list)

    def test_covers_all_known_paths(self):
        results = verify_all_ledgers()
        names = {r["name"] for r in results}
        assert names == set(LEDGER_PATHS.keys())

    def test_known_paths_exist(self):
        """LEDGER_PATHS has the expected entries."""
        assert "web_api" in LEDGER_PATHS
        assert "boot" in LEDGER_PATHS
        assert "live" in LEDGER_PATHS


# ── Module Entry Point ───────────────────────────────────────────────


class TestModuleEntryPoint:
    def test_main_returns_int(self):
        """main() returns 0 or 1 (does not crash)."""
        result = main()
        assert result in (0, 1)
