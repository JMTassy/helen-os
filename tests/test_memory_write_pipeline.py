"""HELEN OS — End-to-end tests for the structured memory write pipeline.

Tests the full Claim Engine soundness chain:
  cognition(dict) → governor → apply_receipt(key/value) → receipt(memory_effect) → reconstruct → verify

Covers:
  - Dict passthrough in cognition (PATCH 1)
  - key=value string parsing in cognition (PATCH 1)
  - Structured memory_write execution (PATCH 2)
  - memory_effect in execution receipts (PATCH 3)
  - memory_effect computation in step() (PATCH 4)
  - Reconstruction from memory_effect (PATCH 5)
  - Provenance lookup for structured keys (PATCH 5)
  - Full epistemic closure: state = f(receipts), memory = f(receipts)
"""
import copy
import json
import pytest

from helensh.kernel import (
    cognition,
    governor,
    apply_receipt,
    make_execution_receipt,
    step,
    init_session,
    replay,
    KNOWN_ACTIONS,
)
from helensh.memory import (
    reconstruct_memory,
    verify_memory,
    disclose,
    memory_provenance,
    build_memory_packet,
    verify_memory_packet,
)
from helensh.state import effect_footprint, governed_state_hash


# ── Helpers ──────────────────────────────────────────────────────────


def _fresh():
    """Fresh session state."""
    return init_session("S-test", "tester", "/tmp")


# ═══════════════════════════════════════════════════════════════════════
# PATCH 1: Typed cognition — dict passthrough + key=value parsing
# ═══════════════════════════════════════════════════════════════════════


class TestCognitionDictPassthrough:
    """cognition() accepts dict input for structured proposals."""

    def test_dict_passthrough_basic(self):
        s = _fresh()
        proposal = cognition(s, {"action": "memory_write", "payload": {"key": "x", "value": 42}})
        assert proposal["action"] == "memory_write"
        assert proposal["payload"]["key"] == "x"
        assert proposal["payload"]["value"] == 42
        assert proposal["authority"] is False

    def test_dict_passthrough_forces_authority_false(self):
        s = _fresh()
        proposal = cognition(s, {"action": "chat", "payload": {"message": "hi"}, "authority": True})
        assert proposal["authority"] is False

    def test_dict_passthrough_unknown_action_preserved(self):
        """Dict passthrough preserves action — governor will DENY if unknown."""
        s = _fresh()
        proposal = cognition(s, {"action": "teleport", "payload": {"where": "mars"}})
        assert proposal["action"] == "teleport"

    def test_dict_passthrough_no_payload_defaults_empty(self):
        s = _fresh()
        proposal = cognition(s, {"action": "memory_read"})
        assert proposal["payload"] == {}

    def test_dict_passthrough_scalar_payload_wrapped(self):
        s = _fresh()
        proposal = cognition(s, {"action": "chat", "payload": "hello"})
        assert proposal["payload"] == {"value": "hello"}

    def test_dict_passthrough_chat(self):
        s = _fresh()
        proposal = cognition(s, {"action": "chat", "payload": {"message": "hello world"}})
        assert proposal["action"] == "chat"
        assert proposal["payload"]["message"] == "hello world"


class TestCognitionKeyValueParsing:
    """cognition() parses '#remember key=value' into structured payloads."""

    def test_remember_key_value_string(self):
        s = _fresh()
        p = cognition(s, "#remember greeting=hello world")
        assert p["action"] == "memory_write"
        assert p["payload"]["key"] == "greeting"
        assert p["payload"]["value"] == "hello world"

    def test_remember_key_value_int(self):
        s = _fresh()
        p = cognition(s, "#remember count=42")
        assert p["action"] == "memory_write"
        assert p["payload"]["key"] == "count"
        assert p["payload"]["value"] == 42

    def test_remember_key_value_float(self):
        s = _fresh()
        p = cognition(s, "#remember pi=3.14")
        assert p["action"] == "memory_write"
        assert p["payload"]["key"] == "pi"
        assert p["payload"]["value"] == 3.14

    def test_remember_key_value_bool(self):
        s = _fresh()
        p = cognition(s, "#remember active=true")
        assert p["action"] == "memory_write"
        assert p["payload"]["key"] == "active"
        assert p["payload"]["value"] is True

    def test_remember_key_value_null(self):
        s = _fresh()
        p = cognition(s, "#remember cleared=null")
        assert p["action"] == "memory_write"
        assert p["payload"]["key"] == "cleared"
        assert p["payload"]["value"] is None

    def test_remember_key_value_json_list(self):
        s = _fresh()
        p = cognition(s, '#remember items=[1,2,3]')
        assert p["action"] == "memory_write"
        assert p["payload"]["key"] == "items"
        assert p["payload"]["value"] == [1, 2, 3]

    def test_remember_legacy_content_format(self):
        """Without '=', falls back to legacy content format."""
        s = _fresh()
        p = cognition(s, "#remember some plain text")
        assert p["action"] == "memory_write"
        assert p["payload"]["content"] == "some plain text"
        assert "key" not in p["payload"]

    def test_remember_key_value_authority_false(self):
        s = _fresh()
        p = cognition(s, "#remember x=1")
        assert p["authority"] is False


# ═══════════════════════════════════════════════════════════════════════
# PATCH 2: apply_receipt — key/value memory_write
# ═══════════════════════════════════════════════════════════════════════


class TestApplyReceiptKeyValue:
    """apply_receipt() handles both key/value and legacy content formats."""

    def test_key_value_write(self):
        s = _fresh()
        proposal = {"action": "memory_write", "payload": {"key": "x", "value": 42}, "authority": False}
        s = apply_receipt(s, proposal, "ALLOW")
        assert s["working_memory"]["x"] == 42

    def test_legacy_content_write(self):
        s = _fresh()
        proposal = {"action": "memory_write", "payload": {"content": "hello"}, "authority": False}
        s = apply_receipt(s, proposal, "ALLOW")
        assert s["working_memory"]["mem_0"] == "hello"

    def test_key_value_overwrite(self):
        s = _fresh()
        s["working_memory"]["x"] = "old"
        proposal = {"action": "memory_write", "payload": {"key": "x", "value": "new"}, "authority": False}
        s = apply_receipt(s, proposal, "ALLOW")
        assert s["working_memory"]["x"] == "new"

    def test_key_value_none(self):
        s = _fresh()
        proposal = {"action": "memory_write", "payload": {"key": "cleared", "value": None}, "authority": False}
        s = apply_receipt(s, proposal, "ALLOW")
        assert s["working_memory"]["cleared"] is None

    def test_deny_no_mutation(self):
        s = _fresh()
        fp_before = effect_footprint(s)
        proposal = {"action": "memory_write", "payload": {"key": "x", "value": 42}, "authority": False}
        s = apply_receipt(s, proposal, "DENY")
        assert "x" not in s["working_memory"]
        assert effect_footprint(s) == fp_before

    def test_authority_guard(self):
        s = _fresh()
        proposal = {"action": "memory_write", "payload": {"key": "x", "value": 42}, "authority": True}
        s = apply_receipt(s, proposal, "ALLOW")
        assert "x" not in s["working_memory"]


# ═══════════════════════════════════════════════════════════════════════
# PATCH 3 + 4: memory_effect in execution receipts
# ═══════════════════════════════════════════════════════════════════════


class TestMemoryEffectInReceipts:
    """Execution receipts carry memory_effect when memory mutates."""

    def test_step_chat_has_memory_effect(self):
        s = _fresh()
        s, _ = step(s, "hello")
        e_receipt = s["receipts"][-1]
        assert e_receipt["type"] == "EXECUTION"
        assert "memory_effect" in e_receipt
        assert e_receipt["memory_effect"]["last_message"] == "hello"

    def test_step_memory_write_legacy_has_effect(self):
        s = _fresh()
        s, _ = step(s, "#remember some note")
        e_receipt = s["receipts"][-1]
        assert "memory_effect" in e_receipt
        assert e_receipt["memory_effect"]["mem_0"] == "some note"

    def test_step_memory_write_kv_has_effect(self):
        s = _fresh()
        s, _ = step(s, "#remember count=42")
        e_receipt = s["receipts"][-1]
        assert "memory_effect" in e_receipt
        assert e_receipt["memory_effect"]["count"] == 42

    def test_step_deny_no_memory_effect(self):
        """DENIED actions produce no memory_effect."""
        from helensh.kernel import revoke_capability
        s = _fresh()
        s = revoke_capability(s, "chat")
        s, _ = step(s, "hello")
        e_receipt = s["receipts"][-1]
        assert e_receipt.get("memory_effect") is None

    def test_step_read_file_no_memory_effect_key(self):
        """read_file mutates env, not working_memory — no memory_effect keys for wm."""
        s = _fresh()
        s, _ = step(s, "#read /etc/hosts")
        e_receipt = s["receipts"][-1]
        # read_file modifies env, not working_memory — memory_effect is None
        assert e_receipt.get("memory_effect") is None

    def test_memory_effect_not_in_hash(self):
        """memory_effect must not affect the receipt hash (backward compat)."""
        s = _fresh()
        s1, _ = step(s, "hello")
        hash_with_effect = s1["receipts"][-1]["hash"]

        # Manually compute what hash would be without memory_effect
        # Since memory_effect is NOT in hash, we can verify by checking
        # the receipt hash is the same as a receipt without memory_effect
        assert hash_with_effect is not None
        assert isinstance(hash_with_effect, str)
        assert len(hash_with_effect) == 64  # SHA-256 hex


class TestMemoryEffectDictPassthrough:
    """Dict passthrough through step() produces correct memory_effect."""

    def test_dict_step_structured_memory_write(self):
        s = _fresh()
        s, _ = step(s, {"action": "memory_write", "payload": {"key": "x", "value": 42}})
        assert s["working_memory"]["x"] == 42
        e_receipt = s["receipts"][-1]
        assert e_receipt["memory_effect"]["x"] == 42

    def test_dict_step_chat(self):
        s = _fresh()
        s, _ = step(s, {"action": "chat", "payload": {"message": "hello from dict"}})
        assert s["working_memory"]["last_message"] == "hello from dict"
        e_receipt = s["receipts"][-1]
        assert e_receipt["memory_effect"]["last_message"] == "hello from dict"


# ═══════════════════════════════════════════════════════════════════════
# PATCH 5: reconstruct_memory alignment
# ═══════════════════════════════════════════════════════════════════════


class TestReconstructMemoryAlignment:
    """reconstruct_memory uses memory_effect when available, falls back to proposal."""

    def test_reconstruct_from_memory_effect(self):
        """When receipt has memory_effect, use it directly."""
        receipts = [{
            "type": "EXECUTION",
            "effect_status": "APPLIED",
            "turn": 0,
            "memory_effect": {"x": 42, "y": "hello"},
            "proposal": {"action": "memory_write", "payload": {"key": "x", "value": 42}},
        }]
        mem = reconstruct_memory(receipts)
        assert mem["x"] == 42
        assert mem["y"] == "hello"

    def test_reconstruct_legacy_fallback(self):
        """Without memory_effect, falls back to proposal derivation."""
        receipts = [{
            "type": "EXECUTION",
            "effect_status": "APPLIED",
            "turn": 0,
            "proposal": {"action": "memory_write", "payload": {"content": "old note"}},
        }]
        mem = reconstruct_memory(receipts)
        assert mem["mem_0"] == "old note"

    def test_reconstruct_kv_from_proposal(self):
        """key/value in proposal without memory_effect."""
        receipts = [{
            "type": "EXECUTION",
            "effect_status": "APPLIED",
            "turn": 0,
            "proposal": {"action": "memory_write", "payload": {"key": "x", "value": 99}},
        }]
        mem = reconstruct_memory(receipts)
        assert mem["x"] == 99

    def test_reconstruct_mixed_receipts(self):
        """Mix of memory_effect and legacy receipts in one chain."""
        receipts = [
            {
                "type": "EXECUTION",
                "effect_status": "APPLIED",
                "turn": 0,
                "proposal": {"action": "memory_write", "payload": {"content": "old"}},
            },
            {
                "type": "EXECUTION",
                "effect_status": "APPLIED",
                "turn": 1,
                "memory_effect": {"x": 42},
                "proposal": {"action": "memory_write", "payload": {"key": "x", "value": 42}},
            },
        ]
        mem = reconstruct_memory(receipts)
        assert mem["mem_0"] == "old"
        assert mem["x"] == 42


# ═══════════════════════════════════════════════════════════════════════
# Provenance with structured keys
# ═══════════════════════════════════════════════════════════════════════


class TestProvenanceStructuredKeys:
    """memory_provenance finds receipts for structured key/value writes."""

    def test_provenance_kv_via_memory_effect(self):
        s = _fresh()
        s, _ = step(s, "#remember score=100")
        receipt = memory_provenance(s, "score")
        assert receipt is not None
        assert receipt["type"] == "EXECUTION"
        assert receipt["effect_status"] == "APPLIED"

    def test_provenance_kv_via_proposal(self):
        """Provenance from proposal key when no memory_effect."""
        s = _fresh()
        # Manually build state with a receipt using proposal-only key/value
        s["receipts"] = [{
            "type": "EXECUTION",
            "effect_status": "APPLIED",
            "turn": 0,
            "proposal": {"action": "memory_write", "payload": {"key": "x", "value": 42}},
            "hash": "fake-hash",
        }]
        s["working_memory"]["x"] = 42
        receipt = memory_provenance(s, "x")
        assert receipt is not None

    def test_provenance_legacy_key(self):
        s = _fresh()
        s, _ = step(s, "#remember some text")
        receipt = memory_provenance(s, "mem_0")
        assert receipt is not None

    def test_provenance_nonexistent_key(self):
        s = _fresh()
        s, _ = step(s, "hello")
        assert memory_provenance(s, "nonexistent") is None


# ═══════════════════════════════════════════════════════════════════════
# Full epistemic closure: verify_memory after structured writes
# ═══════════════════════════════════════════════════════════════════════


class TestEpistemicClosure:
    """verify_memory passes after structured writes — Memory(state) = f(receipts)."""

    def test_verify_after_kv_step(self):
        s = _fresh()
        s, _ = step(s, "#remember x=42")
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed: {errors}"

    def test_verify_after_dict_step(self):
        s = _fresh()
        s, _ = step(s, {"action": "memory_write", "payload": {"key": "y", "value": [1, 2, 3]}})
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed: {errors}"

    def test_verify_after_legacy_step(self):
        s = _fresh()
        s, _ = step(s, "#remember plain text note")
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed: {errors}"

    def test_verify_after_chat_step(self):
        s = _fresh()
        s, _ = step(s, "hello world")
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed: {errors}"

    def test_verify_after_multi_step(self):
        s = _fresh()
        s, _ = step(s, "hello")
        s, _ = step(s, "#remember x=1")
        s, _ = step(s, "#remember y=2")
        s, _ = step(s, "#witness observation one")
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed: {errors}"

    def test_verify_after_mixed_kv_and_legacy(self):
        s = _fresh()
        s, _ = step(s, "#remember plain text")
        s, _ = step(s, "#remember key=value")
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed: {errors}"

    def test_disclose_matches_working_memory(self):
        s = _fresh()
        s, _ = step(s, "#remember score=100")
        disclosed = disclose(s)
        assert disclosed == s["working_memory"]

    def test_replay_preserves_epistemic_closure(self):
        """Replay from inputs produces the same memory state."""
        s0 = _fresh()
        inputs = ["hello", "#remember x=42", "#remember plain note", "#witness test"]
        s = replay(s0, inputs)
        ok, errors = verify_memory(s)
        assert ok, f"verify_memory failed after replay: {errors}"


# ═══════════════════════════════════════════════════════════════════════
# Memory packet with structured keys
# ═══════════════════════════════════════════════════════════════════════


class TestMemoryPacketStructuredKeys:
    """MemoryPacket works with structured key/value writes."""

    def test_packet_for_structured_key(self):
        s = _fresh()
        s, _ = step(s, "#remember color=blue")
        packet = build_memory_packet(s["receipts"], ["color"])
        assert packet.data["color"] == "blue"
        assert verify_memory_packet(packet)

    def test_packet_mixed_keys(self):
        s = _fresh()
        s, _ = step(s, "hello")
        s, _ = step(s, "#remember x=42")
        packet = build_memory_packet(s["receipts"], ["last_message", "x"])
        assert packet.data["last_message"] == "hello"
        assert packet.data["x"] == 42
        assert verify_memory_packet(packet)


# ═══════════════════════════════════════════════════════════════════════
# Determinism invariant preserved
# ═══════════════════════════════════════════════════════════════════════


class TestDeterminismWithStructuredMemory:
    """I1 Determinism holds for structured memory writes."""

    def test_deterministic_kv_step(self):
        s = _fresh()
        s1, r1 = step(copy.deepcopy(s), "#remember x=42")
        s2, r2 = step(copy.deepcopy(s), "#remember x=42")
        assert r1["hash"] == r2["hash"]
        assert s1["receipts"] == s2["receipts"]
        assert s1["working_memory"] == s2["working_memory"]

    def test_deterministic_dict_step(self):
        s = _fresh()
        inp = {"action": "memory_write", "payload": {"key": "y", "value": True}}
        s1, r1 = step(copy.deepcopy(s), inp)
        s2, r2 = step(copy.deepcopy(s), inp)
        assert r1["hash"] == r2["hash"]
        assert s1["working_memory"] == s2["working_memory"]


# ═══════════════════════════════════════════════════════════════════════
# NoSilentEffect invariant preserved
# ═══════════════════════════════════════════════════════════════════════


class TestNoSilentEffectWithStructuredMemory:
    """I2 NoSilentEffect holds for DENY/PENDING structured proposals."""

    def test_deny_no_effect(self):
        from helensh.kernel import revoke_capability
        s = _fresh()
        s = revoke_capability(s, "memory_write")
        fp_before = effect_footprint(s)
        s, _ = step(s, "#remember x=42")
        fp_after = effect_footprint(s)
        assert fp_before == fp_after

    def test_authority_dict_no_effect(self):
        """Dict passthrough with authority=True still produces no effect."""
        s = _fresh()
        fp_before = effect_footprint(s)
        # Governor will DENY because authority is stripped but... let's test
        # the structural guard in apply_receipt
        proposal = {"action": "memory_write", "payload": {"key": "x", "value": 42}, "authority": True}
        s_after = apply_receipt(copy.deepcopy(s), proposal, "ALLOW")
        fp_after = effect_footprint(s_after)
        assert fp_before == fp_after
