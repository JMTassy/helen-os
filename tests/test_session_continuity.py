"""
HELEN OS Session Continuity — Test Suite

Tests multi-session replay: close → open → verify → diff.
Core invariant: context at session B start == context at session A end.
"""

import json
import os
import pytest
import tempfile

from helen_os.session_continuity import (
    SessionRecord, close_session_with_packet, open_session_from_chain,
    replay_session_chain, session_diff, load_session_ledger, get_last_session,
)
from helen_os.memory_hydration import (
    load_packet, verify_packet, GENESIS_DISCLOSURE_HASH,
)


@pytest.fixture
def dirs():
    with tempfile.TemporaryDirectory() as session_dir:
        with tempfile.TemporaryDirectory() as hydration_dir:
            yield session_dir, hydration_dir


# ===================================================================
# Close session
# ===================================================================

class TestCloseSession:
    def test_close_emits_packet(self, dirs):
        sd, hd = dirs
        packet, record = close_session_with_packet(
            "s1", threads=[{"id": "t1", "title": "test"}],
            next_action="do next", summary="first session",
            session_dir=sd, hydration_dir=hd,
        )
        assert packet.session_id == "s1"
        assert packet.receipt_hash
        assert record.packet_hash == packet.receipt_hash
        assert record.summary == "first session"
        assert record.authority == "NONE"

    def test_close_persists_packet(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", next_action="test", session_dir=sd, hydration_dir=hd)
        loaded = load_packet("s1", hd)
        assert loaded is not None
        valid, err = verify_packet(loaded)
        assert valid, err

    def test_close_records_session(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", summary="done", session_dir=sd, hydration_dir=hd)
        records = load_session_ledger(sd)
        assert len(records) == 1
        assert records[0].session_id == "s1"

    def test_first_session_links_to_genesis(self, dirs):
        sd, hd = dirs
        packet, _ = close_session_with_packet("s1", session_dir=sd, hydration_dir=hd)
        assert packet.previous_disclosure_hash == GENESIS_DISCLOSURE_HASH


# ===================================================================
# Chain linking
# ===================================================================

class TestChainLinking:
    def test_second_session_chains_to_first(self, dirs):
        sd, hd = dirs
        p1, _ = close_session_with_packet("s1", next_action="A", session_dir=sd, hydration_dir=hd)
        p2, r2 = close_session_with_packet("s2", next_action="B", session_dir=sd, hydration_dir=hd)
        assert p2.previous_disclosure_hash == p1.receipt_hash
        assert r2.previous_session == "s1"

    def test_three_session_chain(self, dirs):
        sd, hd = dirs
        p1, _ = close_session_with_packet("s1", next_action="A", session_dir=sd, hydration_dir=hd)
        p2, _ = close_session_with_packet("s2", next_action="B", session_dir=sd, hydration_dir=hd)
        p3, _ = close_session_with_packet("s3", next_action="C", session_dir=sd, hydration_dir=hd)
        assert p2.previous_disclosure_hash == p1.receipt_hash
        assert p3.previous_disclosure_hash == p2.receipt_hash


# ===================================================================
# Open from chain — core invariant
# ===================================================================

class TestOpenFromChain:
    def test_cold_start_no_previous(self, dirs):
        sd, hd = dirs
        ctx, valid, err = open_session_from_chain(sd, hd)
        assert ctx is None  # no previous session
        assert valid

    def test_open_restores_context(self, dirs):
        """Core invariant: context at B start == context at A end."""
        sd, hd = dirs
        threads = [{"id": "t1", "title": "Build kernel"}]
        tensions = [{"thread": "Build kernel", "issue": "CI red"}]
        close_session_with_packet(
            "s1", threads=threads, tensions=tensions,
            next_action="Fix CI", summary="session 1",
            session_dir=sd, hydration_dir=hd,
        )

        ctx, valid, err = open_session_from_chain(sd, hd)
        assert valid, err
        assert ctx["threads"] == threads
        assert ctx["tensions"] == tensions
        assert ctx["next_action"] == "Fix CI"
        assert ctx["previous_session"] == "s1"
        assert ctx["previous_summary"] == "session 1"
        assert ctx["verified"] is True
        assert ctx["authority"] == "NONE"

    def test_open_after_two_sessions(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", next_action="A", session_dir=sd, hydration_dir=hd)
        close_session_with_packet("s2", next_action="B", summary="second",
                                  session_dir=sd, hydration_dir=hd)

        ctx, valid, err = open_session_from_chain(sd, hd)
        assert valid
        assert ctx["next_action"] == "B"
        assert ctx["previous_session"] == "s2"

    def test_tampered_packet_detected(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", next_action="test", session_dir=sd, hydration_dir=hd)

        # Tamper with the persisted packet
        path = os.path.join(hd, "s1.json")
        with open(path) as f:
            data = json.load(f)
        data["payload"]["next_action"] = "HACKED"
        with open(path, "w") as f:
            json.dump(data, f)

        ctx, valid, err = open_session_from_chain(sd, hd)
        assert not valid
        assert "mismatch" in err


# ===================================================================
# Replay chain
# ===================================================================

class TestReplayChain:
    def test_empty_chain(self, dirs):
        sd, hd = dirs
        timeline, ok, errors = replay_session_chain(sd, hd)
        assert ok
        assert timeline == []

    def test_replay_three_sessions(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", threads=[{"id": "t1"}], next_action="A",
                                  summary="first", session_dir=sd, hydration_dir=hd)
        close_session_with_packet("s2", threads=[{"id": "t1"}, {"id": "t2"}], next_action="B",
                                  summary="second", session_dir=sd, hydration_dir=hd)
        close_session_with_packet("s3", next_action="C", summary="third",
                                  session_dir=sd, hydration_dir=hd)

        timeline, ok, errors = replay_session_chain(sd, hd)
        assert ok, errors
        assert len(timeline) == 3
        assert timeline[0]["session_id"] == "s1"
        assert timeline[1]["threads"] == 2
        assert timeline[2]["next_action"] == "C"

    def test_replay_detects_missing_packet(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", next_action="A", session_dir=sd, hydration_dir=hd)
        # Delete the packet file
        os.remove(os.path.join(hd, "s1.json"))

        timeline, ok, errors = replay_session_chain(sd, hd)
        assert not ok
        assert any("Missing" in e for e in errors)


# ===================================================================
# Session diff
# ===================================================================

class TestSessionDiff:
    def test_diff_shows_added_thread(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", threads=[{"id": "t1", "title": "A"}],
                                  next_action="X", session_dir=sd, hydration_dir=hd)
        close_session_with_packet("s2", threads=[{"id": "t1", "title": "A"}, {"id": "t2", "title": "B"}],
                                  next_action="Y", session_dir=sd, hydration_dir=hd)

        diff = session_diff("s1", "s2", hd)
        assert diff is not None
        assert len(diff["threads_added"]) == 1
        assert diff["threads_added"][0]["id"] == "t2"
        assert diff["next_action_changed"] is True
        assert diff["to_next"] == "Y"

    def test_diff_shows_removed_thread(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", threads=[{"id": "t1"}, {"id": "t2"}],
                                  session_dir=sd, hydration_dir=hd)
        close_session_with_packet("s2", threads=[{"id": "t1"}],
                                  session_dir=sd, hydration_dir=hd)

        diff = session_diff("s1", "s2", hd)
        assert len(diff["threads_removed"]) == 1

    def test_diff_no_change(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", threads=[{"id": "t1"}], next_action="X",
                                  session_dir=sd, hydration_dir=hd)
        close_session_with_packet("s2", threads=[{"id": "t1"}], next_action="X",
                                  session_dir=sd, hydration_dir=hd)

        diff = session_diff("s1", "s2", hd)
        assert diff["threads_added"] == []
        assert diff["threads_removed"] == []
        assert diff["next_action_changed"] is False

    def test_diff_nonexistent_session(self, dirs):
        sd, hd = dirs
        diff = session_diff("nonexistent_a", "nonexistent_b", hd)
        assert diff is None


# ===================================================================
# Authority
# ===================================================================

class TestAuthority:
    def test_session_record_authority(self, dirs):
        sd, hd = dirs
        _, record = close_session_with_packet("s1", session_dir=sd, hydration_dir=hd)
        assert record.authority == "NONE"

    def test_open_context_authority(self, dirs):
        sd, hd = dirs
        close_session_with_packet("s1", next_action="test", session_dir=sd, hydration_dir=hd)
        ctx, valid, _ = open_session_from_chain(sd, hd)
        assert ctx["authority"] == "NONE"
