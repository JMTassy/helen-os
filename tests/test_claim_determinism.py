"""Tests for Claim Determinism — Step 4 + Step 5 + Step 6.

Proves:
  g(R_{1..n}) = g(R_{1..n})

  - No hidden randomness
  - No state leakage
  - Claims are pure functions of the ledger
  - Verification is stateless
  - Merkle proofs provide O(log n) verification
  - Tampered ledger references break verification

Combined from user's Step 4 (determinism), Step 5 (Merkle compression),
and Step 6 (claim purity / ledger-only).
"""
import copy
import pytest

from helensh.kernel import init_session, step, replay
from helensh.claims import ClaimEngine, VerifiableClaim, verify_claim
from helensh.merkle import MerkleTree


# ── Helpers ──────────────────────────────────────────────────────────


def build_ledger():
    """Build a deterministic receipt ledger from fixed inputs."""
    state = init_session(session_id="S-determinism")
    for u in ["alpha", "beta", "gamma"]:
        state, _ = step(state, u)
    return state


def build_claim(state, key="last_message"):
    """Build a claim from state with fixed ID and timestamp (removes randomness)."""
    engine = ClaimEngine(state)
    return engine.claim_memory_disclosure(
        key, claim_id="determinism-test", created_at="2024-01-01T00:00:00Z",
    )


# ── Step 4: Claim Determinism ────────────────────────────────────────


class TestClaimDeterminism:
    def test_same_ledger_same_claim(self):
        """g(R_{1..n}) == g(R_{1..n}) — no hidden randomness."""
        s1 = build_ledger()
        s2 = build_ledger()
        c1 = build_claim(s1)
        c2 = build_claim(s2)
        assert c1.receipt_hash == c2.receipt_hash
        assert c1.evidence == c2.evidence
        assert c1.merkle_root == c2.merkle_root

    def test_claim_independent_verification(self):
        """Verification requires only the claim, not the state."""
        state = build_ledger()
        claim = build_claim(state)
        # verify_claim takes ONLY the claim — no state parameter
        ok, errors = verify_claim(claim)
        assert ok is True
        assert errors == []

    def test_claim_breaks_on_tampered_receipt(self):
        """Tampered receipt reference breaks verification."""
        state = build_ledger()
        claim = build_claim(state)
        # Create tampered claim with wrong receipt hash
        tampered = VerifiableClaim(
            claim_id=claim.claim_id,
            claim_type=claim.claim_type,
            subject=claim.subject,
            assertion=claim.assertion,
            evidence={**claim.evidence, "source_receipt_hash": "fake_hash"},
            merkle_root=claim.merkle_root,
            merkle_proof=claim.merkle_proof,
            session_id=claim.session_id,
            created_at=claim.created_at,
            receipt_hash=claim.receipt_hash,  # hash no longer matches
        )
        ok, errors = verify_claim(tampered)
        assert ok is False

    def test_different_inputs_different_claims(self):
        """Different ledgers produce different claims."""
        s1 = build_ledger()
        s0 = init_session(session_id="S-determinism")
        s2 = replay(s0, ["delta", "epsilon", "zeta"])
        c1 = build_claim(s1)
        c2 = build_claim(s2)
        assert c1.receipt_hash != c2.receipt_hash

    def test_claim_hash_depends_on_value(self):
        """Claim hash changes when the underlying value changes."""
        s0 = init_session(session_id="S-determinism")
        s1 = replay(s0, ["alpha"])
        s2 = replay(s0, ["omega"])
        c1 = build_claim(s1)
        c2 = build_claim(s2)
        assert c1.evidence["value"] != c2.evidence["value"]
        assert c1.receipt_hash != c2.receipt_hash


# ── Step 5: Merkle Proof Compression ────────────────────────────────


class TestMerkleProofCompression:
    def test_claim_has_merkle_proof(self):
        """Claims carry O(log n) inclusion proofs."""
        state = build_ledger()
        engine = ClaimEngine(state)
        claim = engine.claim_receipt_inclusion(0)
        assert claim.merkle_proof is not None
        assert len(claim.merkle_proof) > 0  # non-trivial proof

    def test_merkle_proof_verifies(self):
        """Proof verifies leaf inclusion in the root."""
        state = build_ledger()
        tree = MerkleTree(state["receipts"])
        for i in range(len(state["receipts"])):
            proof = tree.get_proof(i)
            leaf = tree.leaves[i]
            assert MerkleTree.verify_proof(leaf, proof, tree.root), \
                f"Proof failed at index {i}"

    def test_tampered_leaf_fails_proof(self):
        """Tampered leaf hash fails Merkle verification."""
        state = build_ledger()
        tree = MerkleTree(state["receipts"])
        proof = tree.get_proof(0)
        assert not MerkleTree.verify_proof("fake_leaf", proof, tree.root)

    def test_tampered_proof_fails(self):
        """Modified proof element fails verification."""
        state = build_ledger()
        tree = MerkleTree(state["receipts"])
        proof = tree.get_proof(0)
        if proof:
            tampered_proof = [("fake_hash", proof[0][1])] + list(proof[1:])
            assert not MerkleTree.verify_proof(
                tree.leaves[0], tampered_proof, tree.root,
            )

    def test_proof_size_is_logarithmic(self):
        """Proof size is O(log n) — much smaller than full ledger."""
        s0 = init_session(session_id="S-log-test")
        s = replay(s0, [f"msg-{i}" for i in range(16)])  # 32 receipts
        tree = MerkleTree(s["receipts"])
        proof = tree.get_proof(0)
        # 32 leaves -> log2(32) = 5 proof elements
        assert len(proof) == 5


# ── Step 6: Claim Purity (Ledger-Only) ──────────────────────────────


class TestClaimPurity:
    def test_claim_from_receipts_only(self):
        """ClaimEngine works from state (which contains receipts).
        But the claim itself depends only on receipts, not ambient state."""
        state = build_ledger()

        # Build claim from state
        c1 = build_claim(state)

        # Tamper with non-receipt state (working_memory, env)
        state2 = copy.deepcopy(state)
        state2["env"]["secret"] = True
        state2["working_memory"]["injected"] = "hidden"

        # Claim should be IDENTICAL — it depends on receipts, not ambient state
        # (The memory_disclosure claim reads from receipts via reconstruct_memory)
        c2 = build_claim(state2)
        assert c1.evidence == c2.evidence

    def test_ledger_integrity_pure(self):
        """Ledger integrity claim depends only on receipts."""
        state = build_ledger()
        engine = ClaimEngine(state)
        c1 = engine.claim_ledger_integrity(
            claim_id="pure", created_at="2024-01-01T00:00:00Z",
        )

        # Tamper with non-receipt state
        state2 = copy.deepcopy(state)
        state2["working_memory"]["injected"] = "hidden"
        engine2 = ClaimEngine(state2)
        c2 = engine2.claim_ledger_integrity(
            claim_id="pure", created_at="2024-01-01T00:00:00Z",
        )

        assert c1.receipt_hash == c2.receipt_hash

    def test_verify_needs_no_state(self):
        """verify_claim takes only a VerifiableClaim — no state argument."""
        state = build_ledger()
        claim = build_claim(state)
        # verify_claim signature: verify_claim(claim) -> (bool, errors)
        ok, errors = verify_claim(claim)
        assert ok is True

    def test_all_claim_types_are_pure(self):
        """All 6 claim types verify without state."""
        state = build_ledger()
        engine = ClaimEngine(state)

        claims = [
            engine.claim_state_transition(state["receipts"][0]),
            engine.claim_memory_disclosure("last_message"),
            engine.claim_ledger_integrity(),
            engine.claim_receipt_inclusion(0),
            engine.claim_execution_result(state["receipts"][1]),
            engine.claim_capability_state("chat"),
        ]

        for c in claims:
            ok, errors = verify_claim(c)
            assert ok, f"{c.claim_type} failed purity: {errors}"
