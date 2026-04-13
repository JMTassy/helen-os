"""HELEN OS — Coding Street Instance.

The first concrete street. Maps the existing Egregor pipeline roles
onto the universal role schema:

    architect  -> producer (plans and decomposes)
    coder      -> producer (writes code)
    reviewer   -> critic   (reviews code quality)
    tester     -> tester   (writes and runs tests)
    validator  -> gate     (final quality gate)
"""
from __future__ import annotations

from typing import Optional

from helensh.egregor.street_schema import StreetCharter, ShopSpec
from helensh.egregor.street_factory import StreetFactory, ConcreteStreet, ShopExecutor


# ── Charter ─────────────────────────────────────────────────────────

CODING_CHARTER = StreetCharter(
    street_id="coding",
    name="Coding Street",
    mandate="Write, review, test, and ship code under governance",
    allowed_domains=("code", "testing", "documentation"),
    forbidden_actions=("deploy_production", "merge_to_main", "delete_production"),
    output_types=("code_unit", "test_suite", "review_report"),
    success_metrics=("tests_pass", "review_approved", "lint_clean"),
    risk_profile="medium",
)


# ── Shops ───────────────────────────────────────────────────────────

CODING_SHOPS = [
    ShopSpec(
        shop_id="architect",
        role="producer",
        mandate="Decompose tasks into subtasks with clear interfaces",
        input_schema={"task": "str", "context": "str"},
        output_schema={"subtasks": "list", "design_notes": "str"},
        model="her-claudecode-gemma",
        system_prompt="You are the ARCHITECT. Decompose tasks into subtasks.",
        temperature=0.4,
        max_steps=1,
    ),
    ShopSpec(
        shop_id="coder",
        role="producer",
        mandate="Write clean, complete, tested Python code",
        input_schema={"subtask": "dict", "feedback": "str?"},
        output_schema={"code": "str", "target": "str"},
        model="her-codex-gemma",
        system_prompt="You are the CODER. Write complete Python code.",
        temperature=0.6,
        max_steps=3,
    ),
    ShopSpec(
        shop_id="reviewer",
        role="critic",
        mandate="Review code quality and correctness",
        input_schema={"code": "str", "target": "str"},
        output_schema={"verdict": "str", "issues": "list"},
        model="hal-reviewer",
        system_prompt="",
        temperature=0.3,
        max_steps=1,
    ),
    ShopSpec(
        shop_id="test_writer",
        role="tester",
        mandate="Write comprehensive pytest tests",
        input_schema={"code": "str", "target": "str"},
        output_schema={"test_code": "str", "test_count": "int"},
        model="her-codex-gemma",
        system_prompt="You are the TESTER. Write complete pytest tests.",
        temperature=0.5,
        max_steps=1,
    ),
    ShopSpec(
        shop_id="validator_gate",
        role="gate",
        mandate="Score code with AST + exec + test validation",
        input_schema={"code": "str", "tests": "str"},
        output_schema={"score": "float", "verdict": "str"},
        model="local",
        system_prompt="",
        temperature=0.0,
        max_steps=1,
    ),
]


# ── Factory Function ────────────────────────────────────────────────


def create_coding_street(
    executor: Optional[ShopExecutor] = None,
) -> ConcreteStreet:
    """Create a Coding Street instance via the factory."""
    return StreetFactory.create(
        charter=CODING_CHARTER,
        shops=CODING_SHOPS,
        executor=executor,
    )
