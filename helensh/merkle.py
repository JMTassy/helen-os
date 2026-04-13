"""HELEN OS — Merkle Tree Sealing.

Turns the receipt ledger from "chain of receipts" into "verifiable computation history."

Linear hash chaining (previous_hash) gives append-only integrity.
Merkle tree adds:
  - Single root hash for the entire ledger (publishable / signable)
  - Efficient inclusion proofs: prove any receipt belongs without revealing all
  - Tamper detection at any scale: O(log n) verification
  - Future-proof for snapshots, sharding, distributed witnesses

Design:
  - Leaves = canonical_hash of each receipt
  - Internal nodes = SHA-256(left || right)
  - Odd-count levels: duplicate last node
  - Empty tree: EMPTY_LEDGER sentinel hash

Pure stdlib — no external libraries.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical, canonical_hash


# ── Constants ─────────────────────────────────────────────────────────

EMPTY_LEDGER_HASH = hashlib.sha256(b"EMPTY_LEDGER").hexdigest()


# ── Helpers ──────────────────────────────────────────────────────────


def _hash_pair(left: str, right: str) -> str:
    """Hash two hex digest strings together."""
    combined = (left + right).encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def _leaf_hash(receipt: Dict[str, Any]) -> str:
    """Compute leaf hash from canonical receipt JSON."""
    return canonical_hash(receipt)


# ── MerkleTree ───────────────────────────────────────────────────────


class MerkleTree:
    """Merkle tree over a receipt ledger.

    Supports:
      - Build from a list of receipts
      - Append single receipt (recomputes root)
      - Generate inclusion proof for any index
      - Verify an inclusion proof
    """

    def __init__(self, receipts: Optional[List[Dict[str, Any]]] = None) -> None:
        self.leaves: List[str] = []
        self.root: str = EMPTY_LEDGER_HASH
        if receipts:
            self.build(receipts)

    def build(self, receipts: List[Dict[str, Any]]) -> str:
        """Build tree from a list of receipts. Returns root hash."""
        self.leaves = [_leaf_hash(r) for r in receipts]
        self.root = self._compute_root(list(self.leaves))
        return self.root

    def append(self, receipt: Dict[str, Any]) -> str:
        """Append a receipt and recompute root. Returns new root."""
        self.leaves.append(_leaf_hash(receipt))
        self.root = self._compute_root(list(self.leaves))
        return self.root

    @property
    def size(self) -> int:
        """Number of leaves in the tree."""
        return len(self.leaves)

    def _compute_root(self, nodes: List[str]) -> str:
        """Compute Merkle root from leaf hashes."""
        if not nodes:
            return EMPTY_LEDGER_HASH

        level = list(nodes)
        while len(level) > 1:
            if len(level) % 2 == 1:
                level.append(level[-1])  # duplicate last for pairing
            next_level = []
            for i in range(0, len(level), 2):
                next_level.append(_hash_pair(level[i], level[i + 1]))
            level = next_level

        return level[0]

    def get_proof(self, index: int) -> List[Tuple[str, str]]:
        """Generate Merkle inclusion proof for receipt at index.

        Returns list of (hash, side) tuples where side is "left" or "right",
        indicating which side the sibling is on.

        To verify: start with leaf hash, combine with each proof element
        from bottom to root.
        """
        if index < 0 or index >= len(self.leaves):
            raise IndexError(f"index {index} out of range (0..{len(self.leaves) - 1})")

        proof: List[Tuple[str, str]] = []
        level = list(self.leaves)
        idx = index

        while len(level) > 1:
            if len(level) % 2 == 1:
                level.append(level[-1])

            if idx % 2 == 0:
                # sibling is to the right
                sibling = level[idx + 1]
                proof.append((sibling, "right"))
            else:
                # sibling is to the left
                sibling = level[idx - 1]
                proof.append((sibling, "left"))

            # Move to parent level
            next_level = []
            for i in range(0, len(level), 2):
                next_level.append(_hash_pair(level[i], level[i + 1]))
            level = next_level
            idx = idx // 2

        return proof

    @staticmethod
    def verify_proof(
        leaf_hash: str,
        proof: List[Tuple[str, str]],
        expected_root: str,
    ) -> bool:
        """Verify a Merkle inclusion proof.

        Walk from leaf to root, combining with each proof element.
        Returns True iff the computed root matches expected_root.
        """
        current = leaf_hash
        for sibling_hash, side in proof:
            if side == "left":
                current = _hash_pair(sibling_hash, current)
            else:
                current = _hash_pair(current, sibling_hash)
        return current == expected_root


# ── Convenience functions ────────────────────────────────────────────


def compute_merkle_root(receipts: List[Dict[str, Any]]) -> str:
    """Compute Merkle root of a receipt list. Stateless convenience."""
    tree = MerkleTree(receipts)
    return tree.root


def compute_hash_root(hashes: List[str]) -> str:
    """Compute Merkle root from a list of hash strings directly.

    Unlike compute_merkle_root (which takes receipt dicts and hashes them),
    this takes pre-computed hashes as leaves.

    Used by MemoryPacket and ContinuityPacket for subset-level sealing.
    """
    if not hashes:
        return EMPTY_LEDGER_HASH

    level = list(hashes)
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])
        next_level = []
        for i in range(0, len(level), 2):
            next_level.append(_hash_pair(level[i], level[i + 1]))
        level = next_level

    return level[0]


def verify_receipt_inclusion(
    receipt: Dict[str, Any],
    index: int,
    receipts: List[Dict[str, Any]],
) -> bool:
    """Prove that a receipt at index belongs to the full ledger.

    Builds tree, generates proof, verifies proof. O(n) build + O(log n) verify.
    For repeated queries, build tree once and call get_proof/verify_proof directly.
    """
    tree = MerkleTree(receipts)
    leaf = _leaf_hash(receipt)
    proof = tree.get_proof(index)
    return MerkleTree.verify_proof(leaf, proof, tree.root)


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "MerkleTree",
    "EMPTY_LEDGER_HASH",
    "compute_merkle_root",
    "compute_hash_root",
    "verify_receipt_inclusion",
]
