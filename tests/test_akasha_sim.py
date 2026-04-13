"""HELEN OS — TEMPLE_AURA_AKASHA_SIM Tests.

Tests for the symbolic records simulator inside AURA Temple.

Governing sentence:
    Akasha-sim may enrich inner seeing, but may never claim
    to reveal reality itself.

Properties proven:
    1. All outputs carry SYMBOLIC_ONLY + INADMISSIBLE + NONE
    2. Only canonical zones are allowed
    3. Extended vocabulary gate catches Akasha-specific bans
    4. Zone rooms inherit WhisperRoom guarantees
    5. Envelopes wrap fragments with Akasha metadata
    6. Close produces AURA-mediated summary (only lawful export)
    7. Full exploration visits zones in canonical order
    8. Session hash is deterministic
    9. Closed sim rejects new operations
    10. No authority vocabulary escapes

Test classes:
    1. TestAkashaConstants       — Zone definitions, output classes
    2. TestAkashaVocabulary      — Extended vocabulary gate
    3. TestAkashaEnvelope        — Output envelope structure
    4. TestAkashaZoneManagement  — Open, whisper, close zones
    5. TestAkashaExploration     — Full exploration lifecycle
    6. TestAkashaAdmissibility   — All outputs inadmissible
    7. TestAkashaSessionResult   — Session result structure
    8. TestAkashaBoundary        — Session boundary enforcement
    9. TestAkashaDeterminism     — Deterministic session hash
"""
import pytest

from helensh.sandbox.akasha_sim import (
    AkashaSim,
    AkashaEnvelope,
    AkashaSessionResult,
    AKASHA_ZONES,
    AKASHA_OUTPUT_CLASSES,
    AKASHA_BANNED_VOCABULARY,
    AKASHA_PREFERRED_VOCABULARY,
    EPISTEMIC_STATUS,
    check_akasha_vocabulary,
)
from helensh.sandbox.whisper_room import (
    INTERIOR_ONLY,
    ADMISSIBILITY_NONE,
    AUTHORITY_NONE,
    BANNED_VOCABULARY,
)


# ── 1. Constants ───────────────────────────────────────────────────


class TestAkashaConstants:
    """Zone definitions, output classes, vocabulary sets."""

    def test_six_canonical_zones(self):
        assert len(AKASHA_ZONES) == 6

    def test_zone_names(self):
        expected = {
            "entry_vestibule", "mirror_archive", "pattern_well",
            "veil_corridor", "timeline_pool", "return_chamber",
        }
        assert set(AKASHA_ZONES.keys()) == expected

    def test_each_zone_has_purpose(self):
        for zone_id, spec in AKASHA_ZONES.items():
            assert "purpose" in spec, f"{zone_id} missing purpose"
            assert len(spec["purpose"]) > 0

    def test_each_zone_has_output_class(self):
        for zone_id, spec in AKASHA_ZONES.items():
            assert "output_class" in spec, f"{zone_id} missing output_class"

    def test_six_output_classes(self):
        assert len(AKASHA_OUTPUT_CLASSES) == 6

    def test_output_class_names(self):
        expected = {
            "RECORD_FRAGMENT", "SYMBOLIC_MIRROR", "ARCHETYPAL_CLUSTER",
            "MOTIF_MAP", "SOFT_TIMELINE", "DEEPER_QUERY",
        }
        assert AKASHA_OUTPUT_CLASSES == expected

    def test_epistemic_status_symbolic_only(self):
        assert EPISTEMIC_STATUS == "SYMBOLIC_ONLY"

    def test_akasha_vocabulary_extends_base(self):
        """Akasha banned vocabulary is a superset of base Whisper vocabulary."""
        assert BANNED_VOCABULARY.issubset(AKASHA_BANNED_VOCABULARY)

    def test_akasha_has_additional_bans(self):
        extra = AKASHA_BANNED_VOCABULARY - BANNED_VOCABULARY
        assert "prophecy" in extra
        assert "destiny" in extra
        assert "revelation" in extra


# ── 2. Vocabulary Gate ─────────────────────────────────────────────


class TestAkashaVocabulary:
    """Extended vocabulary gate."""

    def test_clean_content(self):
        assert check_akasha_vocabulary("a soft mirror of repeating patterns") == []

    def test_detects_base_bans(self):
        v = check_akasha_vocabulary("this is proof of readiness")
        assert "proof" in v

    def test_detects_prophecy(self):
        v = check_akasha_vocabulary("this prophecy reveals your destiny")
        assert "prophecy" in v
        assert "destiny" in v

    def test_detects_revelation(self):
        v = check_akasha_vocabulary("a revelation ordained by the records")
        assert "revelation" in v
        assert "ordained" in v

    def test_detects_confirms(self):
        v = check_akasha_vocabulary("this confirms the true memory")
        assert "confirms" in v
        assert "true memory" in v

    def test_detects_records_show(self):
        v = check_akasha_vocabulary("the records show this is certain")
        assert "the records show" in v

    def test_preferred_vocabulary_exists(self):
        assert "symbolic reading" in AKASHA_PREFERRED_VOCABULARY
        assert "mirror fragment" in AKASHA_PREFERRED_VOCABULARY
        assert "non-binding lens" in AKASHA_PREFERRED_VOCABULARY


# ── 3. Envelope ───────────────────────────────────────────────────


class TestAkashaEnvelope:
    """Output envelope structure."""

    def test_envelope_from_zone(self):
        sim = AkashaSim()
        sim.open_zone("mirror_archive", "what repeats?")
        env = sim.whisper_in_zone("mirror_archive", "a returning door", "SYMBOLIC_MIRROR")
        assert env is not None
        assert isinstance(env, AkashaEnvelope)

    def test_envelope_metadata(self):
        sim = AkashaSim()
        sim.open_zone("pattern_well", "what clusters?")
        env = sim.whisper_in_zone("pattern_well", "circles in circles", "ARCHETYPAL_CLUSTER")
        assert env.zone_id == "pattern_well"
        assert env.output_class == "ARCHETYPAL_CLUSTER"
        assert env.mode == "SIMULATION"
        assert env.authority == AUTHORITY_NONE
        assert env.admissibility == ADMISSIBILITY_NONE
        assert env.epistemic_status == EPISTEMIC_STATUS

    def test_envelope_to_dict(self):
        sim = AkashaSim()
        sim.open_zone("entry_vestibule", "begin")
        env = sim.whisper_in_zone("entry_vestibule", "opening", "RECORD_FRAGMENT")
        d = env.to_dict()
        assert d["mode"] == "SIMULATION"
        assert d["authority"] == AUTHORITY_NONE
        assert d["epistemic_status"] == "SYMBOLIC_ONLY"
        assert "fragment" in d

    def test_envelope_frozen(self):
        sim = AkashaSim()
        sim.open_zone("veil_corridor", "q")
        env = sim.whisper_in_zone("veil_corridor", "tension", "MOTIF_MAP")
        with pytest.raises(AttributeError):
            env.authority = "FULL"

    def test_invalid_output_class_rejected(self):
        sim = AkashaSim()
        sim.open_zone("mirror_archive", "q")
        env = sim.whisper_in_zone("mirror_archive", "content", "INVALID_CLASS")
        assert env is None


# ── 4. Zone Management ────────────────────────────────────────────


class TestAkashaZoneManagement:
    """Open, whisper, close zones."""

    def test_open_valid_zone(self):
        sim = AkashaSim()
        assert sim.open_zone("mirror_archive", "query") is True

    def test_open_invalid_zone_rejected(self):
        sim = AkashaSim()
        assert sim.open_zone("nonexistent_zone", "query") is False

    def test_open_duplicate_zone_rejected(self):
        sim = AkashaSim()
        sim.open_zone("mirror_archive", "q")
        assert sim.open_zone("mirror_archive", "q") is False

    def test_whisper_in_open_zone(self):
        sim = AkashaSim()
        sim.open_zone("pattern_well", "q")
        env = sim.whisper_in_zone("pattern_well", "content", "ARCHETYPAL_CLUSTER")
        assert env is not None

    def test_whisper_in_closed_zone_returns_none(self):
        sim = AkashaSim()
        sim.open_zone("mirror_archive", "q")
        sim.close_zone("mirror_archive")
        env = sim.whisper_in_zone("mirror_archive", "late", "SYMBOLIC_MIRROR")
        assert env is None

    def test_whisper_in_unopened_zone_returns_none(self):
        sim = AkashaSim()
        env = sim.whisper_in_zone("mirror_archive", "content", "SYMBOLIC_MIRROR")
        assert env is None

    def test_close_zone(self):
        sim = AkashaSim()
        sim.open_zone("entry_vestibule", "q")
        sim.whisper_in_zone("entry_vestibule", "hello", "RECORD_FRAGMENT")
        session = sim.close_zone("entry_vestibule")
        assert session is not None
        assert len(session.fragments) == 1

    def test_active_zones(self):
        sim = AkashaSim()
        sim.open_zone("mirror_archive", "q")
        sim.open_zone("pattern_well", "q")
        assert len(sim.active_zones) == 2
        sim.close_zone("mirror_archive")
        assert len(sim.active_zones) == 1


# ── 5. Full Exploration ───────────────────────────────────────────


class TestAkashaExploration:
    """Full exploration lifecycle."""

    def test_explore_visits_all_zones(self):
        sim = AkashaSim()
        result = sim.explore("what pattern keeps repeating?")
        assert len(result.zones_visited) == 6

    def test_explore_canonical_zone_order(self):
        sim = AkashaSim()
        result = sim.explore("query")
        expected_order = list(AKASHA_ZONES.keys())
        assert list(result.zones_visited) == expected_order

    def test_explore_produces_envelopes(self):
        sim = AkashaSim()
        result = sim.explore("query", fragments_per_zone=1)
        assert len(result.envelopes) == 6  # 1 per zone

    def test_explore_multiple_fragments(self):
        sim = AkashaSim()
        result = sim.explore("query", fragments_per_zone=3)
        assert len(result.envelopes) == 18  # 3 per zone × 6 zones

    def test_explore_subset_of_zones(self):
        sim = AkashaSim()
        result = sim.explore("query", zones=["mirror_archive", "veil_corridor"])
        assert len(result.zones_visited) == 2
        assert "mirror_archive" in result.zones_visited
        assert "veil_corridor" in result.zones_visited

    def test_explore_produces_summary(self):
        sim = AkashaSim()
        result = sim.explore("what do I keep returning to?")
        assert result.summary is not None
        assert result.summary.purpose == "what do I keep returning to?"

    def test_explore_produces_zone_sessions(self):
        sim = AkashaSim()
        result = sim.explore("query")
        assert len(result.zone_sessions) == 6

    def test_explore_has_session_hash(self):
        sim = AkashaSim()
        result = sim.explore("query")
        assert len(result.session_hash) == 64


# ── 6. Admissibility ──────────────────────────────────────────────


class TestAkashaAdmissibility:
    """All outputs carry SYMBOLIC_ONLY + INADMISSIBLE + NONE."""

    def test_envelope_always_inadmissible(self):
        sim = AkashaSim()
        sim.open_zone("mirror_archive", "q")
        env = sim.whisper_in_zone("mirror_archive", "content", "SYMBOLIC_MIRROR")
        assert env.admissibility == ADMISSIBILITY_NONE
        assert env.authority == AUTHORITY_NONE
        assert env.epistemic_status == EPISTEMIC_STATUS

    def test_session_result_always_inadmissible(self):
        sim = AkashaSim()
        result = sim.explore("query")
        assert result.admissibility == ADMISSIBILITY_NONE
        assert result.authority == AUTHORITY_NONE
        assert result.epistemic_status == EPISTEMIC_STATUS

    def test_summary_always_inadmissible(self):
        sim = AkashaSim()
        result = sim.explore("query")
        assert result.summary.status == INTERIOR_ONLY
        assert result.summary.admissibility == ADMISSIBILITY_NONE
        assert result.summary.authority == AUTHORITY_NONE

    def test_all_envelopes_symbolic_only(self):
        sim = AkashaSim()
        result = sim.explore("query", fragments_per_zone=2)
        for env in result.envelopes:
            assert env.epistemic_status == "SYMBOLIC_ONLY"
            assert env.mode == "SIMULATION"
            assert env.admissibility == ADMISSIBILITY_NONE

    def test_all_zone_sessions_inadmissible(self):
        sim = AkashaSim()
        result = sim.explore("query")
        for session in result.zone_sessions:
            for fragment in session.fragments:
                assert fragment.status == INTERIOR_ONLY
                assert fragment.admissibility == ADMISSIBILITY_NONE


# ── 7. Session Result ─────────────────────────────────────────────


class TestAkashaSessionResult:
    """Session result structure."""

    def test_result_has_query(self):
        sim = AkashaSim()
        result = sim.explore("the question")
        assert result.query == "the question"

    def test_result_has_zones(self):
        sim = AkashaSim()
        result = sim.explore("q")
        assert isinstance(result.zones_visited, tuple)

    def test_result_has_envelopes(self):
        sim = AkashaSim()
        result = sim.explore("q")
        assert isinstance(result.envelopes, tuple)
        for env in result.envelopes:
            assert isinstance(env, AkashaEnvelope)

    def test_result_has_zone_sessions(self):
        sim = AkashaSim()
        result = sim.explore("q")
        assert isinstance(result.zone_sessions, tuple)

    def test_result_summary_is_only_export(self):
        """The summary is the only lawful export path."""
        sim = AkashaSim()
        result = sim.explore("q")
        # Summary exists
        assert result.summary is not None
        # Summary carries proper labels
        assert result.summary.status == INTERIOR_ONLY


# ── 8. Boundary Enforcement ──────────────────────────────────────


class TestAkashaBoundary:
    """Session boundary enforcement."""

    def test_closed_sim_rejects_open(self):
        sim = AkashaSim()
        sim.explore("q")  # closes the sim
        assert sim.open_zone("mirror_archive", "late") is False

    def test_closed_sim_rejects_whisper(self):
        sim = AkashaSim()
        sim.explore("q")
        env = sim.whisper_in_zone("mirror_archive", "late", "SYMBOLIC_MIRROR")
        assert env is None

    def test_double_close_raises(self):
        sim = AkashaSim()
        sim.open_zone("entry_vestibule", "q")
        sim.close_all()
        with pytest.raises(RuntimeError, match="already closed"):
            sim.close_all()

    def test_is_closed_after_explore(self):
        sim = AkashaSim()
        assert sim.is_closed is False
        sim.explore("q")
        assert sim.is_closed is True


# ── 9. Determinism ───────────────────────────────────────────────


class TestAkashaDeterminism:
    """Deterministic session hash."""

    def test_same_query_same_hash(self):
        def _run():
            sim = AkashaSim()
            return sim.explore("the same question", fragments_per_zone=1)
        r1 = _run()
        r2 = _run()
        assert r1.session_hash == r2.session_hash

    def test_different_query_different_hash(self):
        sim1 = AkashaSim()
        r1 = sim1.explore("question one")
        sim2 = AkashaSim()
        r2 = sim2.explore("question two")
        assert r1.session_hash != r2.session_hash

    def test_zone_order_affects_hash(self):
        sim1 = AkashaSim()
        r1 = sim1.explore("q", zones=["mirror_archive", "pattern_well"])
        sim2 = AkashaSim()
        r2 = sim2.explore("q", zones=["pattern_well", "mirror_archive"])
        assert r1.session_hash != r2.session_hash
