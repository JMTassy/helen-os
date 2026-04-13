"""HELEN OS — AURA Whisper Room Tests.

Tests for the sandbox-within-sandbox: HELEN → AURA Temple → Whisper Room.

The governing sentence: the deeper the room, the weaker the admissibility.

Properties proven:
    1. Fragments are always INTERIOR_ONLY, INADMISSIBLE, authority=NONE
    2. Banned vocabulary is detected on every fragment
    3. Room is session-bounded: closes once, no more writes
    4. Preservation requires explicit label from allowed set
    5. Non-preserved fragments decay (not in preserved_fragments)
    6. Summary is the ONLY output mediated upward
    7. Summary carries INTERIOR_ONLY + INADMISSIBLE + NONE
    8. Receipt chain links unbroken from whisper_genesis
    9. Session hash is deterministic
    10. Closed room rejects new whispers

Test classes:
    1. TestWhisperFragment        — Fragment dataclass + invariants
    2. TestWhisperSummary         — Summary dataclass + labels
    3. TestVocabularyGate         — Banned vocabulary detection
    4. TestWhisperRoomLifecycle   — Open → whisper → close lifecycle
    5. TestWhisperRoomPreservation — Preservation rules
    6. TestWhisperRoomBoundary    — Session boundary enforcement
    7. TestWhisperRoomReceipts    — Receipt chain integrity
    8. TestWhisperRoomAdmissibility — All outputs inadmissible
    9. TestWhisperRoomEdgeCases   — Empty rooms, large content
"""
import pytest

from helensh.sandbox.whisper_room import (
    WhisperRoom,
    WhisperFragment,
    WhisperSummary,
    WhisperSession,
    check_vocabulary,
    BANNED_VOCABULARY,
    PRESERVATION_LABELS,
    INTERIOR_ONLY,
    ADMISSIBILITY_NONE,
    AUTHORITY_NONE,
    WHISPER_GENESIS,
)


# ── 1. WhisperFragment ─────────────────────────────────────────────


class TestWhisperFragment:
    """Fragment dataclass and structural invariants."""

    def test_fragment_fields(self):
        f = WhisperFragment(
            fragment_id=0, room_id="room_mirror",
            content="a mirror that shows not you but who you were",
            fragment_type="imagery",
        )
        assert f.fragment_id == 0
        assert f.room_id == "room_mirror"
        assert f.fragment_type == "imagery"

    def test_fragment_default_status(self):
        f = WhisperFragment(
            fragment_id=0, room_id="r", content="c", fragment_type="t",
        )
        assert f.status == INTERIOR_ONLY
        assert f.authority == AUTHORITY_NONE
        assert f.admissibility == ADMISSIBILITY_NONE

    def test_fragment_frozen(self):
        f = WhisperFragment(
            fragment_id=0, room_id="r", content="c", fragment_type="t",
        )
        with pytest.raises(AttributeError):
            f.status = "PROMOTED"

    def test_fragment_default_preservation_none(self):
        f = WhisperFragment(
            fragment_id=0, room_id="r", content="c", fragment_type="t",
        )
        assert f.preservation is None

    def test_fragment_to_dict(self):
        f = WhisperFragment(
            fragment_id=0, room_id="r", content="hello", fragment_type="association",
        )
        d = f.to_dict()
        assert d["status"] == INTERIOR_ONLY
        assert d["authority"] == AUTHORITY_NONE
        assert d["admissibility"] == ADMISSIBILITY_NONE
        assert d["preservation"] is None

    def test_fragment_to_dict_keys(self):
        f = WhisperFragment(
            fragment_id=0, room_id="r", content="c", fragment_type="t",
        )
        expected_keys = {
            "fragment_id", "room_id", "content", "fragment_type",
            "status", "authority", "admissibility", "preservation",
            "receipt_hash",
        }
        assert set(f.to_dict().keys()) == expected_keys


# ── 2. WhisperSummary ──────────────────────────────────────────────


class TestWhisperSummary:
    """Summary dataclass and labels."""

    def test_summary_fields(self):
        s = WhisperSummary(
            room_id="room_threshold",
            purpose="explore crossing",
            fragment_count=3,
            preserved_count=1,
            tone="liminal",
            essence="thresholds hold memory",
        )
        assert s.room_id == "room_threshold"
        assert s.tone == "liminal"
        assert s.fragment_count == 3

    def test_summary_always_interior_only(self):
        s = WhisperSummary(
            room_id="r", purpose="p",
            fragment_count=0, preserved_count=0,
            tone="t", essence="e",
        )
        assert s.status == INTERIOR_ONLY
        assert s.authority == AUTHORITY_NONE
        assert s.admissibility == ADMISSIBILITY_NONE

    def test_summary_to_dict(self):
        s = WhisperSummary(
            room_id="r", purpose="p",
            fragment_count=5, preserved_count=2,
            tone="warm", essence="hello",
        )
        d = s.to_dict()
        assert d["status"] == INTERIOR_ONLY
        assert d["admissibility"] == ADMISSIBILITY_NONE

    def test_summary_frozen(self):
        s = WhisperSummary(
            room_id="r", purpose="p",
            fragment_count=0, preserved_count=0,
            tone="t", essence="e",
        )
        with pytest.raises(AttributeError):
            s.admissibility = "ADMISSIBLE"


# ── 3. Vocabulary Gate ─────────────────────────────────────────────


class TestVocabularyGate:
    """Banned vocabulary detection."""

    def test_clean_content(self):
        assert check_vocabulary("a soft wind through an open door") == []

    def test_detects_proof(self):
        v = check_vocabulary("this is proof that something works")
        assert "proof" in v

    def test_detects_ready(self):
        v = check_vocabulary("this idea is ready to ship")
        assert "ready" in v
        assert "ship" in v

    def test_detects_validated(self):
        v = check_vocabulary("the concept has been validated and approved")
        assert "validated" in v
        assert "approved" in v

    def test_detects_promote(self):
        v = check_vocabulary("we should promote this evidence")
        assert "promote" in v
        assert "evidence" in v

    def test_case_insensitive(self):
        v = check_vocabulary("This is PROOF and READY")
        assert "proof" in v
        assert "ready" in v

    def test_detects_all_banned_words(self):
        """Each banned word is individually detectable."""
        for word in BANNED_VOCABULARY:
            v = check_vocabulary(f"the {word} is here")
            assert word in v, f"failed to detect '{word}'"

    def test_empty_string_clean(self):
        assert check_vocabulary("") == []

    def test_returns_sorted(self):
        v = check_vocabulary("ship the proof now, it is ready and validated")
        assert v == sorted(v)


# ── 4. Lifecycle ───────────────────────────────────────────────────


class TestWhisperRoomLifecycle:
    """Open → whisper → close lifecycle."""

    def test_create_room(self):
        room = WhisperRoom("room_mirror", purpose="explore reflection")
        assert room.room_id == "room_mirror"
        assert room.purpose == "explore reflection"
        assert room.is_closed is False

    def test_whisper_returns_fragment(self):
        room = WhisperRoom("r", purpose="p")
        f = room.whisper("a door that remembers", "imagery")
        assert isinstance(f, WhisperFragment)
        assert f.content == "a door that remembers"
        assert f.fragment_type == "imagery"

    def test_whisper_increments_fragments(self):
        room = WhisperRoom("r")
        room.whisper("one", "association")
        room.whisper("two", "association")
        room.whisper("three", "association")
        assert room.fragment_count == 3

    def test_close_returns_session(self):
        room = WhisperRoom("room_lantern", purpose="warmth")
        room.whisper("golden light from within", "imagery")
        session = room.close(tone="warm", essence="light held gently")
        assert isinstance(session, WhisperSession)
        assert session.room_id == "room_lantern"
        assert session.purpose == "warmth"

    def test_close_includes_all_fragments(self):
        room = WhisperRoom("r")
        room.whisper("a", "association")
        room.whisper("b", "naming")
        room.whisper("c", "tension")
        session = room.close()
        assert len(session.fragments) == 3

    def test_close_produces_summary(self):
        room = WhisperRoom("r", purpose="test")
        room.whisper("content", "association")
        session = room.close(tone="quiet", essence="stillness")
        assert session.summary is not None
        assert session.summary.tone == "quiet"
        assert session.summary.essence == "stillness"

    def test_session_hash_is_hex(self):
        room = WhisperRoom("r")
        room.whisper("x", "association")
        session = room.close()
        assert len(session.session_hash) == 64
        assert all(c in "0123456789abcdef" for c in session.session_hash)


# ── 5. Preservation ───────────────────────────────────────────────


class TestWhisperRoomPreservation:
    """Preservation rules for fragments."""

    def test_preserve_with_valid_label(self):
        room = WhisperRoom("r")
        room.whisper("keep this", "imagery")
        assert room.preserve(0, "symbolic_scrap") is True

    def test_preserve_invalid_label_rejected(self):
        room = WhisperRoom("r")
        room.whisper("x", "association")
        assert room.preserve(0, "PROMOTED") is False

    def test_preserve_invalid_id_rejected(self):
        room = WhisperRoom("r")
        room.whisper("x", "association")
        assert room.preserve(99, "symbolic_scrap") is False

    def test_preserved_in_session(self):
        room = WhisperRoom("r")
        room.whisper("keep", "imagery")
        room.whisper("discard", "association")
        room.preserve(0, "aesthetic_note")
        session = room.close()
        assert len(session.preserved_fragments) == 1
        assert session.preserved_fragments[0].preservation == "aesthetic_note"

    def test_non_preserved_not_in_preserved(self):
        room = WhisperRoom("r")
        room.whisper("ephemeral", "association")
        session = room.close()
        assert len(session.preserved_fragments) == 0

    def test_all_preservation_labels_accepted(self):
        for label in PRESERVATION_LABELS:
            room = WhisperRoom("r")
            room.whisper("content", "association")
            assert room.preserve(0, label) is True

    def test_preserved_fragment_still_inadmissible(self):
        """Even preserved fragments remain INADMISSIBLE."""
        room = WhisperRoom("r")
        room.whisper("kept", "imagery")
        room.preserve(0, "interior_draft")
        session = room.close()
        pf = session.preserved_fragments[0]
        assert pf.status == INTERIOR_ONLY
        assert pf.admissibility == ADMISSIBILITY_NONE
        assert pf.authority == AUTHORITY_NONE


# ── 6. Session Boundary ───────────────────────────────────────────


class TestWhisperRoomBoundary:
    """Session boundary enforcement."""

    def test_closed_room_rejects_whisper(self):
        room = WhisperRoom("r")
        room.whisper("x", "association")
        room.close()
        with pytest.raises(RuntimeError, match="closed"):
            room.whisper("late arrival", "association")

    def test_closed_room_rejects_double_close(self):
        room = WhisperRoom("r")
        room.close()
        with pytest.raises(RuntimeError, match="already closed"):
            room.close()

    def test_is_closed_true_after_close(self):
        room = WhisperRoom("r")
        assert room.is_closed is False
        room.close()
        assert room.is_closed is True

    def test_session_not_expired_by_default(self):
        room = WhisperRoom("r")
        session = room.close()
        assert session.expired is False


# ── 7. Receipts ───────────────────────────────────────────────────


class TestWhisperRoomReceipts:
    """Receipt chain integrity."""

    def test_receipt_per_fragment_plus_close(self):
        room = WhisperRoom("r")
        room.whisper("a", "association")
        room.whisper("b", "naming")
        room.whisper("c", "tension")
        session = room.close()
        # 3 fragments + 1 close = 4 receipts
        assert len(session.receipt_chain) == 4

    def test_receipt_chain_starts_from_genesis(self):
        room = WhisperRoom("r")
        room.whisper("x", "association")
        session = room.close()
        assert session.receipt_chain[0]["previous_hash"] == WHISPER_GENESIS

    def test_receipt_chain_linked(self):
        room = WhisperRoom("r")
        room.whisper("a", "association")
        room.whisper("b", "imagery")
        room.whisper("c", "naming")
        session = room.close()
        chain = session.receipt_chain
        for i in range(1, len(chain)):
            assert chain[i]["previous_hash"] == chain[i-1]["receipt_hash"]

    def test_verify_chain(self):
        room = WhisperRoom("r")
        room.whisper("a", "association")
        room.whisper("b", "association")
        room.whisper("c", "association")
        room.close()
        assert room.verify_chain() is True

    def test_all_receipts_authority_false(self):
        room = WhisperRoom("r")
        room.whisper("a", "association")
        room.whisper("b", "imagery")
        session = room.close()
        for receipt in session.receipt_chain:
            assert receipt["authority"] is False

    def test_fragment_receipt_type(self):
        room = WhisperRoom("r")
        room.whisper("x", "association")
        session = room.close()
        assert session.receipt_chain[0]["type"] == "WHISPER_FRAGMENT"

    def test_close_receipt_type(self):
        room = WhisperRoom("r")
        room.whisper("x", "association")
        session = room.close()
        assert session.receipt_chain[-1]["type"] == "WHISPER_CLOSE"

    def test_vocabulary_violations_recorded(self):
        """Banned vocabulary in content is recorded in receipt."""
        room = WhisperRoom("r")
        room.whisper("this is proof of something", "association")
        session = room.close()
        frag_receipt = session.receipt_chain[0]
        assert "vocabulary_violations" in frag_receipt
        assert "proof" in frag_receipt["vocabulary_violations"]


# ── 8. Admissibility ──────────────────────────────────────────────


class TestWhisperRoomAdmissibility:
    """All outputs carry INTERIOR_ONLY + INADMISSIBLE + NONE."""

    def test_every_fragment_inadmissible(self):
        room = WhisperRoom("r")
        for i in range(5):
            room.whisper(f"fragment {i}", "association")
        session = room.close()
        for f in session.fragments:
            assert f.status == INTERIOR_ONLY
            assert f.admissibility == ADMISSIBILITY_NONE
            assert f.authority == AUTHORITY_NONE

    def test_summary_inadmissible(self):
        room = WhisperRoom("r")
        room.whisper("x", "association")
        session = room.close(tone="soft", essence="a quiet thing")
        assert session.summary.status == INTERIOR_ONLY
        assert session.summary.admissibility == ADMISSIBILITY_NONE
        assert session.summary.authority == AUTHORITY_NONE

    def test_preserved_fragments_inadmissible(self):
        room = WhisperRoom("r")
        room.whisper("keep", "imagery")
        room.preserve(0, "symbolic_scrap")
        session = room.close()
        for pf in session.preserved_fragments:
            assert pf.admissibility == ADMISSIBILITY_NONE

    def test_admissibility_cannot_be_mutated(self):
        room = WhisperRoom("r")
        f = room.whisper("x", "association")
        with pytest.raises(AttributeError):
            f.admissibility = "ADMISSIBLE"

    def test_all_receipts_inadmissible(self):
        room = WhisperRoom("r")
        room.whisper("a", "imagery")
        session = room.close()
        for receipt in session.receipt_chain:
            assert receipt.get("admissibility", ADMISSIBILITY_NONE) == ADMISSIBILITY_NONE


# ── 9. Edge Cases ─────────────────────────────────────────────────


class TestWhisperRoomEdgeCases:
    """Empty rooms, large content, multiple rooms."""

    def test_empty_room_closes(self):
        room = WhisperRoom("r")
        session = room.close()
        assert len(session.fragments) == 0
        assert len(session.preserved_fragments) == 0
        # Only close receipt
        assert len(session.receipt_chain) == 1

    def test_large_content(self):
        room = WhisperRoom("r")
        room.whisper("x" * 10_000, "atmosphere")
        session = room.close()
        assert len(session.fragments[0].content) == 10_000

    def test_multiple_rooms_independent(self):
        room1 = WhisperRoom("room_mirror")
        room2 = WhisperRoom("room_lantern")
        room1.whisper("reflection", "imagery")
        room2.whisper("warmth", "imagery")
        s1 = room1.close()
        s2 = room2.close()
        assert s1.room_id != s2.room_id
        assert s1.session_hash != s2.session_hash

    def test_all_fragment_types(self):
        types = ["imagery", "association", "contour", "naming", "atmosphere", "tension"]
        room = WhisperRoom("r")
        for t in types:
            room.whisper(f"content for {t}", t)
        session = room.close()
        assert len(session.fragments) == len(types)

    def test_session_deterministic(self):
        """Same content → same session hash."""
        def _make():
            room = WhisperRoom("r", purpose="test")
            room.whisper("a", "imagery")
            room.whisper("b", "naming")
            return room.close()
        s1 = _make()
        s2 = _make()
        assert s1.session_hash == s2.session_hash
