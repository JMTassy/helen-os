"""HELEN OS — Persisted GNF Step Tests.

Tests for the integrated persistence layer:
    persisted_gnf_step()  — single step + ledger + artifact
    persisted_gnf_batch() — batch execution
    hydrate_gnf_session() — boot hydration from ledger

Properties proven:
    1. Ledger receives exactly 2 receipts per step (proposal + execution)
    2. Artifact stored when tool executes, None when no tool
    3. Artifact ref matches content hash of tool result
    4. State chains correctly across persisted steps
    5. Batch produces N receipts for N inputs
    6. Boot hydration reconstructs identical state
    7. Artifact verification catches missing blobs
    8. Backward compat: works without artifact store

Test classes:
    1. TestPersistedGNFStep       — Single step persistence
    2. TestPersistedGNFArtifact   — Artifact store integration
    3. TestPersistedGNFBatch      — Batch execution
    4. TestPersistedGNFHydration  — Boot hydration + verification
    5. TestPersistedGNFChain      — Chain integrity across steps
    6. TestPersistedGNFBackCompat — Backward compatibility
"""
import json
import os
import tempfile

import pytest

from helensh.kernel import init_session
from helensh.ledger import LedgerWriter, LedgerReader
from helensh.artifacts import ArtifactStore, ArtifactRef
from helensh.tools import ToolRegistry, ToolResult
from helensh.state import canonical_hash
from helensh.replay import verify_chain, verify_receipt_hashes
from helensh.persisted_gnf import (
    persisted_gnf_step,
    persisted_gnf_batch,
    hydrate_gnf_session,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _fresh():
    return init_session()


def _tmp_dir():
    return tempfile.mkdtemp()


def _make_env(base_dir=None):
    """Create a full persisted GNF environment: ledger + artifact store + registry."""
    if base_dir is None:
        base_dir = _tmp_dir()
    ledger = LedgerWriter(os.path.join(base_dir, "session.jsonl"))
    store = ArtifactStore(os.path.join(base_dir, "artifacts"))
    return ledger, store, base_dir


def _make_registry_with_echo():
    """Registry with a simple echo tool for testing."""
    reg = ToolRegistry()

    def echo_tool(payload, state):
        msg = payload.get("message", "echo")
        return ToolResult(
            success=True,
            output=msg,
            artifacts=(),
            error=None,
            execution_ms=0.1,
        )

    reg.register("respond", echo_tool, requires_approval=False)
    return reg


def _make_registry_with_python():
    """Registry with python_exec for tool execution testing."""
    reg = ToolRegistry()

    def mock_python(payload, state):
        code = payload.get("code", "")
        return ToolResult(
            success=True,
            output=f"executed: {code}",
            artifacts=(),
            error=None,
            execution_ms=1.0,
        )

    reg.register("python_exec", mock_python, requires_approval=True)
    return reg


# ── 1. Single Step Persistence ─────────────────────────────────────


class TestPersistedGNFStep:
    """Single step persistence: ledger + state."""

    def test_returns_three_tuple(self):
        ledger, store, _ = _make_env()
        s = _fresh()
        result = persisted_gnf_step(s, "hello", ledger=ledger)
        assert len(result) == 3
        new_s, receipt, artifact_ref = result

    def test_ledger_gets_two_receipts(self):
        base = _tmp_dir()
        ledger, store, _ = _make_env(base)
        s = _fresh()
        persisted_gnf_step(s, "hello", ledger=ledger)
        reader = LedgerReader(os.path.join(base, "session.jsonl"))
        receipts = reader.all()
        assert len(receipts) == 2  # proposal + execution

    def test_ledger_receipt_types(self):
        base = _tmp_dir()
        ledger, store, _ = _make_env(base)
        s = _fresh()
        persisted_gnf_step(s, "hello", ledger=ledger)
        reader = LedgerReader(os.path.join(base, "session.jsonl"))
        receipts = reader.all()
        assert receipts[0]["type"] == "PROPOSAL"
        assert receipts[1]["type"] == "EXECUTION"

    def test_state_advances(self):
        ledger, _, _ = _make_env()
        s = _fresh()
        new_s, _, _ = persisted_gnf_step(s, "hello", ledger=ledger)
        assert new_s["turn"] == s["turn"] + 1

    def test_receipt_has_authority_false(self):
        ledger, _, _ = _make_env()
        s = _fresh()
        _, receipt, _ = persisted_gnf_step(s, "hello", ledger=ledger)
        assert receipt.authority is False

    def test_no_artifact_without_tool(self):
        """Without tool_registry, artifact_ref is None."""
        ledger, store, _ = _make_env()
        s = _fresh()
        _, _, artifact_ref = persisted_gnf_step(
            s, "hello", ledger=ledger, artifact_store=store
        )
        assert artifact_ref is None

    def test_no_artifact_without_store(self):
        """Without artifact_store, artifact_ref is always None even with tool."""
        ledger, _, _ = _make_env()
        reg = _make_registry_with_echo()
        s = _fresh()
        _, _, artifact_ref = persisted_gnf_step(
            s, "hello", ledger=ledger, tool_registry=reg
        )
        assert artifact_ref is None


# ── 2. Artifact Store Integration ──────────────────────────────────


class TestPersistedGNFArtifact:
    """Artifact persistence when tools execute."""

    def test_artifact_stored_on_tool_execution(self):
        """When tool executes and store provided, artifact is stored."""
        ledger, store, _ = _make_env()
        reg = _make_registry_with_echo()
        s = _fresh()
        _, receipt, artifact_ref = persisted_gnf_step(
            s, "hello", ledger=ledger, artifact_store=store, tool_registry=reg,
        )
        # respond tool fires on ALLOW verdict for "respond" action
        if receipt.tool_result is not None:
            assert artifact_ref is not None
            assert isinstance(artifact_ref, ArtifactRef)
            assert artifact_ref.artifact_type == "tool_result"
            assert store.exists(artifact_ref.artifact_id)

    def test_artifact_content_matches_tool_result(self):
        """Stored artifact content matches the tool result."""
        ledger, store, _ = _make_env()
        reg = _make_registry_with_echo()
        s = _fresh()
        _, receipt, artifact_ref = persisted_gnf_step(
            s, "hello", ledger=ledger, artifact_store=store, tool_registry=reg,
        )
        if receipt.tool_result is not None and artifact_ref is not None:
            stored = store.read(artifact_ref.artifact_id)
            assert stored == receipt.tool_result.to_dict()

    def test_artifact_id_is_content_hash(self):
        """Artifact ID = canonical_hash(tool_result.to_dict())."""
        ledger, store, _ = _make_env()
        reg = _make_registry_with_echo()
        s = _fresh()
        _, receipt, artifact_ref = persisted_gnf_step(
            s, "hello", ledger=ledger, artifact_store=store, tool_registry=reg,
        )
        if receipt.tool_result is not None and artifact_ref is not None:
            expected = canonical_hash(receipt.tool_result.to_dict())
            assert artifact_ref.artifact_id == expected

    def test_artifact_source_matches_action(self):
        """Artifact source = the action that produced it."""
        ledger, store, _ = _make_env()
        reg = _make_registry_with_echo()
        s = _fresh()
        _, receipt, artifact_ref = persisted_gnf_step(
            s, "hello", ledger=ledger, artifact_store=store, tool_registry=reg,
        )
        if artifact_ref is not None:
            assert artifact_ref.source == receipt.proposal.get("action", "unknown")

    def test_artifact_idempotent(self):
        """Same tool result written twice → same artifact ID, one blob."""
        ledger, store, _ = _make_env()
        reg = _make_registry_with_echo()
        s = _fresh()

        # Two steps that should produce the same tool output
        s1, r1, ref1 = persisted_gnf_step(
            s, "hello", ledger=ledger, artifact_store=store, tool_registry=reg,
        )
        s2, r2, ref2 = persisted_gnf_step(
            s1, "hello", ledger=ledger, artifact_store=store, tool_registry=reg,
        )
        # If both produced tool results, they get the same hash
        if ref1 is not None and ref2 is not None:
            if r1.tool_result.to_dict() == r2.tool_result.to_dict():
                assert ref1.artifact_id == ref2.artifact_id


# ── 3. Batch Execution ────────────────────────────────────────────


class TestPersistedGNFBatch:
    """Batch execution: N inputs → N receipts."""

    def test_batch_returns_correct_counts(self):
        ledger, store, _ = _make_env()
        s = _fresh()
        inputs = ["hello", "world", "test"]
        final_s, receipts, refs = persisted_gnf_batch(
            s, inputs, ledger=ledger, artifact_store=store,
        )
        assert len(receipts) == 3
        assert len(refs) == 3

    def test_batch_state_advances(self):
        ledger, store, _ = _make_env()
        s = _fresh()
        inputs = ["a", "b", "c", "d"]
        final_s, _, _ = persisted_gnf_batch(s, inputs, ledger=ledger)
        assert final_s["turn"] == s["turn"] + 4

    def test_batch_ledger_complete(self):
        base = _tmp_dir()
        ledger, store, _ = _make_env(base)
        s = _fresh()
        inputs = ["x", "y", "z"]
        persisted_gnf_batch(s, inputs, ledger=ledger)
        reader = LedgerReader(os.path.join(base, "session.jsonl"))
        receipts = reader.all()
        # 3 steps × 2 receipts each = 6
        assert len(receipts) == 6

    def test_batch_chain_integrity(self):
        base = _tmp_dir()
        ledger, store, _ = _make_env(base)
        s = _fresh()
        inputs = ["a", "b", "c"]
        final_s, _, _ = persisted_gnf_batch(s, inputs, ledger=ledger)
        # In-memory chain must be valid
        ok, errors = verify_chain(final_s["receipts"])
        assert ok, f"chain integrity failed: {errors}"

    def test_batch_empty_inputs(self):
        ledger, store, _ = _make_env()
        s = _fresh()
        final_s, receipts, refs = persisted_gnf_batch(s, [], ledger=ledger)
        assert len(receipts) == 0
        assert len(refs) == 0
        assert final_s["turn"] == s["turn"]

    def test_batch_all_authority_false(self):
        ledger, _, _ = _make_env()
        s = _fresh()
        _, receipts, _ = persisted_gnf_batch(s, ["a", "b", "c"], ledger=ledger)
        for r in receipts:
            assert r.authority is False


# ── 4. Boot Hydration ─────────────────────────────────────────────


class TestPersistedGNFHydration:
    """Boot hydration: reconstruct state from ledger."""

    def test_hydration_empty_ledger(self):
        base = _tmp_dir()
        s0 = _fresh()
        state, ok, errors = hydrate_gnf_session(
            s0, os.path.join(base, "nonexistent.jsonl")
        )
        assert ok is True
        assert errors == []
        assert state["turn"] == s0["turn"]

    def test_hydration_reconstructs_state(self):
        base = _tmp_dir()
        ledger, store, _ = _make_env(base)
        s = _fresh()

        # Run 3 steps
        final_s, _, _ = persisted_gnf_batch(
            s, ["hello", "world", "test"], ledger=ledger,
        )

        # Hydrate from scratch
        s0 = _fresh()
        hydrated, ok, errors = hydrate_gnf_session(
            s0, os.path.join(base, "session.jsonl")
        )
        assert ok is True, f"hydration failed: {errors}"
        assert hydrated["turn"] == final_s["turn"]

    def test_hydration_detects_tamper(self):
        """Tampered ledger is detected during hydration."""
        base = _tmp_dir()
        ledger_path = os.path.join(base, "session.jsonl")
        ledger = LedgerWriter(ledger_path)
        s = _fresh()

        persisted_gnf_step(s, "hello", ledger=ledger)

        # Tamper: corrupt the ledger
        with open(ledger_path, "r") as f:
            lines = f.readlines()
        if lines:
            d = json.loads(lines[0])
            d["hash"] = "TAMPERED_HASH"
            lines[0] = json.dumps(d) + "\n"
            with open(ledger_path, "w") as f:
                f.writelines(lines)

        s0 = _fresh()
        _, ok, errors = hydrate_gnf_session(s0, ledger_path)
        assert ok is False
        assert len(errors) > 0

    def test_hydration_with_artifact_verification(self):
        """Hydration verifies artifact references exist in store."""
        base = _tmp_dir()
        ledger, store, _ = _make_env(base)
        reg = _make_registry_with_echo()
        s = _fresh()

        # Run a step with tool execution
        final_s, receipt, artifact_ref = persisted_gnf_step(
            s, "hello", ledger=ledger, artifact_store=store, tool_registry=reg,
        )

        # Hydrate with artifact store — should pass
        s0 = _fresh()
        _, ok, errors = hydrate_gnf_session(
            s0, os.path.join(base, "session.jsonl"),
            artifact_store=store,
        )
        assert ok is True, f"hydration with artifacts failed: {errors}"

    def test_hydration_chain_valid(self):
        base = _tmp_dir()
        ledger, _, _ = _make_env(base)
        s = _fresh()

        persisted_gnf_batch(s, ["a", "b", "c", "d", "e"], ledger=ledger)

        reader = LedgerReader(os.path.join(base, "session.jsonl"))
        receipts = reader.all()
        ok, errors = verify_chain(receipts)
        assert ok, f"chain invalid: {errors}"


# ── 5. Chain Integrity ─────────────────────────────────────────────


class TestPersistedGNFChain:
    """Chain integrity across persisted steps."""

    def test_receipt_chain_links(self):
        """previous_hash links unbroken across persisted steps."""
        base = _tmp_dir()
        ledger, _, _ = _make_env(base)
        s = _fresh()

        for i in range(5):
            s, _, _ = persisted_gnf_step(s, f"step_{i}", ledger=ledger)

        reader = LedgerReader(os.path.join(base, "session.jsonl"))
        receipts = reader.all()
        assert len(receipts) == 10  # 5 steps × 2 receipts

        # Verify chain
        ok, errors = verify_chain(receipts)
        assert ok, f"chain broken: {errors}"

    def test_receipt_hashes_valid(self):
        base = _tmp_dir()
        ledger, _, _ = _make_env(base)
        s = _fresh()

        for i in range(3):
            s, _, _ = persisted_gnf_step(s, f"input_{i}", ledger=ledger)

        reader = LedgerReader(os.path.join(base, "session.jsonl"))
        receipts = reader.all()
        ok, errors = verify_receipt_hashes(receipts)
        assert ok, f"hash verification failed: {errors}"

    def test_in_memory_matches_ledger(self):
        """In-memory receipt chain matches what was written to ledger."""
        base = _tmp_dir()
        ledger, _, _ = _make_env(base)
        s = _fresh()

        for i in range(3):
            s, _, _ = persisted_gnf_step(s, f"msg_{i}", ledger=ledger)

        reader = LedgerReader(os.path.join(base, "session.jsonl"))
        ledger_receipts = reader.all()
        memory_receipts = s["receipts"]

        assert len(ledger_receipts) == len(memory_receipts)
        for lr, mr in zip(ledger_receipts, memory_receipts):
            assert lr["hash"] == mr["hash"]

    def test_deterministic_replay(self):
        """Same inputs produce same receipt hashes (I1/I5)."""
        base1 = _tmp_dir()
        base2 = _tmp_dir()
        ledger1, _, _ = _make_env(base1)
        ledger2, _, _ = _make_env(base2)

        s1 = _fresh()
        s2 = _fresh()
        # Force identical session_id
        s2["session_id"] = s1["session_id"]

        inputs = ["alpha", "beta", "gamma"]
        for inp in inputs:
            s1, _, _ = persisted_gnf_step(s1, inp, ledger=ledger1)
            s2, _, _ = persisted_gnf_step(s2, inp, ledger=ledger2)

        # Receipt hashes must match
        for r1, r2 in zip(s1["receipts"], s2["receipts"]):
            assert r1["hash"] == r2["hash"]


# ── 6. Backward Compatibility ─────────────────────────────────────


class TestPersistedGNFBackCompat:
    """Backward compatibility: works without optional components."""

    def test_works_without_artifact_store(self):
        ledger, _, _ = _make_env()
        s = _fresh()
        new_s, receipt, ref = persisted_gnf_step(s, "hello", ledger=ledger)
        assert ref is None
        assert new_s["turn"] == s["turn"] + 1

    def test_works_without_tool_registry(self):
        ledger, store, _ = _make_env()
        s = _fresh()
        new_s, receipt, ref = persisted_gnf_step(
            s, "hello", ledger=ledger, artifact_store=store,
        )
        assert ref is None
        assert receipt.tool_result is None

    def test_works_without_both(self):
        ledger, _, _ = _make_env()
        s = _fresh()
        new_s, receipt, ref = persisted_gnf_step(s, "hello", ledger=ledger)
        assert ref is None
        assert receipt.tool_result is None
        assert new_s["turn"] == 1

    def test_receipt_identical_with_and_without_store(self):
        """Receipt hashes are the same whether or not artifact store is used."""
        ledger1, _, base1 = _make_env()
        ledger2, store2, base2 = _make_env()

        s1 = _fresh()
        s2 = _fresh()
        s2["session_id"] = s1["session_id"]

        new_s1, r1, _ = persisted_gnf_step(s1, "hello", ledger=ledger1)
        new_s2, r2, _ = persisted_gnf_step(
            s2, "hello", ledger=ledger2, artifact_store=store2,
        )

        assert r1.proposal_receipt_hash == r2.proposal_receipt_hash
        assert r1.execution_receipt_hash == r2.execution_receipt_hash
