"""HELEN OS — Street Factory.

Creates street instances from charter + shops + executor.

The proof is not 'template exists.'
The proof is: same factory, different charter, valid second street.

Usage:
    factory = StreetFactory()
    coding = factory.create(charter=..., shops=..., executor=...)
    marketing = factory.create(charter=..., shops=..., executor=...)
    # Both are valid AbstractStreet instances with the same lifecycle.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from helensh.state import canonical_hash
from helensh.egregor.street_schema import (
    StreetCharter,
    ShopSpec,
    MessageEnvelope,
    UNIVERSAL_ROLES,
)
from helensh.egregor.street_base import AbstractStreet


# Type alias for shop executor
ShopExecutor = Callable[[MessageEnvelope, ShopSpec], MessageEnvelope]


# ── Default Executor ────────────────────────────────────────────────


def _default_executor(
    envelope: MessageEnvelope,
    spec: ShopSpec,
) -> MessageEnvelope:
    """Default executor: process input with role annotation + receipt.

    In production, this delegates to an LLM (Ollama/Gemma).
    For testing, it returns a deterministic receipt-bearing output.
    """
    receipt_hash = canonical_hash({
        "shop_id": spec.shop_id,
        "role": spec.role,
        "envelope_id": envelope.envelope_id,
        "task_id": envelope.task_id,
    })

    return MessageEnvelope(
        envelope_id=f"{envelope.envelope_id}-out",
        street_id=envelope.street_id,
        task_id=envelope.task_id,
        sender=spec.shop_id,
        recipient="next",
        message_type="PROPOSAL",
        payload={
            "role": spec.role,
            "shop_id": spec.shop_id,
            "mandate": spec.mandate,
            "input_summary": str(envelope.payload.get("description", ""))[:200],
            "output": f"[{spec.shop_id}] processed task",
            "authority": False,
        },
        receipts=(receipt_hash,) + envelope.receipts,
        parents=(envelope.envelope_id,),
    )


# ── Factory ─────────────────────────────────────────────────────────


class StreetFactory:
    """Factory for creating governed streets.

    Validates charter and shops, then produces a ConcreteStreet
    that satisfies the AbstractStreet interface.
    """

    @staticmethod
    def create(
        charter: StreetCharter,
        shops: List[ShopSpec],
        executor: Optional[ShopExecutor] = None,
        shop_order: Optional[List[str]] = None,
    ) -> "ConcreteStreet":
        """Create a governed street from charter and shop specifications.

        Args:
            charter: Street identity, mandate, constraints
            shops: List of shop specifications
            executor: Function that runs a shop (default: receipted echo)
            shop_order: Fixed execution order (default: shops in list order)

        Returns:
            An initialized ConcreteStreet ready to run tasks.

        Raises:
            ValueError: if any shop violates structural invariants
        """
        # ── Validate: non-sovereign ──
        for shop in shops:
            if not shop.non_sovereign:
                raise ValueError(
                    f"shop '{shop.shop_id}' has non_sovereign=False — "
                    f"shops never claim authority"
                )

        # ── Validate: universal roles ──
        for shop in shops:
            if shop.role not in UNIVERSAL_ROLES:
                raise ValueError(
                    f"shop '{shop.shop_id}' has role '{shop.role}' — "
                    f"must be one of {sorted(UNIVERSAL_ROLES)}"
                )

        if executor is None:
            executor = _default_executor

        if shop_order is None:
            shop_order = [s.shop_id for s in shops]

        street = ConcreteStreet(
            charter=charter,
            shops=shops,
            executor=executor,
            shop_order=shop_order,
        )
        street.initialize()
        return street


# ── Concrete Street ─────────────────────────────────────────────────


class ConcreteStreet(AbstractStreet):
    """A street created by the factory.

    Satisfies all AbstractStreet requirements with pluggable executor.
    """

    def __init__(
        self,
        charter: StreetCharter,
        shops: List[ShopSpec],
        executor: ShopExecutor,
        shop_order: List[str],
    ) -> None:
        super().__init__()
        self._charter_data = charter
        self._shops_data = shops
        self._executor = executor
        self._shop_order = shop_order

    def load_charter(self) -> StreetCharter:
        return self._charter_data

    def load_shops(self) -> List[ShopSpec]:
        return self._shops_data

    def route_task(self, task: Dict[str, Any]) -> List[str]:
        return list(self._shop_order)

    def run_shop(
        self,
        shop_id: str,
        envelope: MessageEnvelope,
    ) -> MessageEnvelope:
        spec = self._shops.get(shop_id)
        if spec is None:
            raise ValueError(f"unknown shop: '{shop_id}'")
        return self._executor(envelope, spec)

    def aggregate(
        self,
        outputs: List[MessageEnvelope],
    ) -> Dict[str, Any]:
        """Aggregate shop outputs into a unified artifact."""
        items = []
        for env in outputs:
            items.append({
                "shop": env.sender,
                "role": env.payload.get("role", ""),
                "output": env.payload.get("output", ""),
                "type": env.message_type,
            })

        return {
            "type": "street_output",
            "domain": (
                self.charter.allowed_domains[0]
                if self.charter.allowed_domains else ""
            ),
            "items": items,
            "shop_count": len(items),
            "authority": False,
        }


__all__ = [
    "StreetFactory",
    "ConcreteStreet",
    "ShopExecutor",
]
