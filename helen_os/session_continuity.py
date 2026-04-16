"""
HELEN OS Session Continuity — Multi-Session Replay

Bridges sessions via chained MemoryPackets:
  Session A → emit packet → persist → close
  Session B → load last packet → verify chain → hydrate → continue

Core invariant: context at session B start == context at session A end.

Flow:
  close_session_with_packet() → emit + persist + chain
  open_session_from_chain()   → load + verify + reconstruct
  replay_session_chain()      → walk all packets, verify integrity
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from helen_os.memory_hydration import (
    MemoryPacket, emit_boot_memory, verify_packet, verify_chain,
    persist_packet, load_packet, load_and_verify,
    reconstruct_boot_context, GENESIS_DISCLOSURE_HASH,
    _canonical, _hash,
)


# ---------------------------------------------------------------------------
# Session ledger — tracks session lifecycle
# ---------------------------------------------------------------------------

@dataclass
class SessionRecord:
    session_id: str
    started_at: str
    ended_at: Optional[str]
    packet_hash: Optional[str]  # receipt_hash of the closing MemoryPacket
    previous_session: Optional[str]  # session_id of the prior session
    summary: Optional[str]
    authority: str = "NONE"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionRecord":
        return cls(**d)


# ---------------------------------------------------------------------------
# Session ledger file operations
# ---------------------------------------------------------------------------

DEFAULT_SESSION_DIR = os.path.join(os.path.dirname(__file__), "..", "helensh", ".state", "sessions")


def _session_ledger_path(directory: Optional[str] = None) -> str:
    d = directory or DEFAULT_SESSION_DIR
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "session_ledger.jsonl")


def _append_session_record(record: SessionRecord, directory: Optional[str] = None):
    path = _session_ledger_path(directory)
    with open(path, "a") as f:
        f.write(_canonical(record.to_dict()) + "\n")


def load_session_ledger(directory: Optional[str] = None) -> List[SessionRecord]:
    path = _session_ledger_path(directory)
    if not os.path.exists(path):
        return []
    records = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(SessionRecord.from_dict(json.loads(line)))
    return records


def get_last_session(directory: Optional[str] = None) -> Optional[SessionRecord]:
    records = load_session_ledger(directory)
    return records[-1] if records else None


# ---------------------------------------------------------------------------
# Close session — emit packet + persist + record
# ---------------------------------------------------------------------------

def close_session_with_packet(
    session_id: str,
    threads: Optional[List[Dict]] = None,
    tensions: Optional[List[Dict]] = None,
    committed_memory: Optional[List[Dict]] = None,
    next_action: Optional[str] = None,
    summary: Optional[str] = None,
    session_dir: Optional[str] = None,
    hydration_dir: Optional[str] = None,
) -> Tuple[MemoryPacket, SessionRecord]:
    """Close a session by emitting a MemoryPacket and recording it.

    Automatically chains to the previous session's packet.

    Returns (packet, session_record).
    """
    # Find previous session for chaining
    last = get_last_session(session_dir)
    previous_hash = GENESIS_DISCLOSURE_HASH
    previous_session = None
    if last and last.packet_hash:
        previous_hash = last.packet_hash
        previous_session = last.session_id

    # Emit packet
    packet = emit_boot_memory(
        session_id=session_id,
        threads=threads,
        tensions=tensions,
        committed_memory=committed_memory,
        next_action=next_action,
        previous_disclosure_hash=previous_hash,
    )

    # Persist packet
    persist_packet(packet, directory=hydration_dir)

    # Record session
    now = datetime.now(timezone.utc).isoformat()
    record = SessionRecord(
        session_id=session_id,
        started_at=now,  # approximate — real start was earlier
        ended_at=now,
        packet_hash=packet.receipt_hash,
        previous_session=previous_session,
        summary=summary,
    )
    _append_session_record(record, session_dir)

    return packet, record


# ---------------------------------------------------------------------------
# Open session — load chain, verify, reconstruct
# ---------------------------------------------------------------------------

def open_session_from_chain(
    session_dir: Optional[str] = None,
    hydration_dir: Optional[str] = None,
) -> Tuple[Optional[Dict], bool, Optional[str]]:
    """Open a new session by loading and verifying the last session's packet.

    Returns (boot_context_or_none, valid, error).

    Core invariant: context at new session start == context at last session end.
    """
    last = get_last_session(session_dir)
    if not last:
        return None, True, None  # No previous session — cold start

    if not last.packet_hash:
        return None, False, f"Last session {last.session_id} has no packet_hash"

    # Load the packet
    packet, valid, error = load_and_verify(last.session_id, hydration_dir)
    if not valid:
        return None, False, f"Packet verification failed: {error}"

    # Reconstruct context
    ctx = reconstruct_boot_context(packet)
    ctx["previous_session"] = last.session_id
    ctx["previous_summary"] = last.summary

    return ctx, True, None


# ---------------------------------------------------------------------------
# Replay — walk the full chain
# ---------------------------------------------------------------------------

def replay_session_chain(
    session_dir: Optional[str] = None,
    hydration_dir: Optional[str] = None,
) -> Tuple[List[Dict], bool, List[str]]:
    """Replay the full session chain.

    Loads all session records, loads all packets, verifies the chain.

    Returns (timeline, chain_valid, errors).
    """
    records = load_session_ledger(session_dir)
    if not records:
        return [], True, []

    packets = []
    timeline = []
    errors = []

    for r in records:
        packet = load_packet(r.session_id, hydration_dir)
        if packet is None:
            errors.append(f"Missing packet for session {r.session_id}")
            continue
        packets.append(packet)
        timeline.append({
            "session_id": r.session_id,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
            "summary": r.summary,
            "packet_hash": r.packet_hash,
            "previous_session": r.previous_session,
            "threads": len(packet.payload.get("threads", [])),
            "tensions": len(packet.payload.get("tensions", [])),
            "next_action": packet.payload.get("next_action", ""),
        })

    # Verify packet chain
    if packets:
        chain_ok, chain_errors = verify_chain(packets)
        if not chain_ok:
            errors.extend(chain_errors)

    return timeline, len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Session diff — what changed between two sessions
# ---------------------------------------------------------------------------

def session_diff(
    session_a_id: str,
    session_b_id: str,
    hydration_dir: Optional[str] = None,
) -> Optional[Dict]:
    """Compare two sessions and return what changed.

    Returns dict with added/removed/changed threads, tensions, etc.
    """
    pa = load_packet(session_a_id, hydration_dir)
    pb = load_packet(session_b_id, hydration_dir)
    if not pa or not pb:
        return None

    a_threads = {t.get("id", i): t for i, t in enumerate(pa.payload.get("threads", []))}
    b_threads = {t.get("id", i): t for i, t in enumerate(pb.payload.get("threads", []))}

    added_threads = [b_threads[k] for k in b_threads if k not in a_threads]
    removed_threads = [a_threads[k] for k in a_threads if k not in b_threads]

    a_tensions = set(json.dumps(t, sort_keys=True) for t in pa.payload.get("tensions", []))
    b_tensions = set(json.dumps(t, sort_keys=True) for t in pb.payload.get("tensions", []))

    return {
        "from_session": session_a_id,
        "to_session": session_b_id,
        "threads_added": added_threads,
        "threads_removed": removed_threads,
        "tensions_added": len(b_tensions - a_tensions),
        "tensions_removed": len(a_tensions - b_tensions),
        "next_action_changed": pa.payload.get("next_action") != pb.payload.get("next_action"),
        "from_next": pa.payload.get("next_action", ""),
        "to_next": pb.payload.get("next_action", ""),
    }
