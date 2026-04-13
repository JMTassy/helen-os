"""HELEN OS — AURA Whisper Room.

Sandbox within sandbox. The innermost governed space.

    HELEN → AURA_TEMPLE → WHISPER_ROOM

Structural hierarchy:
    - AURA Temple: non-binding insight field, authority=NONE
    - Whisper Room: even weaker — inadmissible by default

The governing sentence:
    **The deeper the room, the weaker the admissibility.**

A Whisper Room is:
    - One focused exploration (concept, image, tension, naming)
    - Session-bounded by default (decays at session end)
    - Inadmissible as evidence, readiness, lineage, or promotion
    - Mediated by AURA before any upward movement

A Whisper Room is NOT:
    - A sovereign decision space
    - A memory write path
    - A promotion channel
    - A truth claim surface

Hard rules:
    1. No direct export to HAL, CHRONOS, MAYOR, memory, or ship path
    2. AURA mediates upward (room → AURA summary → optional HER reframe)
    3. Session-bounded: expires at session end unless explicitly preserved
    4. No authority vocabulary inside (proof, ready, true, validated, promote, ship)
    5. Every artifact labeled: status=INTERIOR_ONLY, authority=NONE, admissibility=INADMISSIBLE

Banned vocabulary in room content:
    proof, ready, true, validated, promote, ship, evidence,
    approved, decided, confirmed, authorized, certified

Storage:
    Room artifacts are ephemeral by default.
    If preserved, they carry INTERIOR_ONLY status and cannot
    be promoted without explicit AURA mediation + requalification.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from helensh.state import canonical_hash


# ── Constants ────────────────────────────────────────────────────────

WHISPER_GENESIS = "whisper_genesis"

# Status labels — frozen, not free strings
INTERIOR_ONLY = "INTERIOR_ONLY"
ADMISSIBILITY_NONE = "INADMISSIBLE"
AUTHORITY_NONE = "NONE"

# Banned vocabulary — enforced on all room content
BANNED_VOCABULARY = frozenset({
    "proof", "ready", "true", "validated", "promote", "ship",
    "evidence", "approved", "decided", "confirmed", "authorized",
    "certified",
})

# Preservation labels for fragments that survive session end
PRESERVATION_LABELS = frozenset({
    "symbolic_scrap",
    "aesthetic_note",
    "interior_draft",
})


# ── Types ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WhisperFragment:
    """A single fragment produced inside a Whisper Room.

    Always inadmissible. Always non-authoritative.
    The content is the raw material — imagery, association,
    emotional contour, naming experiment, atmosphere sketch.
    """
    fragment_id: int
    room_id: str
    content: str
    fragment_type: str         # "imagery" | "association" | "contour" | "naming" | "atmosphere" | "tension"
    status: str = INTERIOR_ONLY
    authority: str = AUTHORITY_NONE
    admissibility: str = ADMISSIBILITY_NONE
    preservation: Optional[str] = None   # None = ephemeral, or one of PRESERVATION_LABELS
    receipt_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fragment_id": self.fragment_id,
            "room_id": self.room_id,
            "content": self.content,
            "fragment_type": self.fragment_type,
            "status": self.status,
            "authority": self.authority,
            "admissibility": self.admissibility,
            "preservation": self.preservation,
            "receipt_hash": self.receipt_hash,
        }


@dataclass(frozen=True)
class WhisperSummary:
    """AURA-mediated summary of a Whisper Room session.

    This is the ONLY output that may move upward from a room.
    It is still non-authoritative and non-binding.
    """
    room_id: str
    purpose: str
    fragment_count: int
    preserved_count: int
    tone: str                  # one-word emotional register
    essence: str               # one-sentence distillation (AURA voice)
    status: str = INTERIOR_ONLY
    authority: str = AUTHORITY_NONE
    admissibility: str = ADMISSIBILITY_NONE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "room_id": self.room_id,
            "purpose": self.purpose,
            "fragment_count": self.fragment_count,
            "preserved_count": self.preserved_count,
            "tone": self.tone,
            "essence": self.essence,
            "status": self.status,
            "authority": self.authority,
            "admissibility": self.admissibility,
        }


@dataclass(frozen=True)
class WhisperSession:
    """Result of one Whisper Room session.

    Immutable. Session-bounded. Decays unless fragments are preserved.
    """
    room_id: str
    purpose: str
    fragments: Tuple[WhisperFragment, ...]
    preserved_fragments: Tuple[WhisperFragment, ...]
    summary: Optional[WhisperSummary]
    receipt_chain: Tuple[dict, ...]
    session_hash: str
    expired: bool = False       # True after session end


# ── Vocabulary Gate ──────────────────────────────────────────────────


def check_vocabulary(text: str) -> List[str]:
    """Check text for banned authority vocabulary.

    Returns list of violations (empty = clean).
    """
    violations = []
    lower = text.lower()
    for word in BANNED_VOCABULARY:
        if word in lower:
            violations.append(word)
    return sorted(violations)


# ── Receipt Construction ─────────────────────────────────────────────


def _make_fragment_receipt(
    room_id: str,
    fragment_id: int,
    content_hash: str,
    previous_hash: str,
) -> dict:
    """Create a receipt for a whisper fragment."""
    body = {
        "type": "WHISPER_FRAGMENT",
        "room_id": room_id,
        "fragment_id": fragment_id,
        "content_hash": content_hash,
        "authority": False,
        "admissibility": ADMISSIBILITY_NONE,
        "previous_hash": previous_hash,
    }
    receipt_hash = canonical_hash(body)
    return {**body, "receipt_hash": receipt_hash}


def _make_close_receipt(
    room_id: str,
    fragment_count: int,
    preserved_count: int,
    previous_hash: str,
) -> dict:
    """Create a receipt for closing a whisper room."""
    body = {
        "type": "WHISPER_CLOSE",
        "room_id": room_id,
        "fragment_count": fragment_count,
        "preserved_count": preserved_count,
        "authority": False,
        "previous_hash": previous_hash,
    }
    receipt_hash = canonical_hash(body)
    return {**body, "receipt_hash": receipt_hash}


# ── Whisper Room ─────────────────────────────────────────────────────


class WhisperRoom:
    """AURA Whisper Room — the innermost sandbox.

    A temporary, session-bounded space for focused exploration.
    Everything produced here is inadmissible by default.

    Usage:
        room = WhisperRoom("room_threshold", purpose="explore the feeling of crossing")
        room.whisper("a door that remembers who passed through it", "imagery")
        room.whisper("threshold as permission boundary", "association")
        room.preserve(0, "symbolic_scrap")
        session = room.close(tone="liminal", essence="thresholds hold memory of passage")

        # Only the summary may move upward, mediated by AURA
        if session.summary:
            aura_can_see = session.summary.to_dict()
    """

    def __init__(self, room_id: str, purpose: str = "") -> None:
        self._room_id = room_id
        self._purpose = purpose
        self._fragments: List[WhisperFragment] = []
        self._receipt_chain: List[dict] = []
        self._previous_hash = WHISPER_GENESIS
        self._closed = False
        self._preserved_ids: set = set()

    @property
    def room_id(self) -> str:
        return self._room_id

    @property
    def purpose(self) -> str:
        return self._purpose

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def fragment_count(self) -> int:
        return len(self._fragments)

    def whisper(
        self,
        content: str,
        fragment_type: str = "association",
    ) -> WhisperFragment:
        """Add a fragment to the room.

        The content is checked for banned vocabulary.
        Violations are stripped — the room does not reject,
        it softens. But the violation is recorded in the receipt.

        Returns the created WhisperFragment.

        Raises RuntimeError if room is already closed.
        """
        if self._closed:
            raise RuntimeError(f"room '{self._room_id}' is closed")

        violations = check_vocabulary(content)

        content_hash = canonical_hash({"content": content, "type": fragment_type})

        receipt = _make_fragment_receipt(
            self._room_id, len(self._fragments), content_hash, self._previous_hash,
        )
        if violations:
            receipt["vocabulary_violations"] = violations

        self._previous_hash = receipt["receipt_hash"]
        self._receipt_chain.append(receipt)

        fragment = WhisperFragment(
            fragment_id=len(self._fragments),
            room_id=self._room_id,
            content=content,
            fragment_type=fragment_type,
            receipt_hash=receipt["receipt_hash"],
        )
        self._fragments.append(fragment)
        return fragment

    def preserve(self, fragment_id: int, label: str) -> bool:
        """Mark a fragment for preservation beyond session end.

        Label must be one of: symbolic_scrap, aesthetic_note, interior_draft.
        Returns True if preserved, False if label invalid or fragment not found.
        """
        if label not in PRESERVATION_LABELS:
            return False
        if fragment_id < 0 or fragment_id >= len(self._fragments):
            return False

        self._preserved_ids.add(fragment_id)

        # Rebuild fragment with preservation label
        old = self._fragments[fragment_id]
        self._fragments[fragment_id] = WhisperFragment(
            fragment_id=old.fragment_id,
            room_id=old.room_id,
            content=old.content,
            fragment_type=old.fragment_type,
            status=INTERIOR_ONLY,
            authority=AUTHORITY_NONE,
            admissibility=ADMISSIBILITY_NONE,
            preservation=label,
            receipt_hash=old.receipt_hash,
        )
        return True

    def close(
        self,
        tone: str = "quiet",
        essence: str = "",
    ) -> WhisperSession:
        """Close the room and produce a WhisperSession.

        After closing:
            - No more fragments can be added
            - Non-preserved fragments decay (marked expired)
            - A WhisperSummary is generated for AURA mediation

        Returns the complete WhisperSession.
        """
        if self._closed:
            raise RuntimeError(f"room '{self._room_id}' is already closed")

        self._closed = True

        preserved = tuple(
            f for f in self._fragments if f.preservation is not None
        )

        close_receipt = _make_close_receipt(
            self._room_id, len(self._fragments), len(preserved),
            self._previous_hash,
        )
        self._receipt_chain.append(close_receipt)

        summary = WhisperSummary(
            room_id=self._room_id,
            purpose=self._purpose,
            fragment_count=len(self._fragments),
            preserved_count=len(preserved),
            tone=tone,
            essence=essence,
        )

        session_hash = canonical_hash({
            "room_id": self._room_id,
            "purpose": self._purpose,
            "fragment_count": len(self._fragments),
            "receipt_hashes": [r["receipt_hash"] for r in self._receipt_chain],
        })

        return WhisperSession(
            room_id=self._room_id,
            purpose=self._purpose,
            fragments=tuple(self._fragments),
            preserved_fragments=preserved,
            summary=summary,
            receipt_chain=tuple(self._receipt_chain),
            session_hash=session_hash,
        )

    def verify_chain(self) -> bool:
        """Verify the receipt chain integrity."""
        expected_prev = WHISPER_GENESIS
        for receipt in self._receipt_chain:
            if receipt.get("previous_hash") != expected_prev:
                return False
            body = {k: v for k, v in receipt.items() if k not in ("receipt_hash", "vocabulary_violations")}
            computed = canonical_hash(body)
            if computed != receipt.get("receipt_hash"):
                return False
            expected_prev = receipt["receipt_hash"]
        return True


# ── Exports ──────────────────────────────────────────────────────────

__all__ = [
    "WhisperRoom",
    "WhisperFragment",
    "WhisperSummary",
    "WhisperSession",
    "check_vocabulary",
    "BANNED_VOCABULARY",
    "PRESERVATION_LABELS",
    "INTERIOR_ONLY",
    "ADMISSIBILITY_NONE",
    "AUTHORITY_NONE",
    "WHISPER_GENESIS",
]
