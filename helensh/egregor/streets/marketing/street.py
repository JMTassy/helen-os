"""HELEN OS — Marketing Street Instance.

Second concrete street. Proves the factory pattern clones.

Role mapping onto universal schema:

    strategist       -> producer (plans campaigns and positioning)
    copywriter       -> producer (writes marketing copy)
    brand_critic     -> critic   (reviews brand alignment and voice)
    channel_checker  -> tester   (checks channel fit and distribution)
    quality_gate     -> gate     (final marketing quality gate)
"""
from __future__ import annotations

from typing import Optional

from helensh.egregor.street_schema import StreetCharter, ShopSpec
from helensh.egregor.street_factory import StreetFactory, ConcreteStreet, ShopExecutor


# ── Charter ─────────────────────────────────────────────────────────

MARKETING_CHARTER = StreetCharter(
    street_id="marketing",
    name="Marketing Street",
    mandate="Create, review, and optimize marketing content and strategy",
    allowed_domains=("copy", "strategy", "brand", "distribution"),
    forbidden_actions=("publish", "spend_budget", "sign_contract"),
    output_types=("copy_draft", "campaign_plan", "brand_review"),
    success_metrics=("brand_aligned", "target_clear", "cta_present"),
    risk_profile="low",
)


# ── Shops ───────────────────────────────────────────────────────────

MARKETING_SHOPS = [
    ShopSpec(
        shop_id="strategist",
        role="producer",
        mandate="Design campaign positioning and audience targeting",
        input_schema={"brief": "str", "audience": "str"},
        output_schema={"strategy": "str", "target_segments": "list"},
        model="gemma4",
        system_prompt="You are the STRATEGIST. Design campaign positioning.",
        temperature=0.5,
        max_steps=1,
    ),
    ShopSpec(
        shop_id="copywriter",
        role="producer",
        mandate="Write compelling marketing copy aligned with brand voice",
        input_schema={"strategy": "dict", "tone": "str"},
        output_schema={"copy": "str", "headline": "str", "cta": "str"},
        model="gemma4",
        system_prompt="You are the COPYWRITER. Write compelling marketing copy.",
        temperature=0.7,
        max_steps=2,
    ),
    ShopSpec(
        shop_id="brand_critic",
        role="critic",
        mandate="Review copy for brand alignment, tone, and accuracy",
        input_schema={"copy": "str", "brand_guidelines": "dict"},
        output_schema={"verdict": "str", "issues": "list"},
        model="gemma4",
        system_prompt="You are the BRAND CRITIC. Review for brand alignment.",
        temperature=0.3,
        max_steps=1,
    ),
    ShopSpec(
        shop_id="channel_checker",
        role="tester",
        mandate="Verify copy fits target channels and distribution constraints",
        input_schema={"copy": "str", "channels": "list"},
        output_schema={"fit_scores": "dict", "warnings": "list"},
        model="gemma4",
        system_prompt="You are the CHANNEL CHECKER. Verify distribution fit.",
        temperature=0.3,
        max_steps=1,
    ),
    ShopSpec(
        shop_id="marketing_gate",
        role="gate",
        mandate="Final quality and compliance check before export",
        input_schema={"copy": "str", "strategy": "dict", "reviews": "list"},
        output_schema={"verdict": "str", "score": "float"},
        model="local",
        system_prompt="",
        temperature=0.0,
        max_steps=1,
    ),
]


# ── Factory Function ────────────────────────────────────────────────


def create_marketing_street(
    executor: Optional[ShopExecutor] = None,
) -> ConcreteStreet:
    """Create a Marketing Street instance via the factory."""
    return StreetFactory.create(
        charter=MARKETING_CHARTER,
        shops=MARKETING_SHOPS,
        executor=executor,
    )
