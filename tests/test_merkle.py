"""Tests for helensh/merkle.py — Merkle Tree Sealing.

Tests verify:
  - Same receipts produce same root (determinism)
  - Tampered receipt changes root (tamper detection)
  - Inclusion proof for any receipt verifies
  - Proof fails on wrong root or wrong leaf
  - Empty ledger has sentinel root
  - Append-based and batch-built trees match
  - Edge cases: single receipt, power-of-2, odd count
"""
import copy
import pytest

from helensh.kernel import init_session, replay
from helensh.merkle import (
    MerkleTree,
    EMPTY_LEDGER_HASH,
    compute_merkle_root,
    verify_receipt_inclusion,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return init_session(session_id="S-merkle-test")


@pytest.fixture
def receipts(s0):
    s = replay(s0, ["hello", "world", "#recall", "#remember data"])
    return s["receipts"]


# ── Basic Properties ─────────────────────────────────────────────────


class TestMerkleBasic:
    def test_empty_tree_has_sentinel_root(self):
        tree = MerkleTree()
        assert tree.root == EMPTY_LEDGER_HASH
        assert tree.size == 0

    def test_single_receipt(self, receipts):
        tree = MerkleTree([receipts[0]])
        assert tree.root != EMPTY_LEDGER_HASH
        assert tree.size == 1

    def test_root_is_64_hex(self, receipts):
        tree = MerkleTree(receipts)
        assert len(tree.root) == 64
        assert all(c in "0123456789abcdef" for c in tree.root)

    def test_size_matches_receipts(self, receipts):
        tree = MerkleTree(receipts)
        assert tree.size == len(receipts)


# ── Determinism ──────────────────────────────────────────────────────


class TestMerkleDeterminism:
    def test_same_receipts_same_root(self, receipts):
        r1 = compute_merkle_root(receipts)
        r2 = compute_merkle_root(receipts)
        assert r1 == r2

    def test_build_twice_same_root(self, receipts):
        t1 = MerkleTree(receipts)
        t2 = MerkleTree(receipts)
        assert t1.root == t2.root

    def test_append_matches_batch(self, receipts):
        # Build from batch
        batch = MerkleTree(receipts)

        # Build by appending
        incremental = MerkleTree()
        for r in receipts:
            incremental.append(r)

        assert incremental.root == batch.root


# ── Tamper Detection ─────────────────────────────────────────────────


class TestMerkleTamperDetection:
    def test_tampered_receipt_changes_root(self, receipts):
        original_root = compute_merkle_root(receipts)
        tampered = copy.deepcopy(receipts)
        tampered[0]["verdict"] = "TAMPERED"
        tampered_root = compute_merkle_root(tampered)
        assert tampered_root != original_root

    def test_deleted_receipt_changes_root(self, receipts):
        original_root = compute_merkle_root(receipts)
        shortened = receipts[:-1]
        shortened_root = compute_merkle_root(shortened)
        assert shortened_root != original_root

    def test_reordered_receipts_changes_root(self, receipts):
        original_root = compute_merkle_root(receipts)
        reordered = list(reversed(receipts))
        reordered_root = compute_merkle_root(reordered)
        assert reordered_root != original_root

    def test_inserted_receipt_changes_root(self, receipts):
        original_root = compute_merkle_root(receipts)
        inserted = list(receipts)
        inserted.insert(2, receipts[0])  # duplicate a receipt in middle
        inserted_root = compute_merkle_root(inserted)
        assert inserted_root != original_root


# ── Inclusion Proofs ─────────────────────────────────────────────────


class TestInclusionProofs:
    def test_proof_for_first_receipt(self, receipts):
        tree = MerkleTree(receipts)
        proof = tree.get_proof(0)
        leaf = tree.leaves[0]
        assert MerkleTree.verify_proof(leaf, proof, tree.root)

    def test_proof_for_last_receipt(self, receipts):
        tree = MerkleTree(receipts)
        last = len(receipts) - 1
        proof = tree.get_proof(last)
        leaf = tree.leaves[last]
        assert MerkleTree.verify_proof(leaf, proof, tree.root)

    def test_proof_for_every_receipt(self, receipts):
        tree = MerkleTree(receipts)
        for i in range(len(receipts)):
            proof = tree.get_proof(i)
            leaf = tree.leaves[i]
            assert MerkleTree.verify_proof(leaf, proof, tree.root), \
                f"proof failed at index {i}"

    def test_proof_fails_on_wrong_root(self, receipts):
        tree = MerkleTree(receipts)
        proof = tree.get_proof(0)
        leaf = tree.leaves[0]
        assert not MerkleTree.verify_proof(leaf, proof, "wrong_root_hash")

    def test_proof_fails_on_wrong_leaf(self, receipts):
        tree = MerkleTree(receipts)
        proof = tree.get_proof(0)
        assert not MerkleTree.verify_proof("wrong_leaf_hash", proof, tree.root)

    def test_out_of_range_raises(self, receipts):
        tree = MerkleTree(receipts)
        with pytest.raises(IndexError):
            tree.get_proof(len(receipts))

    def test_negative_index_raises(self, receipts):
        tree = MerkleTree(receipts)
        with pytest.raises(IndexError):
            tree.get_proof(-1)

    def test_single_receipt_proof(self, receipts):
        tree = MerkleTree([receipts[0]])
        proof = tree.get_proof(0)
        assert proof == []  # root IS the leaf — no siblings
        assert MerkleTree.verify_proof(tree.leaves[0], proof, tree.root)


# ── Convenience Functions ────────────────────────────────────────────


class TestConvenienceFunctions:
    def test_compute_merkle_root_matches_tree(self, receipts):
        root = compute_merkle_root(receipts)
        tree = MerkleTree(receipts)
        assert root == tree.root

    def test_verify_receipt_inclusion(self, receipts):
        for i, r in enumerate(receipts):
            assert verify_receipt_inclusion(r, i, receipts), \
                f"inclusion failed at index {i}"

    def test_verify_inclusion_fails_for_wrong_receipt(self, receipts):
        fake = {"type": "FAKE", "hash": "fake123"}
        assert not verify_receipt_inclusion(fake, 0, receipts)


# ── Edge Cases ───────────────────────────────────────────────────────


class TestMerkleEdgeCases:
    def test_power_of_two_receipts(self, s0):
        """4 receipts (2 steps) — power of 2, no padding needed at leaf level."""
        s = replay(s0, ["a", "b"])
        tree = MerkleTree(s["receipts"])
        assert tree.size == 4
        # All proofs work
        for i in range(4):
            proof = tree.get_proof(i)
            assert MerkleTree.verify_proof(tree.leaves[i], proof, tree.root)

    def test_odd_number_receipts(self, s0):
        """3 steps = 6 receipts (even). Test with sliced to 5 (odd)."""
        s = replay(s0, ["a", "b", "c"])
        odd_receipts = s["receipts"][:5]
        tree = MerkleTree(odd_receipts)
        assert tree.size == 5
        for i in range(5):
            proof = tree.get_proof(i)
            assert MerkleTree.verify_proof(tree.leaves[i], proof, tree.root)

    def test_two_receipts(self, s0):
        """Minimum real case: 1 step = 2 receipts."""
        s = replay(s0, ["hello"])
        tree = MerkleTree(s["receipts"])
        assert tree.size == 2
        for i in range(2):
            proof = tree.get_proof(i)
            assert MerkleTree.verify_proof(tree.leaves[i], proof, tree.root)

    def test_empty_receipts_list(self):
        assert compute_merkle_root([]) == EMPTY_LEDGER_HASH
