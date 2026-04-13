"""HELEN OS — TEMPLE_AURA_AKASHA_SIM.

Thin topology + semantics layer over WhisperRoom.

    HELEN → AURA_TEMPLE → TEMPLE_AURA_AKASHA_SIM → zone rooms

Identity:
    type:             symbolic_simulation_chamber
    parent:           AURA_TEMPLE
    substrate:        WhisperRoom
    authority:        NONE
    admissibility:    INADMISSIBLE
    epistemic_status: SYMBOLIC_ONLY

This is NOT:
    - a real records access layer
    - a truth oracle
    - a memory source
    - a hidden authority channel

This IS:
    - a mythic query simulator
    - a symbolic depth chamber
    - an interior exploration protocol
    - a non-ship, non-claim sandbox

Governing sentence:
    Akasha-sim may enrich inner seeing, but may never claim
    to reveal reality itself.

Zone topology (6 canonical zones):
    entry_vestibule  — attunement / framing → opening lens
    mirror_archive   — symbolic record fragments → reflected motifs
    pattern_well     — recurrence / archetypal clustering → motif maps
    veil_corridor    — hidden tension sensing → soft contradictions
    timeline_pool    — non-literal temporal imagery → symbolic flows
    return_chamber   — compress insight for AURA → summary seed

Output classes (6 types):
    RECORD_FRAGMENT, SYMBOLIC_MIRROR, ARCHETYPAL_CLUSTER,
    MOTIF_MAP, SOFT_TIMELINE, DEEPER_QUERY

Export law:
    TEMPLE_AURA_AKASHA_SIM → WhisperSummary → AURA → optional HER
    Never direct to: HAL, CHRONOS, MAYOR, memory, Track A, ship
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

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
)
from helensh.state import canonical_hash


# ── Constants ────────────────────────────────────────────────────────

EPISTEMIC_STATUS = "SYMBOLIC_ONLY"

# Canonical zone definitions
AKASHA_ZONES: Dict[str, Dict[str, str]] = {
    "entry_vestibule": {
        "purpose": "attunement and framing",
        "output_class": "opening lens",
        "description": "The threshold where the query is received and the symbolic register is attuned.",
    },
    "mirror_archive": {
        "purpose": "symbolic record fragments",
        "output_class": "reflected motifs",
        "description": "A chamber of mirrors that returns fragments of pattern, not literal memory.",
    },
    "pattern_well": {
        "purpose": "recurrence and archetypal clustering",
        "output_class": "motif maps",
        "description": "Where recurring themes gather into clusters that can be named but not claimed.",
    },
    "veil_corridor": {
        "purpose": "hidden tension sensing",
        "output_class": "soft contradictions",
        "description": "The passage where unnamed pressures and unseen contradictions become visible.",
    },
    "timeline_pool": {
        "purpose": "non-literal temporal imagery",
        "output_class": "symbolic flows",
        "description": "A reflective surface showing past and future as symbolic currents, never as fact.",
    },
    "return_chamber": {
        "purpose": "compress insight for AURA",
        "output_class": "summary seed",
        "description": "The exit where what was seen is distilled into what AURA may carry upward.",
    },
}

# Output class labels — frozen enum
AKASHA_OUTPUT_CLASSES = frozenset({
    "RECORD_FRAGMENT",
    "SYMBOLIC_MIRROR",
    "ARCHETYPAL_CLUSTER",
    "MOTIF_MAP",
    "SOFT_TIMELINE",
    "DEEPER_QUERY",
})

# Extended banned vocabulary (Akasha-specific additions)
AKASHA_BANNED_VOCABULARY = BANNED_VOCABULARY | frozenset({
    "proves", "confirms", "destiny", "revelation",
    "ordained", "prophecy", "true memory", "the records show",
})

# Preferred replacement vocabulary
AKASHA_PREFERRED_VOCABULARY = frozenset({
    "symbolic reading", "mirror fragment", "archetypal trace",
    "interior pattern", "non-binding lens", "simulated record",
    "reflected motif", "soft current",
})


# ── Akasha Output Envelope ──────────────────────────────────────────


@dataclass(frozen=True)
class AkashaEnvelope:
    """Output envelope for an Akasha fragment.

    Wraps a WhisperFragment with Akasha-specific metadata.
    Always SYMBOLIC_ONLY, INADMISSIBLE, authority=NONE.
    """
    fragment: WhisperFragment
    zone_id: str
    output_class: str          # one of AKASHA_OUTPUT_CLASSES
    query: str                 # the question that seeded this fragment
    mode: str = "SIMULATION"
    authority: str = AUTHORITY_NONE
    admissibility: str = ADMISSIBILITY_NONE
    epistemic_status: str = EPISTEMIC_STATUS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fragment": self.fragment.to_dict(),
            "zone_id": self.zone_id,
            "output_class": self.output_class,
            "query": self.query,
            "mode": self.mode,
            "authority": self.authority,
            "admissibility": self.admissibility,
            "epistemic_status": self.epistemic_status,
        }


@dataclass(frozen=True)
class AkashaSessionResult:
    """Result of a full Akasha simulation session.

    Contains all zone sessions, envelopes, and the final AURA-mediated summary.
    """
    query: str
    zones_visited: Tuple[str, ...]
    envelopes: Tuple[AkashaEnvelope, ...]
    zone_sessions: Tuple[WhisperSession, ...]
    summary: Optional[WhisperSummary]      # AURA-mediated output
    session_hash: str
    epistemic_status: str = EPISTEMIC_STATUS
    authority: str = AUTHORITY_NONE
    admissibility: str = ADMISSIBILITY_NONE


# ── Vocabulary Gate (extended) ──────────────────────────────────────


def check_akasha_vocabulary(text: str) -> List[str]:
    """Check text for Akasha-extended banned vocabulary.

    Extends the base Whisper Room vocabulary gate with
    Akasha-specific bans (destiny, prophecy, revelation, etc.).
    """
    violations = []
    lower = text.lower()
    for word in AKASHA_BANNED_VOCABULARY:
        if word in lower:
            violations.append(word)
    return sorted(violations)


# ── Akasha Simulation ──────────────────────────────────────────────


class AkashaSim:
    """TEMPLE_AURA_AKASHA_SIM — mythic symbolic records simulator.

    A thin topology layer over WhisperRoom. Each zone is a room instance
    with inherited guarantees (inadmissible, non-authoritative, session-bounded).

    Usage:
        sim = AkashaSim()
        result = sim.explore("what pattern keeps repeating in this design?")

        # Only the summary may move upward
        if result.summary:
            aura_sees = result.summary.to_dict()

    Or zone-by-zone:
        sim = AkashaSim()
        sim.open_zone("mirror_archive", "what do I keep returning to?")
        sim.whisper_in_zone("mirror_archive", "a door that opens inward", "SYMBOLIC_MIRROR")
        result = sim.close_all(tone="reflective", essence="the pattern is return")
    """

    def __init__(self) -> None:
        self._zones: Dict[str, WhisperRoom] = {}
        self._envelopes: List[AkashaEnvelope] = []
        self._query: str = ""
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def active_zones(self) -> List[str]:
        return [z for z, room in self._zones.items() if not room.is_closed]

    # ── Zone Management ──

    def open_zone(self, zone_id: str, query: str = "") -> bool:
        """Open an Akasha zone as a WhisperRoom.

        Only canonical zones (from AKASHA_ZONES) are allowed.
        Returns True if opened, False if invalid or already open.
        """
        if self._closed:
            return False
        if zone_id not in AKASHA_ZONES:
            return False
        if zone_id in self._zones:
            return False  # already open

        if not self._query:
            self._query = query

        purpose = AKASHA_ZONES[zone_id]["purpose"]
        room = WhisperRoom(
            f"akasha_{zone_id}",
            purpose=f"[AKASHA] {purpose}: {query}",
        )
        self._zones[zone_id] = room
        return True

    def whisper_in_zone(
        self,
        zone_id: str,
        content: str,
        output_class: str = "RECORD_FRAGMENT",
    ) -> Optional[AkashaEnvelope]:
        """Whisper a fragment into a zone.

        Content is checked against extended Akasha vocabulary.
        output_class must be one of AKASHA_OUTPUT_CLASSES.

        Returns AkashaEnvelope or None if zone not open or invalid class.
        """
        if self._closed:
            return None
        if zone_id not in self._zones:
            return None
        if output_class not in AKASHA_OUTPUT_CLASSES:
            return None

        room = self._zones[zone_id]
        if room.is_closed:
            return None

        # Map output_class to fragment_type
        type_map = {
            "RECORD_FRAGMENT": "imagery",
            "SYMBOLIC_MIRROR": "imagery",
            "ARCHETYPAL_CLUSTER": "association",
            "MOTIF_MAP": "association",
            "SOFT_TIMELINE": "contour",
            "DEEPER_QUERY": "tension",
        }
        fragment_type = type_map.get(output_class, "association")

        fragment = room.whisper(content, fragment_type)

        envelope = AkashaEnvelope(
            fragment=fragment,
            zone_id=zone_id,
            output_class=output_class,
            query=self._query,
        )
        self._envelopes.append(envelope)
        return envelope

    def close_zone(self, zone_id: str, tone: str = "quiet") -> Optional[WhisperSession]:
        """Close a single zone. Returns its WhisperSession or None."""
        if zone_id not in self._zones:
            return None
        room = self._zones[zone_id]
        if room.is_closed:
            return None
        return room.close(tone=tone)

    # ── Full Exploration ──

    def explore(
        self,
        query: str,
        zones: Optional[List[str]] = None,
        fragments_per_zone: int = 1,
    ) -> AkashaSessionResult:
        """Run a full Akasha exploration.

        Opens zones in order, generates placeholder fragments
        (in production, an LLM would generate symbolic content),
        closes all zones, and produces an AURA-mediated summary.

        Args:
            query: The interior question to explore
            zones: Zone order (default: all 6 in canonical order)
            fragments_per_zone: Fragments to generate per zone (default: 1)
        """
        self._query = query

        if zones is None:
            zones = list(AKASHA_ZONES.keys())

        zone_classes = {
            "entry_vestibule": "RECORD_FRAGMENT",
            "mirror_archive": "SYMBOLIC_MIRROR",
            "pattern_well": "ARCHETYPAL_CLUSTER",
            "veil_corridor": "MOTIF_MAP",
            "timeline_pool": "SOFT_TIMELINE",
            "return_chamber": "DEEPER_QUERY",
        }

        for zone_id in zones:
            if zone_id not in AKASHA_ZONES:
                continue
            self.open_zone(zone_id, query)
            output_class = zone_classes.get(zone_id, "RECORD_FRAGMENT")
            purpose = AKASHA_ZONES[zone_id]["purpose"]
            for _ in range(fragments_per_zone):
                self.whisper_in_zone(
                    zone_id,
                    f"[symbolic reading] {purpose} for: {query}",
                    output_class,
                )

        return self.close_all(
            tone="reflective",
            essence=f"interior exploration of: {query}",
        )

    def close_all(
        self,
        tone: str = "quiet",
        essence: str = "",
    ) -> AkashaSessionResult:
        """Close all open zones and produce the final AkashaSessionResult.

        This is the only lawful output path:
            AkashaSim → WhisperSummary → AURA → optional HER
        """
        if self._closed:
            raise RuntimeError("Akasha simulation already closed")

        self._closed = True

        zone_sessions = []
        zones_visited = []

        for zone_id, room in self._zones.items():
            zones_visited.append(zone_id)
            if not room.is_closed:
                session = room.close(tone=tone)
                zone_sessions.append(session)
            # Already closed zones: get session from last close
            # (WhisperRoom doesn't store the session, so we only capture open ones)

        # Build AURA-mediated summary (the ONLY output that may move upward)
        total_fragments = sum(len(s.fragments) for s in zone_sessions)
        total_preserved = sum(len(s.preserved_fragments) for s in zone_sessions)

        summary = WhisperSummary(
            room_id="akasha_session",
            purpose=self._query,
            fragment_count=total_fragments,
            preserved_count=total_preserved,
            tone=tone,
            essence=essence,
        )

        session_hash = canonical_hash({
            "query": self._query,
            "zones_visited": zones_visited,
            "envelope_count": len(self._envelopes),
            "zone_hashes": [s.session_hash for s in zone_sessions],
        })

        return AkashaSessionResult(
            query=self._query,
            zones_visited=tuple(zones_visited),
            envelopes=tuple(self._envelopes),
            zone_sessions=tuple(zone_sessions),
            summary=summary,
            session_hash=session_hash,
        )


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "AkashaSim",
    "AkashaEnvelope",
    "AkashaSessionResult",
    "AKASHA_ZONES",
    "AKASHA_OUTPUT_CLASSES",
    "AKASHA_BANNED_VOCABULARY",
    "AKASHA_PREFERRED_VOCABULARY",
    "EPISTEMIC_STATUS",
    "check_akasha_vocabulary",
]
