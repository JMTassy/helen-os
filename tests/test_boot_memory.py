"""Tests for Boot Memory Hydration V1 — Lock 2.

Law:
  Boot memory = verified memory packet + sanitized resume + git context.
  No raw companion_state. No unverified injection. No ambient context.

Tests verify:
  - hydrate_boot_memory returns dict
  - Verified memory from receipts (MemoryPacket sealed)
  - Resume packet sanitized (only allowed keys)
  - Git context extracted
  - Boot hash seals the hydrated memory
  - Empty state → empty boot memory
  - Corrupted resume is ignored (not crash)
  - Non-allowed resume keys are stripped
"""
import json
import pytest
from pathlib import Path

from helensh.kernel import init_session, replay
from helensh.boot import (
    hydrate_boot_memory,
    ALLOWED_RESUME_KEYS,
    HelenSession,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def s0():
    return init_session(session_id="S-boot-mem-test")


@pytest.fixture
def s_with_data(s0):
    return replay(s0, ["hello", "#remember important data", "world"])


@pytest.fixture
def resume_file(tmp_path):
    """Create a valid resume file."""
    resume = {
        "last_topic": "kernel",
        "last_action": "chat",
        "open_loop": "implement url gate",
        "next_step": "run tests",
    }
    p = tmp_path / "session_resume.json"
    p.write_text(json.dumps(resume))
    return p


@pytest.fixture
def bad_resume_file(tmp_path):
    """Create a resume file with extra forbidden keys."""
    resume = {
        "last_topic": "kernel",
        "secret_key": "should_be_stripped",
        "companion_state": {"full": "dump"},
        "open_threads": ["thread1", "thread2"],
    }
    p = tmp_path / "session_resume.json"
    p.write_text(json.dumps(resume))
    return p


# ── Basic Hydration ───────────────────────────────────────────────────


class TestHydrateBootMemory:
    def test_returns_dict(self, s0):
        result = hydrate_boot_memory(s0)
        assert isinstance(result, dict)

    def test_empty_state_empty_memory(self, s0):
        result = hydrate_boot_memory(s0)
        # No receipts → no verified memory
        assert "verified_memory" not in result

    def test_state_with_receipts_has_verified_memory(self, s_with_data):
        result = hydrate_boot_memory(s_with_data)
        assert "verified_memory" in result
        assert "memory_packet_hash" in result
        assert isinstance(result["verified_memory"], dict)

    def test_verified_memory_has_keys(self, s_with_data):
        result = hydrate_boot_memory(s_with_data)
        mem = result["verified_memory"]
        assert "last_message" in mem
        assert mem["last_message"] == "world"

    def test_memory_packet_hash_is_hex(self, s_with_data):
        result = hydrate_boot_memory(s_with_data)
        h = result["memory_packet_hash"]
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_boot_hash_present(self, s_with_data):
        result = hydrate_boot_memory(s_with_data)
        assert "boot_hash" in result
        assert len(result["boot_hash"]) == 64


# ── Resume Packet ─────────────────────────────────────────────────────


class TestResumePacket:
    def test_resume_loaded(self, s0, resume_file):
        result = hydrate_boot_memory(s0, resume_path=resume_file)
        assert "resume" in result
        assert result["resume"]["last_topic"] == "kernel"

    def test_resume_sanitized(self, s0, bad_resume_file):
        result = hydrate_boot_memory(s0, resume_path=bad_resume_file)
        resume = result.get("resume", {})
        # Only allowed keys survive
        assert "secret_key" not in resume
        assert "companion_state" not in resume
        assert "open_threads" not in resume
        # last_topic IS allowed
        assert resume.get("last_topic") == "kernel"

    def test_allowed_resume_keys_frozen(self):
        """ALLOWED_RESUME_KEYS is a frozenset — cannot be mutated."""
        assert isinstance(ALLOWED_RESUME_KEYS, frozenset)
        assert ALLOWED_RESUME_KEYS == {"last_topic", "last_action", "open_loop", "next_step"}

    def test_missing_resume_no_crash(self, s0, tmp_path):
        """Missing resume file does not crash."""
        missing = tmp_path / "nonexistent.json"
        result = hydrate_boot_memory(s0, resume_path=missing)
        assert "resume" not in result

    def test_corrupt_resume_no_crash(self, s0, tmp_path):
        """Corrupt resume file does not crash."""
        bad = tmp_path / "corrupt.json"
        bad.write_text("not valid json {{{")
        result = hydrate_boot_memory(s0, resume_path=bad)
        assert "resume" not in result


# ── Git Context ───────────────────────────────────────────────────────


class TestGitContext:
    def test_git_context_present(self, s0):
        """Git context is extracted (assuming we're in a git repo)."""
        result = hydrate_boot_memory(s0)
        # May or may not have git depending on environment
        # If git is available, it should have branch
        if "git" in result:
            assert "branch" in result["git"]
            assert "last_commit" in result["git"]

    def test_git_failure_no_crash(self, s0, tmp_path):
        """Invalid git root does not crash."""
        result = hydrate_boot_memory(s0, git_root=tmp_path / "nonexistent")
        # Should still return a valid dict (just without git)
        assert isinstance(result, dict)


# ── Determinism ───────────────────────────────────────────────────────


class TestBootMemoryDeterminism:
    def test_same_state_same_memory(self, s_with_data):
        r1 = hydrate_boot_memory(s_with_data)
        r2 = hydrate_boot_memory(s_with_data)
        assert r1["verified_memory"] == r2["verified_memory"]
        assert r1["memory_packet_hash"] == r2["memory_packet_hash"]

    def test_different_state_different_memory(self, s0):
        s1 = replay(s0, ["alpha"])
        s2 = replay(s0, ["omega"])
        r1 = hydrate_boot_memory(s1)
        r2 = hydrate_boot_memory(s2)
        assert r1["verified_memory"] != r2["verified_memory"]


# ── HelenSession Integration ──────────────────────────────────────────


class TestHelenSessionBootMemory:
    def test_session_has_boot_memory_attr(self, s0):
        session = HelenSession(state=s0, boot_memory={"test": True})
        assert session.boot_memory == {"test": True}

    def test_session_default_boot_memory_empty(self, s0):
        session = HelenSession(state=s0)
        assert session.boot_memory == {}
