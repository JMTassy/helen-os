"""
HELEN OS Memory Hydration V1 — Test Suite

4 mandatory invariants (T1-T4) from the architecture plan,
plus persistence, reconstruction, and edge case coverage.
"""

import json
import os
import pytest
import tempfile

from helen_os.memory_hydration import (
    MemoryPacket, emit_boot_memory, verify_packet,
    persist_packet, load_packet, load_and_verify,
    verify_chain, reconstruct_boot_context,
    GENESIS_DISCLOSURE_HASH, _hash, _canonical,
)


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def sample_threads():
    return [
        {"id": "t1", "title": "Build kernel", "memory_class": "committed", "next_action": "test"},
        {"id": "t2", "title": "Deploy API", "memory_class": "working", "unresolved": "CI red"},
    ]


@pytest.fixture
def sample_tensions():
    return [{"thread": "Deploy API", "issue": "CI red"}]


@pytest.fixture
def sample_memory():
    return [{"text": "authority=NONE always", "source": "SYSTEM"}]


@pytest.fixture
def sample_packet(sample_threads, sample_tensions, sample_memory):
    return emit_boot_memory(
        session_id="test-session",
        threads=sample_threads,
        tensions=sample_tensions,
        committed_memory=sample_memory,
        next_action="Fix CI",
    )


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


# ===================================================================
# T1 — DETERMINISTIC PACKET HASH (mandatory)
# ===================================================================

class TestT1DeterministicHash:
    def test_same_inputs_same_hash(self, sample_threads, sample_tensions, sample_memory):
        """T1: emit_boot_memory with identical inputs produces identical receipt_hash."""
        p1 = emit_boot_memory("s1", threads=sample_threads, tensions=sample_tensions,
                              committed_memory=sample_memory, next_action="Fix CI")
        p2 = emit_boot_memory("s1", threads=sample_threads, tensions=sample_tensions,
                              committed_memory=sample_memory, next_action="Fix CI")
        assert p1.receipt_hash == p2.receipt_hash

    def test_payload_hash_stable(self, sample_threads):
        p1 = emit_boot_memory("s1", threads=sample_threads)
        p2 = emit_boot_memory("s1", threads=sample_threads)
        assert p1.payload_hash == p2.payload_hash

    def test_different_session_different_hash(self, sample_threads):
        p1 = emit_boot_memory("session-A", threads=sample_threads)
        p2 = emit_boot_memory("session-B", threads=sample_threads)
        assert p1.receipt_hash != p2.receipt_hash

    def test_different_payload_different_hash(self):
        p1 = emit_boot_memory("s1", next_action="A")
        p2 = emit_boot_memory("s1", next_action="B")
        assert p1.receipt_hash != p2.receipt_hash

    def test_20_runs_all_identical(self, sample_threads):
        hashes = set()
        for _ in range(20):
            p = emit_boot_memory("s1", threads=sample_threads, next_action="test")
            hashes.add(p.receipt_hash)
        assert len(hashes) == 1


# ===================================================================
# T2 — TAMPER DETECTION (mandatory)
# ===================================================================

class TestT2TamperDetection:
    def test_tampered_payload_detected(self, sample_packet):
        """T2: modifying payload after emission makes verify_packet fail."""
        # Tamper with the payload
        sample_packet.payload["next_action"] = "HACKED"
        valid, error = verify_packet(sample_packet)
        assert not valid
        assert "payload_hash mismatch" in error

    def test_tampered_threads_detected(self, sample_packet):
        sample_packet.payload["threads"].append({"id": "injected", "title": "FAKE"})
        valid, error = verify_packet(sample_packet)
        assert not valid

    def test_tampered_receipt_hash_detected(self, sample_packet):
        original_hash = sample_packet.receipt_hash
        sample_packet.receipt_hash = "0" * 64
        valid, error = verify_packet(sample_packet)
        assert not valid
        assert "receipt_hash mismatch" in error

    def test_tampered_authority_detected(self):
        p = emit_boot_memory("s1", next_action="test")
        p.authority = "ADMIN"
        valid, error = verify_packet(p)
        assert not valid
        assert "authority" in error

    def test_untampered_packet_passes(self, sample_packet):
        valid, error = verify_packet(sample_packet)
        assert valid
        assert error is None


# ===================================================================
# T3 — LOAD → VERIFY → RECONSTRUCT (mandatory)
# ===================================================================

class TestT3LoadVerifyReconstruct:
    def test_persist_and_load(self, sample_packet, tmp_dir):
        """T3: emit → persist → load → verify cycle."""
        persist_packet(sample_packet, directory=tmp_dir)
        loaded = load_packet(sample_packet.session_id, directory=tmp_dir)
        assert loaded is not None
        valid, error = verify_packet(loaded)
        assert valid, error

    def test_loaded_hash_matches_original(self, sample_packet, tmp_dir):
        persist_packet(sample_packet, directory=tmp_dir)
        loaded = load_packet(sample_packet.session_id, directory=tmp_dir)
        assert loaded.receipt_hash == sample_packet.receipt_hash

    def test_loaded_payload_matches_original(self, sample_packet, tmp_dir):
        persist_packet(sample_packet, directory=tmp_dir)
        loaded = load_packet(sample_packet.session_id, directory=tmp_dir)
        assert loaded.payload == sample_packet.payload

    def test_load_and_verify_convenience(self, sample_packet, tmp_dir):
        persist_packet(sample_packet, directory=tmp_dir)
        packet, valid, error = load_and_verify(sample_packet.session_id, directory=tmp_dir)
        assert packet is not None
        assert valid
        assert error is None

    def test_load_nonexistent_returns_none(self, tmp_dir):
        packet = load_packet("nonexistent", directory=tmp_dir)
        assert packet is None

    def test_load_and_verify_nonexistent(self, tmp_dir):
        packet, valid, error = load_and_verify("nonexistent", directory=tmp_dir)
        assert packet is None
        assert not valid
        assert "not found" in error

    def test_tampered_file_detected(self, sample_packet, tmp_dir):
        path = persist_packet(sample_packet, directory=tmp_dir)
        # Tamper with the file on disk
        with open(path, "r") as f:
            data = json.load(f)
        data["payload"]["next_action"] = "TAMPERED_ON_DISK"
        with open(path, "w") as f:
            json.dump(data, f)
        packet, valid, error = load_and_verify(sample_packet.session_id, directory=tmp_dir)
        assert packet is not None
        assert not valid
        assert "mismatch" in error


# ===================================================================
# T4 — CHAIN LINKING (mandatory)
# ===================================================================

class TestT4ChainLinking:
    def test_chain_integrity(self):
        """T4: packets chain via previous_disclosure_hash."""
        p1 = emit_boot_memory("session-1", next_action="start")
        p2 = emit_boot_memory("session-2", next_action="continue",
                              previous_disclosure_hash=p1.receipt_hash)
        assert p2.previous_disclosure_hash == p1.receipt_hash

    def test_genesis_link(self):
        p = emit_boot_memory("first", next_action="begin")
        assert p.previous_disclosure_hash == GENESIS_DISCLOSURE_HASH

    def test_three_packet_chain(self):
        p1 = emit_boot_memory("s1", next_action="A")
        p2 = emit_boot_memory("s2", next_action="B", previous_disclosure_hash=p1.receipt_hash)
        p3 = emit_boot_memory("s3", next_action="C", previous_disclosure_hash=p2.receipt_hash)

        ok, errors = verify_chain([p1, p2, p3])
        assert ok, errors

    def test_broken_chain_detected(self):
        p1 = emit_boot_memory("s1", next_action="A")
        p2 = emit_boot_memory("s2", next_action="B", previous_disclosure_hash="WRONG_HASH")
        ok, errors = verify_chain([p1, p2])
        assert not ok
        assert any("chain break" in e for e in errors)

    def test_empty_chain_valid(self):
        ok, errors = verify_chain([])
        assert ok


# ===================================================================
# Reconstruction
# ===================================================================

class TestReconstruction:
    def test_reconstruct_has_all_fields(self, sample_packet):
        ctx = reconstruct_boot_context(sample_packet)
        assert ctx["session_id"] == "test-session"
        assert len(ctx["threads"]) == 2
        assert len(ctx["tensions"]) == 1
        assert ctx["next_action"] == "Fix CI"
        assert ctx["verified"] is True
        assert ctx["authority"] == "NONE"

    def test_reconstruct_empty_packet(self):
        p = emit_boot_memory("empty")
        ctx = reconstruct_boot_context(p)
        assert ctx["threads"] == []
        assert ctx["tensions"] == []
        assert ctx["next_action"] == ""


# ===================================================================
# Authority
# ===================================================================

class TestAuthority:
    def test_packet_authority_always_none(self, sample_packet):
        assert sample_packet.authority == "NONE"

    def test_cannot_emit_with_authority(self):
        """emit_boot_memory always sets authority=NONE regardless of input."""
        p = emit_boot_memory("s1", next_action="test")
        assert p.authority == "NONE"


# ===================================================================
# Serialization
# ===================================================================

class TestSerialization:
    def test_to_dict_and_back(self, sample_packet):
        d = sample_packet.to_dict()
        restored = MemoryPacket.from_dict(d)
        assert restored.receipt_hash == sample_packet.receipt_hash
        assert restored.payload == sample_packet.payload

    def test_json_round_trip(self, sample_packet):
        s = json.dumps(sample_packet.to_dict())
        d = json.loads(s)
        restored = MemoryPacket.from_dict(d)
        valid, error = verify_packet(restored)
        assert valid, error

    def test_canonical_key_order_irrelevant(self):
        """Payload with different key insertion order produces same hash."""
        p1 = emit_boot_memory("s1", threads=[{"a": 1}], tensions=[{"b": 2}])
        p2 = emit_boot_memory("s1", tensions=[{"b": 2}], threads=[{"a": 1}])
        assert p1.payload_hash == p2.payload_hash
