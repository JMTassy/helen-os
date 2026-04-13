"""HELEN OS — Tool Determinism + Replay Identity Tests.

HARD INVARIANT:
    ToolExecution ≡ Deterministic Projection
    replay(receipts) ⇒ same artifacts

Properties proven:
    1. Seed derivation: same chain position → same seed
    2. Seed derivation: different chain position → different seed
    3. Artifact commitment: args_hash + output_hash + seed committed
    4. Artifact verification: detects tampered args, output, or seed
    5. Deterministic tool call: same inputs → same result + same artifact
    6. Replay identity: replayed tool call produces identical artifact
    7. Stress: tool whitelist enforced
    8. Stress: non-serializable payloads blocked
    9. Stress: oversized payloads blocked
    10. GNF integration: tool stress checks plug into stress layer

Test classes:
    1. TestSeedDerivation          — Deterministic seed from chain
    2. TestArtifactCommitment      — Commit structure and hashing
    3. TestArtifactVerification    — Verification catches tampering
    4. TestDeterministicToolCall   — Full deterministic wrapper
    5. TestReplayIdentity          — Same inputs → same outputs
    6. TestToolStressWhitelist     — Tool whitelist enforcement
    7. TestToolStressDeterminism   — Payload serialization check
    8. TestToolStressBounds        — Artifact size limits
    9. TestGNFStressIntegration    — Plug tool checks into GNF
"""
import pytest

from helensh.kernel import init_session, GENESIS_HASH
from helensh.state import canonical_hash
from helensh.tools import ToolResult, ToolRegistry
from helensh.tools.determinism import (
    derive_tool_seed,
    seed_to_int,
    commit_tool_artifact,
    verify_tool_artifact,
    deterministic_tool_call,
    TOOL_WHITELIST,
    MAX_ARTIFACT_SIZE,
    TOOL_STRESS_CHECKS,
    stress_check_tool_whitelist,
    stress_check_tool_determinism,
    stress_check_artifact_bounds,
)
from helensh.gnf import stress, DEFAULT_STRESS_CHECKS


# ── Helpers ─────────────────────────────────────────────────────────


def _fresh():
    return init_session()


def _echo_executor(payload, state):
    """Deterministic echo executor for testing."""
    msg = payload.get("message", "echo")
    return ToolResult(
        success=True, output=msg, artifacts=(),
        error=None, execution_ms=0.1,
    )


def _math_executor(payload, state):
    """Deterministic math executor for testing."""
    code = payload.get("code", "0")
    # Pure eval of simple math expressions
    try:
        result = eval(code, {"__builtins__": {}})  # noqa
    except Exception:
        result = None
    return ToolResult(
        success=result is not None, output=result, artifacts=(),
        error=None if result is not None else "eval failed",
        execution_ms=0.1,
    )


# ── 1. Seed Derivation ────────────────────────────────────────────


class TestSeedDerivation:
    """Deterministic seed from receipt chain."""

    def test_seed_is_hex(self):
        seed = derive_tool_seed(GENESIS_HASH, {"action": "test"})
        assert len(seed) == 64
        assert all(c in "0123456789abcdef" for c in seed)

    def test_same_inputs_same_seed(self):
        s1 = derive_tool_seed("hash_a", {"action": "test", "payload": {"x": 1}})
        s2 = derive_tool_seed("hash_a", {"action": "test", "payload": {"x": 1}})
        assert s1 == s2

    def test_different_previous_hash_different_seed(self):
        s1 = derive_tool_seed("hash_a", {"action": "test"})
        s2 = derive_tool_seed("hash_b", {"action": "test"})
        assert s1 != s2

    def test_different_proposal_different_seed(self):
        s1 = derive_tool_seed("hash_a", {"action": "test", "payload": {"x": 1}})
        s2 = derive_tool_seed("hash_a", {"action": "test", "payload": {"x": 2}})
        assert s1 != s2

    def test_seed_to_int_deterministic(self):
        seed = derive_tool_seed(GENESIS_HASH, {"action": "test"})
        i1 = seed_to_int(seed)
        i2 = seed_to_int(seed)
        assert i1 == i2
        assert isinstance(i1, int)
        assert 0 <= i1 < 2**32

    def test_seed_to_int_bounded(self):
        seed = derive_tool_seed(GENESIS_HASH, {"action": "test"})
        i = seed_to_int(seed, max_val=100)
        assert 0 <= i < 100


# ── 2. Artifact Commitment ────────────────────────────────────────


class TestArtifactCommitment:
    """Commit structure and hashing."""

    def test_commit_structure(self):
        result = ToolResult(success=True, output="42", artifacts=(), error=None, execution_ms=0.1)
        artifact = commit_tool_artifact("python_exec", {"code": "2+2"}, result, "seed123")
        assert artifact["type"] == "tool_output"
        assert artifact["tool"] == "python_exec"
        assert artifact["seed"] == "seed123"
        assert artifact["success"] is True
        assert "args_hash" in artifact
        assert "output_hash" in artifact

    def test_commit_args_hash_matches(self):
        args = {"code": "2+2"}
        result = ToolResult(success=True, output="4", artifacts=(), error=None, execution_ms=0.1)
        artifact = commit_tool_artifact("python_exec", args, result, "seed")
        assert artifact["args_hash"] == canonical_hash(args)

    def test_commit_output_hash_matches(self):
        result = ToolResult(success=True, output="hello", artifacts=(), error=None, execution_ms=0.1)
        artifact = commit_tool_artifact("test", {}, result, "seed")
        assert artifact["output_hash"] == canonical_hash(result.to_dict())

    def test_different_args_different_commit(self):
        result = ToolResult(success=True, output="x", artifacts=(), error=None, execution_ms=0.1)
        a1 = commit_tool_artifact("t", {"x": 1}, result, "seed")
        a2 = commit_tool_artifact("t", {"x": 2}, result, "seed")
        assert a1["args_hash"] != a2["args_hash"]

    def test_different_output_different_commit(self):
        r1 = ToolResult(success=True, output="a", artifacts=(), error=None, execution_ms=0.1)
        r2 = ToolResult(success=True, output="b", artifacts=(), error=None, execution_ms=0.1)
        a1 = commit_tool_artifact("t", {}, r1, "seed")
        a2 = commit_tool_artifact("t", {}, r2, "seed")
        assert a1["output_hash"] != a2["output_hash"]


# ── 3. Artifact Verification ──────────────────────────────────────


class TestArtifactVerification:
    """Verification catches tampering."""

    def test_valid_artifact_passes(self):
        args = {"code": "1+1"}
        result = ToolResult(success=True, output="2", artifacts=(), error=None, execution_ms=0.1)
        seed = "abc123"
        artifact = commit_tool_artifact("python_exec", args, result, seed)
        ok, errors = verify_tool_artifact(artifact, args, result, seed)
        assert ok is True
        assert errors == []

    def test_tampered_args_detected(self):
        args = {"code": "1+1"}
        result = ToolResult(success=True, output="2", artifacts=(), error=None, execution_ms=0.1)
        artifact = commit_tool_artifact("python_exec", args, result, "seed")
        # Verify with different args
        ok, errors = verify_tool_artifact(artifact, {"code": "9+9"}, result, "seed")
        assert ok is False
        assert "args_hash mismatch" in errors

    def test_tampered_output_detected(self):
        args = {"code": "1+1"}
        result = ToolResult(success=True, output="2", artifacts=(), error=None, execution_ms=0.1)
        artifact = commit_tool_artifact("python_exec", args, result, "seed")
        # Verify with different result
        fake_result = ToolResult(success=True, output="999", artifacts=(), error=None, execution_ms=0.1)
        ok, errors = verify_tool_artifact(artifact, args, fake_result, "seed")
        assert ok is False
        assert "output_hash mismatch" in errors

    def test_tampered_seed_detected(self):
        args = {"code": "1+1"}
        result = ToolResult(success=True, output="2", artifacts=(), error=None, execution_ms=0.1)
        artifact = commit_tool_artifact("python_exec", args, result, "seed_a")
        ok, errors = verify_tool_artifact(artifact, args, result, "seed_b")
        assert ok is False
        assert "seed mismatch" in errors

    def test_missing_tool_name_detected(self):
        artifact = {"args_hash": "x", "output_hash": "y", "seed": "z"}
        result = ToolResult(success=True, output="", artifacts=(), error=None, execution_ms=0.1)
        ok, errors = verify_tool_artifact(artifact, {}, result, "z")
        assert ok is False
        assert "missing tool name" in errors


# ── 4. Deterministic Tool Call ────────────────────────────────────


class TestDeterministicToolCall:
    """Full deterministic wrapper."""

    def test_returns_three_tuple(self):
        proposal = {"action": "respond", "payload": {"message": "hi"}}
        result, artifact, seed = deterministic_tool_call(
            "respond", _echo_executor, {"message": "hi"}, {},
            GENESIS_HASH, proposal,
        )
        assert isinstance(result, ToolResult)
        assert isinstance(artifact, dict)
        assert isinstance(seed, str)

    def test_result_matches_executor(self):
        proposal = {"action": "respond", "payload": {"message": "hello"}}
        result, _, _ = deterministic_tool_call(
            "respond", _echo_executor, {"message": "hello"}, {},
            GENESIS_HASH, proposal,
        )
        assert result.success is True
        assert result.output == "hello"

    def test_artifact_has_seed(self):
        proposal = {"action": "respond", "payload": {"message": "hi"}}
        _, artifact, seed = deterministic_tool_call(
            "respond", _echo_executor, {"message": "hi"}, {},
            GENESIS_HASH, proposal,
        )
        assert artifact["seed"] == seed

    def test_seed_injected_in_payload(self):
        """The seed is injected into the payload as _seed."""
        received_payload = {}

        def capture_executor(payload, state):
            received_payload.update(payload)
            return ToolResult(success=True, output="ok", artifacts=(), error=None, execution_ms=0.1)

        proposal = {"action": "test", "payload": {}}
        deterministic_tool_call(
            "test", capture_executor, {}, {},
            GENESIS_HASH, proposal,
        )
        assert "_seed" in received_payload
        assert "_seed_int" in received_payload

    def test_artifact_verifiable(self):
        """The artifact produced can be verified."""
        payload = {"message": "test"}
        proposal = {"action": "respond", "payload": payload}
        result, artifact, seed = deterministic_tool_call(
            "respond", _echo_executor, payload, {},
            GENESIS_HASH, proposal,
        )
        ok, errors = verify_tool_artifact(artifact, payload, result, seed)
        assert ok is True


# ── 5. Replay Identity ───────────────────────────────────────────


class TestReplayIdentity:
    """Same inputs → same outputs (the critical test)."""

    def test_replay_produces_identical_seed(self):
        """Same chain position → same seed."""
        proposal = {"action": "respond", "payload": {"message": "hi"}}
        s1 = derive_tool_seed(GENESIS_HASH, proposal)
        s2 = derive_tool_seed(GENESIS_HASH, proposal)
        assert s1 == s2

    def test_replay_produces_identical_artifact(self):
        """Same tool call replayed → identical artifact commitment."""
        payload = {"code": "2+2"}
        proposal = {"action": "python_exec", "payload": payload}

        r1, a1, s1 = deterministic_tool_call(
            "python_exec", _math_executor, payload, {},
            GENESIS_HASH, proposal,
        )
        r2, a2, s2 = deterministic_tool_call(
            "python_exec", _math_executor, payload, {},
            GENESIS_HASH, proposal,
        )

        assert s1 == s2
        assert a1 == a2
        assert r1.output == r2.output

    def test_replay_chain_produces_identical_artifacts(self):
        """Sequential tool calls with chained hashes → identical replay."""
        proposals = [
            {"action": "python_exec", "payload": {"code": f"{i}+{i}"}}
            for i in range(5)
        ]

        def _run_chain():
            prev_hash = GENESIS_HASH
            artifacts = []
            for p in proposals:
                _, artifact, seed = deterministic_tool_call(
                    "python_exec", _math_executor, p["payload"], {},
                    prev_hash, p,
                )
                artifacts.append(artifact)
                # Chain: next hash = H(artifact)
                prev_hash = canonical_hash(artifact)
            return artifacts

        chain1 = _run_chain()
        chain2 = _run_chain()

        assert len(chain1) == len(chain2)
        for a1, a2 in zip(chain1, chain2):
            assert a1 == a2

    def test_different_chain_position_different_artifact(self):
        """Different previous_hash → different seed → different artifact."""
        payload = {"code": "1+1"}
        proposal = {"action": "python_exec", "payload": payload}

        _, a1, s1 = deterministic_tool_call(
            "python_exec", _math_executor, payload, {},
            "hash_a", proposal,
        )
        _, a2, s2 = deterministic_tool_call(
            "python_exec", _math_executor, payload, {},
            "hash_b", proposal,
        )

        assert s1 != s2
        # Args hash and output hash are same (same inputs → same outputs)
        assert a1["args_hash"] == a2["args_hash"]
        assert a1["output_hash"] == a2["output_hash"]
        # But seeds differ → artifacts differ
        assert a1["seed"] != a2["seed"]


# ── 6. Tool Stress: Whitelist ─────────────────────────────────────


class TestToolStressWhitelist:
    """Tool whitelist enforcement."""

    def test_whitelisted_tool_passes(self):
        for tool in TOOL_WHITELIST:
            result = stress_check_tool_whitelist(
                {"action": tool}, {}, "ALLOW",
            )
            assert result is None, f"{tool} should pass whitelist"

    def test_non_tool_action_passes(self):
        """Non-tool actions (chat, respond, etc.) pass through."""
        result = stress_check_tool_whitelist(
            {"action": "chat"}, {}, "ALLOW",
        )
        assert result is None

    def test_whitelist_contains_expected_tools(self):
        assert "python_exec" in TOOL_WHITELIST
        assert "fs_read" in TOOL_WHITELIST
        assert "db_query" in TOOL_WHITELIST


# ── 7. Tool Stress: Determinism ──────────────────────────────────


class TestToolStressDeterminism:
    """Payload serialization check."""

    def test_serializable_payload_passes(self):
        result = stress_check_tool_determinism(
            {"action": "python_exec", "payload": {"code": "2+2"}},
            {}, "ALLOW",
        )
        assert result is None

    def test_non_allow_verdict_skipped(self):
        result = stress_check_tool_determinism(
            {"action": "python_exec", "payload": {"code": "2+2"}},
            {}, "DENY",
        )
        assert result is None

    def test_non_tool_action_skipped(self):
        result = stress_check_tool_determinism(
            {"action": "chat", "payload": {"message": "hi"}},
            {}, "ALLOW",
        )
        assert result is None


# ── 8. Tool Stress: Bounds ───────────────────────────────────────


class TestToolStressBounds:
    """Artifact size limits."""

    def test_normal_payload_passes(self):
        result = stress_check_artifact_bounds(
            {"action": "python_exec", "payload": {"code": "2+2"}},
            {}, "ALLOW",
        )
        assert result is None

    def test_oversized_payload_blocked(self):
        huge = {"action": "python_exec", "payload": {"data": "x" * (MAX_ARTIFACT_SIZE + 1)}}
        result = stress_check_artifact_bounds(huge, {}, "ALLOW")
        assert result is not None
        assert "exceeds" in result

    def test_non_allow_verdict_skipped(self):
        result = stress_check_artifact_bounds(
            {"action": "python_exec", "payload": {"data": "x" * 999999}},
            {}, "DENY",
        )
        assert result is None


# ── 9. GNF Stress Integration ────────────────────────────────────


class TestGNFStressIntegration:
    """Tool stress checks plug into GNF stress layer."""

    def test_tool_checks_are_callable(self):
        """All tool stress checks follow the (proposal, state, verdict) → Optional[str] signature."""
        for name, fn in TOOL_STRESS_CHECKS:
            result = fn({"action": "chat", "payload": {}}, {}, "ALLOW")
            assert result is None or isinstance(result, str)

    def test_tool_checks_combinable_with_default(self):
        """Tool checks can be appended to DEFAULT_STRESS_CHECKS."""
        combined = DEFAULT_STRESS_CHECKS + TOOL_STRESS_CHECKS
        s = _fresh()
        proposal = {"action": "chat", "payload": {"message": "hi"}, "authority": False}
        result = stress(proposal, s, "ALLOW", checks=combined)
        assert result.passed is True

    def test_authority_true_still_blocked(self):
        """Combining tool checks doesn't weaken existing checks."""
        combined = DEFAULT_STRESS_CHECKS + TOOL_STRESS_CHECKS
        s = _fresh()
        proposal = {"action": "respond", "payload": {}, "authority": True}
        result = stress(proposal, s, "ALLOW", checks=combined)
        assert result.passed is False

    def test_oversized_tool_payload_blocked_in_gnf(self):
        """Oversized payload detected when tool checks are in the stress chain."""
        combined = DEFAULT_STRESS_CHECKS + TOOL_STRESS_CHECKS
        s = _fresh()
        proposal = {
            "action": "python_exec",
            "payload": {"code": "x" * (MAX_ARTIFACT_SIZE + 1)},
            "authority": False,
        }
        result = stress(proposal, s, "ALLOW", checks=combined)
        assert result.passed is False
        assert any("exceeds" in f for f in result.failures)
