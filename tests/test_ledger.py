"""Tests for helensh ledger — persisted receipt ledger + boot hydration.

Invariant coverage via ledger:
  I4  ChainIntegrity       verify_chain passes on hydrated receipts
  I5  ByteStableReplay     hydrated state matches in-memory replay
  I9  ReplayVerification   hydrate_session re-runs rebuild_and_verify logic
  TMP TamperDetection      mutated ledger line detected on hydration
"""
import copy
import json
import pytest

from helensh.kernel import (
    GENESIS_HASH,
    init_session,
    replay,
    revoke_capability,
    step,
)
from helensh.ledger import (
    LedgerReader,
    LedgerWriter,
    hydrate_session,
    persisted_step,
)
from helensh.replay import verify_chain, verify_receipt_hashes
from helensh.state import governed_state_hash


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return init_session(session_id="S-ledger", user="tester", root="/test")


@pytest.fixture
def ledger_path(tmp_path):
    return tmp_path / "receipts.ndjson"


# ── LedgerWriter basics ───────────────────────────────────────────────


class TestLedgerWriter:
    def test_creates_file_on_first_append(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        assert not ledger_path.exists()
        s1, p = step(s0, "hello")
        e = s1["receipts"][-1]
        writer.append_step(p, e)
        assert ledger_path.exists()

    def test_file_has_correct_line_count(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        for msg in ["a", "b", "c"]:
            s0, p = step(s0, msg)
            e = s0["receipts"][-1]
            writer.append_step(p, e)
        lines = ledger_path.read_text().strip().splitlines()
        assert len(lines) == 6  # 2 per step

    def test_each_line_is_valid_json(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s1, p = step(s0, "hello")
        e = s1["receipts"][-1]
        writer.append_step(p, e)
        for line in ledger_path.read_text().strip().splitlines():
            obj = json.loads(line)
            assert isinstance(obj, dict)

    def test_creates_parent_dirs(self, tmp_path):
        deep_path = tmp_path / "a" / "b" / "c" / "receipts.ndjson"
        writer = LedgerWriter(deep_path)
        assert deep_path.parent.exists()

    def test_canonical_round_trip(self, s0, ledger_path):
        """Receipt written then read must equal the original."""
        writer = LedgerWriter(ledger_path)
        s1, p = step(s0, "hello")
        e = s1["receipts"][-1]
        writer.append_step(p, e)

        reader = LedgerReader(ledger_path)
        stored = reader.all()
        assert stored[0]["hash"] == p["hash"]
        assert stored[1]["hash"] == e["hash"]


# ── LedgerReader basics ───────────────────────────────────────────────


class TestLedgerReader:
    def test_empty_if_file_missing(self, tmp_path):
        reader = LedgerReader(tmp_path / "nonexistent.ndjson")
        assert reader.all() == []

    def test_len_matches_line_count(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        for msg in ["x", "y"]:
            s0, p = step(s0, msg)
            writer.append_step(p, s0["receipts"][-1])
        reader = LedgerReader(ledger_path)
        assert len(reader) == 4

    def test_iteration_yields_dicts(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s1, p = step(s0, "hello")
        writer.append_step(p, s1["receipts"][-1])
        for r in LedgerReader(ledger_path):
            assert isinstance(r, dict)
            assert "hash" in r


# ── persisted_step ────────────────────────────────────────────────────


class TestPersistedStep:
    def test_returns_same_as_step(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s_direct, p_direct = step(copy.deepcopy(s0), "hello")
        s_pers, p_pers = persisted_step(copy.deepcopy(s0), "hello", writer)
        assert p_direct["hash"] == p_pers["hash"]
        assert governed_state_hash(s_direct) == governed_state_hash(s_pers)

    def test_writes_two_receipts_per_call(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        persisted_step(s0, "hello", writer)
        assert len(LedgerReader(ledger_path)) == 2

    def test_multiple_calls_accumulate(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        for msg in ["a", "b", "c"]:
            s, _ = persisted_step(s, msg, writer)
        assert len(LedgerReader(ledger_path)) == 6

    def test_persisted_chain_verifies(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        for msg in ["hello", "world", "#recall"]:
            s, _ = persisted_step(s, msg, writer)
        receipts = LedgerReader(ledger_path).all()
        ok, errors = verify_chain(receipts)
        assert ok, f"Chain errors: {errors}"

    def test_persisted_hashes_verify(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        for msg in ["a", "b"]:
            s, _ = persisted_step(s, msg, writer)
        receipts = LedgerReader(ledger_path).all()
        ok, errors = verify_receipt_hashes(receipts)
        assert ok, f"Hash errors: {errors}"

    def test_persisted_receipt_hashes_match_in_memory(self, s0, ledger_path):
        """Ledger hashes must equal in-memory state hashes (byte-stable replay)."""
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        for msg in ["hello", "world"]:
            s, _ = persisted_step(s, msg, writer)
        ledger_receipts = LedgerReader(ledger_path).all()
        for ledger_r, mem_r in zip(ledger_receipts, s["receipts"]):
            assert ledger_r["hash"] == mem_r["hash"]

    def test_deny_still_persisted(self, s0, ledger_path):
        """DENY receipts must be written to the ledger (chain must not skip)."""
        s = revoke_capability(s0, "chat")
        writer = LedgerWriter(ledger_path)
        s1, r = persisted_step(s, "hello", writer)
        assert r["verdict"] == "DENY"
        receipts = LedgerReader(ledger_path).all()
        assert len(receipts) == 2
        ok, errors = verify_chain(receipts)
        assert ok, errors


# ── hydrate_session ───────────────────────────────────────────────────


class TestHydrateSession:
    def test_empty_ledger_returns_initial(self, s0, ledger_path):
        state, ok, errors = hydrate_session(s0, ledger_path)
        assert ok
        assert errors == []
        assert governed_state_hash(state) == governed_state_hash(s0)

    def test_hydrated_state_matches_replay(self, s0, ledger_path):
        """State from hydration must equal state from in-memory replay."""
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        inputs = ["hello", "world", "#recall", "#remember key thing"]
        for msg in inputs:
            s, _ = persisted_step(s, msg, writer)

        hydrated, ok, errors = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert ok, f"Hydration errors: {errors}"
        assert governed_state_hash(hydrated) == governed_state_hash(s)

    def test_hydrated_receipt_count(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        n = 4
        for i in range(n):
            s, _ = persisted_step(s, f"msg {i}", writer)
        hydrated, ok, _ = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert ok
        assert len(hydrated["receipts"]) == 2 * n

    def test_hydrated_chain_verifies(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        for msg in ["a", "b", "c"]:
            s, _ = persisted_step(s, msg, writer)
        hydrated, ok, errors = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert ok, errors
        chain_ok, chain_errors = verify_chain(hydrated["receipts"])
        assert chain_ok, chain_errors

    def test_hydrated_turn_correct(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        n = 3
        for i in range(n):
            s, _ = persisted_step(s, f"turn {i}", writer)
        hydrated, ok, _ = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert ok
        assert hydrated["turn"] == n

    def test_hydrated_working_memory(self, s0, ledger_path):
        """Working memory mutations survive persist → hydrate round-trip."""
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        s, _ = persisted_step(s, "hello there", writer)
        hydrated, ok, _ = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert ok
        assert hydrated["working_memory"].get("last_message") == "hello there"

    def test_hydrated_genesis_link(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        s, _ = persisted_step(s, "hello", writer)
        hydrated, ok, _ = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert ok
        assert hydrated["receipts"][0]["previous_hash"] == GENESIS_HASH

    def test_multi_session_accumulation(self, s0, ledger_path):
        """Simulate two sessions writing to the same ledger (e.g. append resume)."""
        # Session A: 2 steps
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        for msg in ["step1", "step2"]:
            s, _ = persisted_step(s, msg, writer)

        # Session B: hydrate then continue
        s_resumed, ok, errors = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert ok, errors

        writer2 = LedgerWriter(ledger_path)
        for msg in ["step3", "step4"]:
            s_resumed, _ = persisted_step(s_resumed, msg, writer2)

        # Full ledger now has 4 steps (8 receipts)
        receipts = LedgerReader(ledger_path).all()
        assert len(receipts) == 8
        chain_ok, chain_errors = verify_chain(receipts)
        assert chain_ok, chain_errors


# ── Tamper detection ─────────────────────────────────────────────────


class TestTamperDetection:
    def test_mutated_verdict_detected(self, s0, ledger_path):
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        s, _ = persisted_step(s, "hello", writer)

        # Corrupt the first receipt's verdict in the file
        lines = ledger_path.read_text().splitlines()
        obj = json.loads(lines[0])
        obj["verdict"] = "ALLOW_TAMPERED"
        lines[0] = json.dumps(obj)
        ledger_path.write_text("\n".join(lines) + "\n")

        _, ok, errors = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert not ok
        assert errors

    def test_mutated_hash_chain_detected(self, s0, ledger_path):
        """Changing a previous_hash link breaks chain verification."""
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        for msg in ["a", "b"]:
            s, _ = persisted_step(s, msg, writer)

        lines = ledger_path.read_text().splitlines()
        obj = json.loads(lines[2])  # third receipt
        obj["previous_hash"] = "deadbeef" * 8
        lines[2] = json.dumps(obj)
        ledger_path.write_text("\n".join(lines) + "\n")

        _, ok, errors = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert not ok
        assert errors

    def test_deleted_receipt_detected(self, s0, ledger_path):
        """Dropping a receipt from the chain breaks chain verification."""
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        for msg in ["a", "b"]:
            s, _ = persisted_step(s, msg, writer)

        lines = ledger_path.read_text().splitlines()
        # Drop the execution receipt from step 1 (line index 1)
        lines.pop(1)
        ledger_path.write_text("\n".join(lines) + "\n")

        _, ok, errors = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert not ok
        assert errors

    def test_appended_fake_receipt_detected(self, s0, ledger_path):
        """A forged receipt appended to the ledger fails hash verification."""
        writer = LedgerWriter(ledger_path)
        s = copy.deepcopy(s0)
        s, _ = persisted_step(s, "hello", writer)

        # Forge a receipt with a wrong hash
        forged = dict(s["receipts"][-1])
        forged["verdict"] = "ALLOW"
        forged["hash"] = "0" * 64  # wrong hash

        with ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(forged) + "\n")

        _, ok, errors = hydrate_session(copy.deepcopy(s0), ledger_path)
        assert not ok
        assert errors
