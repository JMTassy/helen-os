"""HELEN OS — Street Message Bus.

Typed bus. No prose traffic.

Only ALLOWED_MESSAGE_TYPES can transit.
Anything else: reject.

That matters. If you allow freeform chatter, your city becomes theater.

Rules:
    1. Only ALLOWED_MESSAGE_TYPES accepted
    2. Street ID must match bus ID
    3. GATE_PACKET can only be sent by gate-role senders
    4. All messages logged (append-only)
    5. Shops cannot emit verdicts
"""
from __future__ import annotations

from typing import List

from helensh.egregor.street_schema import (
    MessageEnvelope,
    ALLOWED_MESSAGE_TYPES,
)


class BusError(Exception):
    """Raised when a message violates bus rules."""
    pass


# Message types that only gate-role senders can emit
_GATE_ONLY_TYPES = frozenset({"GATE_PACKET"})


class StreetBus:
    """Typed message bus for inter-shop communication.

    Enforces message discipline:
      - only typed messages transit
      - shops cannot emit gate-only messages
      - every message is logged
    """

    def __init__(self, street_id: str) -> None:
        self.street_id = street_id
        self._log: List[MessageEnvelope] = []

    def send(self, envelope: MessageEnvelope) -> None:
        """Send a message on the bus. Rejects invalid types and senders."""
        # Rule 1: type must be allowed
        if envelope.message_type not in ALLOWED_MESSAGE_TYPES:
            raise BusError(
                f"unknown message type '{envelope.message_type}'; "
                f"allowed: {sorted(ALLOWED_MESSAGE_TYPES)}"
            )

        # Rule 2: street ID must match
        if envelope.street_id != self.street_id:
            raise BusError(
                f"envelope street_id '{envelope.street_id}' "
                f"does not match bus street_id '{self.street_id}'"
            )

        # Rule 3: gate-only types cannot be sent by shops
        if envelope.message_type in _GATE_ONLY_TYPES:
            sender = envelope.sender
            if sender != "gate" and not sender.endswith("_gate"):
                raise BusError(
                    f"sender '{sender}' cannot emit "
                    f"'{envelope.message_type}' — gate-only message type"
                )

        # Rule 4: append to log
        self._log.append(envelope)

    def get_log(self) -> List[MessageEnvelope]:
        """Return full bus log (copy)."""
        return list(self._log)

    def get_for_recipient(self, recipient: str) -> List[MessageEnvelope]:
        """Get all messages addressed to a given recipient."""
        return [e for e in self._log if e.recipient == recipient]

    def count(self) -> int:
        """Number of messages on the bus."""
        return len(self._log)


__all__ = [
    "StreetBus",
    "BusError",
]
