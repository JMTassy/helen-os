"""HELEN OS — Abstract Street Base.

Every street in the city must satisfy this interface.
Defines the lifecycle: load -> route -> run -> aggregate -> gate -> ledger -> emit.

Non-negotiables:
    1. Shops never decide.
    2. Streets never self-certify.
    3. Gate runs before export.
    4. Ledger is append-only.
    5. Replay hash excludes narrative artifacts but includes governing outputs.
    6. Prompts are configuration, not authority.
    7. Reducer remains above the streets.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from helensh.state import canonical_hash
from helensh.egregor.street_schema import (
    StreetCharter,
    ShopSpec,
    MessageEnvelope,
    StreetGateResult,
    StreetLedgerEntry,
)
from helensh.egregor.street_bus import StreetBus
from helensh.egregor.street_gate import StreetGate


class AbstractStreet(ABC):
    """Abstract base for all streets in the city.

    Lifecycle:
        1. load_charter() -> StreetCharter
        2. load_shops() -> list of ShopSpec
        3. route_task(task) -> ordered list of shop_ids
        4. run_shop(shop_id, envelope) -> output envelope
        5. aggregate(outputs) -> unified artifact dict
        6. gate_check(artifact) -> StreetGateResult
        7. write_ledger(entry) -> append to street ledger
        8. emit_packet(artifact, gate_result) -> exportable packet or None

    Concrete streets override the abstract methods.
    The lifecycle orchestration (run) is shared.
    """

    def __init__(self) -> None:
        self._charter: Optional[StreetCharter] = None
        self._shops: Dict[str, ShopSpec] = {}
        self._bus: Optional[StreetBus] = None
        self._gate: Optional[StreetGate] = None
        self._ledger: List[StreetLedgerEntry] = []
        self._initialized = False

    def initialize(self) -> None:
        """Bootstrap: load charter, shops, create bus and gate."""
        self._charter = self.load_charter()
        shop_list = self.load_shops()
        self._shops = {s.shop_id: s for s in shop_list}
        self._bus = StreetBus(self._charter.street_id)
        self._gate = StreetGate(self._charter)
        self._ledger = []
        self._initialized = True

    # ── Properties ──

    @property
    def charter(self) -> StreetCharter:
        assert self._charter is not None, "street not initialized"
        return self._charter

    @property
    def bus(self) -> StreetBus:
        assert self._bus is not None, "street not initialized"
        return self._bus

    @property
    def gate(self) -> StreetGate:
        assert self._gate is not None, "street not initialized"
        return self._gate

    @property
    def shops(self) -> Dict[str, ShopSpec]:
        return dict(self._shops)

    @property
    def ledger(self) -> List[StreetLedgerEntry]:
        """Read-only copy of the street ledger."""
        return list(self._ledger)

    # ── Abstract methods (each street defines these) ──

    @abstractmethod
    def load_charter(self) -> StreetCharter:
        """Load and return this street's charter."""
        ...

    @abstractmethod
    def load_shops(self) -> List[ShopSpec]:
        """Load and return this street's shop specifications."""
        ...

    @abstractmethod
    def route_task(self, task: Dict[str, Any]) -> List[str]:
        """Route a task to an ordered list of shop_ids to execute."""
        ...

    @abstractmethod
    def run_shop(
        self,
        shop_id: str,
        envelope: MessageEnvelope,
    ) -> MessageEnvelope:
        """Execute a single shop on the given input envelope."""
        ...

    @abstractmethod
    def aggregate(
        self,
        outputs: List[MessageEnvelope],
    ) -> Dict[str, Any]:
        """Aggregate shop outputs into a single artifact dict."""
        ...

    # ── Concrete methods (shared lifecycle) ──

    def gate_check(self, artifact: Dict[str, Any]) -> StreetGateResult:
        """Run the exit gate on an artifact."""
        # Collect all receipt hashes from bus log
        flat_receipts: List[str] = []
        for env in self.bus.get_log():
            flat_receipts.extend(env.receipts)
        return self.gate.check(artifact, flat_receipts, self._ledger)

    def write_ledger(self, entry: StreetLedgerEntry) -> None:
        """Append an entry to the street ledger. Append-only — never delete."""
        self._ledger.append(entry)

    def emit_packet(
        self,
        artifact: Dict[str, Any],
        gate_result: StreetGateResult,
    ) -> Optional[Dict[str, Any]]:
        """Emit artifact if gate allows (PASS or WARN). None if BLOCK."""
        if gate_result.verdict == "BLOCK":
            return None
        return {
            "street_id": self.charter.street_id,
            "artifact": artifact,
            "gate_verdict": gate_result.verdict,
            "gate_reasons": list(gate_result.reasons),
            "replay_hash": gate_result.replay_hash,
        }

    def run(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Full street lifecycle: route -> run shops -> aggregate -> gate -> emit.

        Returns dict with:
            artifact:      the aggregated output
            gate_result:   StreetGateResult
            packet:        exportable packet (None if BLOCK)
            ledger_count:  entries in street ledger
        """
        if not self._initialized:
            self.initialize()

        task_id = task.get("task_id", "T-0")

        # ── Route ──
        shop_order = self.route_task(task)

        # ── Initial task envelope ──
        first_recipient = shop_order[0] if shop_order else "gate"
        initial_env = MessageEnvelope(
            envelope_id=f"{self.charter.street_id}-{task_id}-input",
            street_id=self.charter.street_id,
            task_id=task_id,
            sender="street",
            recipient=first_recipient,
            message_type="TASK",
            payload=task,
            receipts=(),
            parents=(),
        )
        self.bus.send(initial_env)

        # ── Run shops in order ──
        outputs: List[MessageEnvelope] = []
        prev_env = initial_env

        for shop_id in shop_order:
            input_env = MessageEnvelope(
                envelope_id=f"{self.charter.street_id}-{task_id}-{shop_id}-in",
                street_id=self.charter.street_id,
                task_id=task_id,
                sender="street",
                recipient=shop_id,
                message_type="TASK",
                payload=prev_env.payload,
                receipts=prev_env.receipts,
                parents=(prev_env.envelope_id,),
            )

            output_env = self.run_shop(shop_id, input_env)
            outputs.append(output_env)
            self.bus.send(output_env)
            prev_env = output_env

        # ── Aggregate ──
        artifact = self.aggregate(outputs)

        # ── Ledger entry ──
        receipt_hash = canonical_hash({
            "street_id": self.charter.street_id,
            "task_id": task_id,
            "shop_count": len(shop_order),
            "output_count": len(outputs),
        })
        ledger_entry = StreetLedgerEntry(
            entry_id=f"{self.charter.street_id}-{task_id}-final",
            street_id=self.charter.street_id,
            task_id=task_id,
            phase="complete",
            artifact_refs=(receipt_hash,),
            receipts=(receipt_hash,),
            hash=receipt_hash,
        )
        self.write_ledger(ledger_entry)

        # ── Gate check ──
        gate_result = self.gate_check(artifact)

        # ── Emit ──
        packet = self.emit_packet(artifact, gate_result)

        return {
            "artifact": artifact,
            "gate_result": gate_result,
            "packet": packet,
            "ledger_count": len(self._ledger),
        }


__all__ = ["AbstractStreet"]
