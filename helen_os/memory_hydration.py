"""
HELEN OS Memory Hydration V1

Persistent, verifiable cross-session state.

Flow: emit → persist → load → verify

A MemoryPacket is a receipted snapshot of working context:
- threads, tensions, committed memory, last session, next action
- receipt_hash for tamper detection
- previous_disclosure_hash for chain linking
- authority = NONE (always)

Core invariant: same inputs → same receipt_hash (deterministic).
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Canonical serialization (same as kernel)
# ---------------------------------------------------------------------------

def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(data: Any) -> str:
    return hashlib.sha256(_canonical(data).encode("utf-8")).hexdigest()


GENESIS_DISCLOSURE_HASH = "0" * 64


# ---------------------------------------------------------------------------
# MemoryPacket — the verifiable cross-session state object
# ---------------------------------------------------------------------------

@dataclass
class MemoryPacket:
    """A receipted snapshot of HELEN's working context.

    Frozen after construction. Tamper-detectable via receipt_hash.
    Chain-linkable via previous_disclosure_hash.
    """
    session_id: str
    timestamp: str
    payload: Dict[str, Any]  # threads, tensions, memory, next_action, etc.
    payload_hash: str
    previous_disclosure_hash: str
    receipt_hash: str
    authority: str = "NONE"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryPacket":
        return cls(**d)


# ---------------------------------------------------------------------------
# Emit — construct a MemoryPacket from current state
# ---------------------------------------------------------------------------

def emit_boot_memory(
    session_id: str,
    threads: Optional[List[Dict]] = None,
    tensions: Optional[List[Dict]] = None,
    committed_memory: Optional[List[Dict]] = None,
    last_session: Optional[Dict] = None,
    next_action: Optional[str] = None,
    previous_disclosure_hash: str = GENESIS_DISCLOSURE_HASH,
    extra: Optional[Dict] = None,
) -> MemoryPacket:
    """Emit a MemoryPacket from current working context.

    Deterministic: same inputs → same receipt_hash.
    """
    payload = {
        "threads": threads or [],
        "tensions": tensions or [],
        "committed_memory": committed_memory or [],
        "last_session": last_session,
        "next_action": next_action or "",
    }
    if extra:
        payload["extra"] = extra

    payload_hash = _hash(payload)

    # Receipt hash covers: session_id + payload_hash + previous_disclosure_hash
    receipt_input = {
        "session_id": session_id,
        "payload_hash": payload_hash,
        "previous_disclosure_hash": previous_disclosure_hash,
        "authority": "NONE",
    }
    receipt_hash = _hash(receipt_input)

    return MemoryPacket(
        session_id=session_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        payload=payload,
        payload_hash=payload_hash,
        previous_disclosure_hash=previous_disclosure_hash,
        receipt_hash=receipt_hash,
        authority="NONE",
    )


# ---------------------------------------------------------------------------
# Verify — check a MemoryPacket for tampering
# ---------------------------------------------------------------------------

def verify_packet(packet: MemoryPacket) -> Tuple[bool, Optional[str]]:
    """Verify a MemoryPacket's integrity.

    Checks:
    1. payload_hash matches payload
    2. receipt_hash matches (session_id + payload_hash + previous_disclosure_hash)
    3. authority is NONE

    Returns (valid, error_message).
    """
    # Check 1: payload hash
    expected_payload_hash = _hash(packet.payload)
    if packet.payload_hash != expected_payload_hash:
        return False, f"payload_hash mismatch: expected {expected_payload_hash[:16]}, got {packet.payload_hash[:16]}"

    # Check 2: receipt hash
    receipt_input = {
        "session_id": packet.session_id,
        "payload_hash": packet.payload_hash,
        "previous_disclosure_hash": packet.previous_disclosure_hash,
        "authority": "NONE",
    }
    expected_receipt_hash = _hash(receipt_input)
    if packet.receipt_hash != expected_receipt_hash:
        return False, f"receipt_hash mismatch: expected {expected_receipt_hash[:16]}, got {packet.receipt_hash[:16]}"

    # Check 3: authority
    if packet.authority != "NONE":
        return False, f"authority must be NONE, got {packet.authority}"

    return True, None


# ---------------------------------------------------------------------------
# Persist + Load — file-based storage
# ---------------------------------------------------------------------------

DEFAULT_HYDRATION_DIR = os.path.join(os.path.dirname(__file__), "..", "helensh", ".state", "hydration")


def persist_packet(packet: MemoryPacket, directory: Optional[str] = None) -> str:
    """Persist a MemoryPacket to disk. Returns the file path."""
    d = directory or DEFAULT_HYDRATION_DIR
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{packet.session_id}.json")
    with open(path, "w") as f:
        f.write(_canonical(packet.to_dict()))
    return path


def load_packet(session_id: str, directory: Optional[str] = None) -> Optional[MemoryPacket]:
    """Load a MemoryPacket from disk. Returns None if not found."""
    d = directory or DEFAULT_HYDRATION_DIR
    path = os.path.join(d, f"{session_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        data = json.load(f)
    return MemoryPacket.from_dict(data)


def load_and_verify(session_id: str, directory: Optional[str] = None) -> Tuple[Optional[MemoryPacket], bool, Optional[str]]:
    """Load a MemoryPacket and verify its integrity.

    Returns (packet_or_none, valid, error_message).
    """
    packet = load_packet(session_id, directory)
    if packet is None:
        return None, False, "packet not found"
    valid, error = verify_packet(packet)
    return packet, valid, error


# ---------------------------------------------------------------------------
# Chain verification — verify a sequence of linked packets
# ---------------------------------------------------------------------------

def verify_chain(packets: List[MemoryPacket]) -> Tuple[bool, List[str]]:
    """Verify a chain of MemoryPackets.

    Checks:
    1. First packet links to GENESIS
    2. Each subsequent packet links to the previous receipt_hash
    3. All packets pass individual verification
    """
    if not packets:
        return True, []

    errors = []

    # Check first links to genesis
    if packets[0].previous_disclosure_hash != GENESIS_DISCLOSURE_HASH:
        errors.append("first packet does not link to GENESIS")

    # Verify each packet individually
    for i, packet in enumerate(packets):
        valid, error = verify_packet(packet)
        if not valid:
            errors.append(f"packet {i} ({packet.session_id}): {error}")

    # Check chain links
    for i in range(1, len(packets)):
        expected_prev = packets[i - 1].receipt_hash
        actual_prev = packets[i].previous_disclosure_hash
        if actual_prev != expected_prev:
            errors.append(f"chain break at packet {i}: expected {expected_prev[:16]}, got {actual_prev[:16]}")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Boot context reconstruction — what /init uses
# ---------------------------------------------------------------------------

def reconstruct_boot_context(packet: MemoryPacket) -> Dict[str, Any]:
    """Reconstruct boot context from a verified MemoryPacket.

    Returns a structured dict suitable for /init or system prompt injection.
    Only call this AFTER verify_packet() passes.
    """
    p = packet.payload
    return {
        "session_id": packet.session_id,
        "threads": p.get("threads", []),
        "tensions": p.get("tensions", []),
        "committed_memory": p.get("committed_memory", []),
        "last_session": p.get("last_session"),
        "next_action": p.get("next_action", ""),
        "receipt_hash": packet.receipt_hash,
        "verified": True,
        "authority": "NONE",
    }
