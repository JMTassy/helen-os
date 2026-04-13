"""Tests for MemoryPacket — PR #2 Memory Disclosure V1 (STRICT).

Core law:
  READ => receipt-bound ∧ explicit ∧ verifiable

Tests verify:
  - MemoryPacket construction from receipts + requested keys
  - Scoped reads: only requested keys appear in packet
  - Determinism: same receipts + keys -> same packet
  - Tamper detection: modified receipt_hashes -> verify fails
  - Tamper detection: modified data -> verify fails
  - Merkle root seals contributing receipts
  - Empty keys return empty data
  - No ambient state access (packet is self-contained)
  - Frozen packet (immutable)
"""
import copy
import pytest

from helensh.kernel import init_session, step, replay
from helensh.memory import (
    MemoryPacket,
    build_memory_packet,
    verify_memory_packet,
    reconstruct_memory,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return init_session(session_id="S-packet-test")


@pytest.fixture
def receipts_chat(s0):
    s = replay(s0, ["hello", "world"])
    return s["receipts"]


@pytest.fixture
def receipts_mixed(s0):
    s = replay(s0, ["hello", "#remember important data", "#recall", "goodbye"])
    return s["receipts"]


# ── Construction ─────────────────────────────────────────────────────


class TestMemoryPacketConstruction:
    def test_returns_memory_packet(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        assert isinstance(p, MemoryPacket)

    def test_scope_matches_requested_keys(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        assert p.scope == ("last_message",)

    def test_data_has_requested_key(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        assert "last_message" in p.data
        assert p.data["last_message"] == "world"

    def test_missing_key_excluded(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["nonexistent"])
        assert p.data == {}

    def test_packet_id_is_string(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        assert isinstance(p.packet_id, str)
        assert len(p.packet_id) > 0

    def test_merkle_root_is_hex(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        assert len(p.merkle_root) == 64
        assert all(c in "0123456789abcdef" for c in p.merkle_root)

    def test_packet_hash_is_hex(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        assert len(p.packet_hash) == 64

    def test_frozen(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        with pytest.raises(AttributeError):
            p.data = {"tampered": True}


# ── Scoped Reads ─────────────────────────────────────────────────────


class TestScopedReads:
    def test_single_key(self, receipts_mixed):
        p = build_memory_packet(receipts_mixed, ["last_message"])
        assert "last_message" in p.data
        assert "mem_1" not in p.data

    def test_multiple_keys(self, receipts_mixed):
        p = build_memory_packet(receipts_mixed, ["last_message", "mem_1"])
        assert p.data["last_message"] == "goodbye"
        assert "important data" in p.data["mem_1"]

    def test_scope_sorted(self, receipts_mixed):
        p = build_memory_packet(receipts_mixed, ["mem_1", "last_message"])
        assert p.scope == ("last_message", "mem_1")

    def test_only_contributing_receipts(self, receipts_mixed):
        p = build_memory_packet(receipts_mixed, ["mem_1"])
        # Only the memory_write execution receipt should contribute
        assert len(p.receipt_hashes) >= 1

    def test_empty_keys_empty_data(self, receipts_mixed):
        p = build_memory_packet(receipts_mixed, [])
        assert p.data == {}
        assert p.receipt_hashes == ()

    def test_empty_receipts(self):
        p = build_memory_packet([], ["last_message"])
        assert p.data == {}
        assert p.packet_id == "GENESIS"


# ── Determinism ──────────────────────────────────────────────────────


class TestPacketDeterminism:
    def test_same_inputs_same_packet(self, receipts_chat):
        p1 = build_memory_packet(receipts_chat, ["last_message"])
        p2 = build_memory_packet(receipts_chat, ["last_message"])
        assert p1.packet_hash == p2.packet_hash
        assert p1.merkle_root == p2.merkle_root
        assert p1.data == p2.data

    def test_replayed_state_same_packet(self, s0):
        s1 = replay(s0, ["hello", "world"])
        s2 = replay(s0, ["hello", "world"])
        p1 = build_memory_packet(s1["receipts"], ["last_message"])
        p2 = build_memory_packet(s2["receipts"], ["last_message"])
        assert p1 == p2

    def test_different_keys_different_packet(self, receipts_mixed):
        p1 = build_memory_packet(receipts_mixed, ["last_message"])
        p2 = build_memory_packet(receipts_mixed, ["mem_1"])
        assert p1.packet_hash != p2.packet_hash


# ── Verification ─────────────────────────────────────────────────────


class TestPacketVerification:
    def test_valid_packet_verifies(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        assert verify_memory_packet(p) is True

    def test_all_packet_types_verify(self, receipts_mixed):
        for keys in [["last_message"], ["mem_1"], ["last_message", "mem_1"]]:
            p = build_memory_packet(receipts_mixed, keys)
            assert verify_memory_packet(p), f"Failed for keys={keys}"

    def test_empty_packet_verifies(self):
        p = build_memory_packet([], [])
        assert verify_memory_packet(p) is True


# ── Tamper Detection ─────────────────────────────────────────────────


class TestPacketTamperDetection:
    def test_tampered_receipt_hash_fails(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        # Create tampered packet with modified receipt hash
        tampered = MemoryPacket(
            packet_id=p.packet_id,
            scope=p.scope,
            receipt_hashes=("fake_hash",),
            data=p.data,
            merkle_root=p.merkle_root,
            packet_hash=p.packet_hash,
        )
        assert verify_memory_packet(tampered) is False

    def test_tampered_merkle_root_fails(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        tampered = MemoryPacket(
            packet_id=p.packet_id,
            scope=p.scope,
            receipt_hashes=p.receipt_hashes,
            data=p.data,
            merkle_root="0" * 64,
            packet_hash=p.packet_hash,
        )
        assert verify_memory_packet(tampered) is False

    def test_tampered_data_fails(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        tampered = MemoryPacket(
            packet_id=p.packet_id,
            scope=p.scope,
            receipt_hashes=p.receipt_hashes,
            data={"last_message": "TAMPERED"},
            merkle_root=p.merkle_root,
            packet_hash=p.packet_hash,
        )
        assert verify_memory_packet(tampered) is False

    def test_tampered_scope_fails(self, receipts_chat):
        p = build_memory_packet(receipts_chat, ["last_message"])
        tampered = MemoryPacket(
            packet_id=p.packet_id,
            scope=("tampered_key",),
            receipt_hashes=p.receipt_hashes,
            data=p.data,
            merkle_root=p.merkle_root,
            packet_hash=p.packet_hash,
        )
        assert verify_memory_packet(tampered) is False


# ── Integration ──────────────────────────────────────────────────────


class TestPacketIntegration:
    def test_packet_data_matches_reconstruction(self, receipts_mixed):
        full_mem = reconstruct_memory(receipts_mixed)
        keys = list(full_mem.keys())
        p = build_memory_packet(receipts_mixed, keys)
        assert p.data == full_mem

    def test_packet_after_every_step(self, s0):
        """Build and verify a packet after each step."""
        inputs = ["hello", "#remember x", "#recall", "world"]
        s = copy.deepcopy(s0)
        for u in inputs:
            s, _ = step(s, u)
            mem = reconstruct_memory(s["receipts"])
            keys = list(mem.keys())
            if keys:
                p = build_memory_packet(s["receipts"], keys)
                assert verify_memory_packet(p), f"Failed after '{u}'"

    def test_packet_with_task_keys(self, s0):
        """MemoryPacket works with task_create keys."""
        s = replay(s0, ["#task design governor gate"])
        p = build_memory_packet(s["receipts"], ["task_0"])
        assert verify_memory_packet(p)
        assert "task_0" in p.data
