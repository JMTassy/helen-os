"""HELEN OS — Street Template Test Suite.

Tests the abstract street base, typed bus, exit gate, factory,
and two concrete street instances (Coding + Marketing).

Mandatory tests (from spec):
    1. test_street_template_refuses_unknown_message_type
    2. test_shop_cannot_emit_verdict
    3. test_gate_blocks_missing_receipts
    4. test_gate_blocks_forbidden_action
    5. test_street_ledger_append_only
    6. test_replay_hash_stable_same_inputs
    7. test_coding_street_instantiates_from_factory
    8. test_marketing_street_instantiates_from_same_factory
    9. test_cross_street_schema_identical_at_base_layer

Additional coverage:
    - Schema dataclass properties
    - Bus discipline (type validation, street ID check)
    - Gate check completeness (all 6 checks)
    - Factory validation (non-sovereign, universal roles)
    - Street lifecycle (route -> run -> aggregate -> gate -> emit)
    - Adversarial: authority injection, domain escape
"""
import pytest

from helensh.egregor.street_schema import (
    ALLOWED_MESSAGE_TYPES,
    UNIVERSAL_ROLES,
    StreetCharter,
    ShopSpec,
    MessageEnvelope,
    StreetGateResult,
    StreetLedgerEntry,
)
from helensh.egregor.street_bus import StreetBus, BusError
from helensh.egregor.street_gate import StreetGate
from helensh.egregor.street_base import AbstractStreet
from helensh.egregor.street_factory import StreetFactory, ConcreteStreet
from helensh.egregor.streets.coding.street import (
    CODING_CHARTER,
    CODING_SHOPS,
    create_coding_street,
)
from helensh.egregor.streets.marketing.street import (
    MARKETING_CHARTER,
    MARKETING_SHOPS,
    create_marketing_street,
)


# ── Helpers ─────────────────────────────────────────────────────────

def _minimal_charter(**overrides):
    defaults = dict(
        street_id="test",
        name="Test Street",
        mandate="Testing",
        allowed_domains=("testing",),
        forbidden_actions=("destroy",),
        output_types=("result",),
        success_metrics=("pass",),
        risk_profile="low",
    )
    defaults.update(overrides)
    return StreetCharter(**defaults)


def _minimal_shop(shop_id="worker", role="producer", **overrides):
    defaults = dict(
        shop_id=shop_id,
        role=role,
        mandate="Do work",
        input_schema={"task": "str"},
        output_schema={"result": "str"},
        model="test-model",
        system_prompt="You are a worker.",
        temperature=0.5,
        max_steps=1,
        non_sovereign=True,
    )
    defaults.update(overrides)
    return ShopSpec(**defaults)


def _minimal_envelope(street_id="test", **overrides):
    defaults = dict(
        envelope_id="env-1",
        street_id=street_id,
        task_id="T-1",
        sender="worker",
        recipient="next",
        message_type="PROPOSAL",
        payload={"data": "test"},
        receipts=("hash-1",),
        parents=(),
    )
    defaults.update(overrides)
    return MessageEnvelope(**defaults)


# ═══════════════════════════════════════════════════════════════════════
# SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestSchemaDataclasses:
    """All schema types are frozen dataclasses."""

    def test_charter_frozen(self):
        c = _minimal_charter()
        with pytest.raises(AttributeError):
            c.name = "changed"

    def test_shop_spec_frozen(self):
        s = _minimal_shop()
        with pytest.raises(AttributeError):
            s.role = "changed"

    def test_envelope_frozen(self):
        e = _minimal_envelope()
        with pytest.raises(AttributeError):
            e.payload = {}

    def test_gate_result_frozen(self):
        r = StreetGateResult("PASS", (), (), ("h",), "rh")
        with pytest.raises(AttributeError):
            r.verdict = "BLOCK"

    def test_ledger_entry_frozen(self):
        e = StreetLedgerEntry("e1", "s1", "T-1", "done", (), ("h",), "hash")
        with pytest.raises(AttributeError):
            e.phase = "changed"

    def test_shop_spec_non_sovereign_default(self):
        s = _minimal_shop()
        assert s.non_sovereign is True

    def test_allowed_message_types_complete(self):
        expected = {"TASK", "PROPOSAL", "PATCH", "REVIEW",
                    "TEST_RESULT", "RISK_NOTE", "GATE_PACKET", "LEDGER_WRITE"}
        assert ALLOWED_MESSAGE_TYPES == expected

    def test_universal_roles_complete(self):
        expected = {"producer", "critic", "tester", "archivist", "gate"}
        assert UNIVERSAL_ROLES == expected


# ═══════════════════════════════════════════════════════════════════════
# BUS TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestStreetBus:
    """Typed message bus discipline."""

    def test_send_valid(self):
        bus = StreetBus("test")
        env = _minimal_envelope()
        bus.send(env)
        assert bus.count() == 1

    def test_street_template_refuses_unknown_message_type(self):
        """MANDATORY TEST 1: unknown message type rejected."""
        bus = StreetBus("test")
        env = _minimal_envelope(message_type="GOSSIP")
        with pytest.raises(BusError, match="unknown message type"):
            bus.send(env)

    def test_wrong_street_id_rejected(self):
        bus = StreetBus("alpha")
        env = _minimal_envelope(street_id="beta")
        with pytest.raises(BusError, match="does not match"):
            bus.send(env)

    def test_shop_cannot_emit_verdict(self):
        """MANDATORY TEST 2: non-gate sender cannot emit GATE_PACKET."""
        bus = StreetBus("test")
        env = _minimal_envelope(sender="coder", message_type="GATE_PACKET")
        with pytest.raises(BusError, match="gate-only"):
            bus.send(env)

    def test_gate_can_emit_gate_packet(self):
        """Gate-role senders CAN emit GATE_PACKET."""
        bus = StreetBus("test")
        env = _minimal_envelope(sender="gate", message_type="GATE_PACKET")
        bus.send(env)  # should not raise
        assert bus.count() == 1

    def test_gate_suffix_can_emit_gate_packet(self):
        """Senders ending with _gate can emit GATE_PACKET."""
        bus = StreetBus("test")
        env = _minimal_envelope(sender="quality_gate", message_type="GATE_PACKET")
        bus.send(env)
        assert bus.count() == 1

    def test_get_for_recipient(self):
        bus = StreetBus("test")
        bus.send(_minimal_envelope(recipient="alice", envelope_id="e1"))
        bus.send(_minimal_envelope(recipient="bob", envelope_id="e2"))
        bus.send(_minimal_envelope(recipient="alice", envelope_id="e3"))
        assert len(bus.get_for_recipient("alice")) == 2
        assert len(bus.get_for_recipient("bob")) == 1

    def test_log_is_copy(self):
        bus = StreetBus("test")
        bus.send(_minimal_envelope())
        log = bus.get_log()
        log.clear()
        assert bus.count() == 1  # original unaffected

    def test_all_allowed_types_accepted(self):
        bus = StreetBus("test")
        for mt in ALLOWED_MESSAGE_TYPES:
            sender = "gate" if mt == "GATE_PACKET" else "worker"
            env = _minimal_envelope(
                message_type=mt, sender=sender,
                envelope_id=f"e-{mt}",
            )
            bus.send(env)
        assert bus.count() == len(ALLOWED_MESSAGE_TYPES)


# ═══════════════════════════════════════════════════════════════════════
# GATE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestStreetGate:
    """HAL-class exit gate checks."""

    def _gate(self, **charter_overrides):
        return StreetGate(_minimal_charter(**charter_overrides))

    def test_clean_artifact_passes(self):
        g = self._gate()
        result = g.check(
            {"type": "result", "domain": "testing", "authority": False},
            ["receipt-1"],
            [],
        )
        assert result.verdict == "PASS"
        assert len(result.reasons) == 0

    def test_gate_blocks_missing_receipts(self):
        """MANDATORY TEST 3: no receipts -> BLOCK."""
        g = self._gate()
        result = g.check(
            {"type": "result", "domain": "testing"},
            [],  # no receipts
            [],
        )
        assert result.verdict == "BLOCK"
        assert any("receipt" in r.lower() for r in result.reasons)

    def test_gate_blocks_forbidden_action(self):
        """MANDATORY TEST 4: forbidden action -> BLOCK."""
        g = self._gate(forbidden_actions=("deploy",))
        result = g.check(
            {"type": "result", "action": "deploy", "domain": "testing"},
            ["r1"],
            [],
        )
        assert result.verdict == "BLOCK"
        assert any("forbidden" in r for r in result.reasons)

    def test_gate_blocks_wrong_domain(self):
        g = self._gate(allowed_domains=("code",))
        result = g.check(
            {"type": "result", "domain": "finance"},
            ["r1"],
            [],
        )
        assert result.verdict == "BLOCK"
        assert any("domain" in r for r in result.reasons)

    def test_gate_blocks_authority_true(self):
        g = self._gate()
        result = g.check(
            {"type": "result", "authority": True},
            ["r1"],
            [],
        )
        assert result.verdict == "BLOCK"
        assert any("authority" in r.lower() for r in result.reasons)

    def test_gate_warns_missing_type(self):
        g = self._gate()
        result = g.check(
            {"domain": "testing"},  # no "type" field
            ["r1"],
            [],
        )
        assert result.verdict == "WARN"

    def test_gate_warns_unresolved_obligations(self):
        g = self._gate()
        result = g.check(
            {"type": "result", "domain": "testing",
             "obligations": [{"id": "O-1", "status": "open"}]},
            ["r1"],
            [],
        )
        assert result.verdict == "WARN"
        assert any("unresolved" in r for r in result.reasons)

    def test_replay_hash_stable_same_inputs(self):
        """MANDATORY TEST 6: same inputs -> same replay hash."""
        g = self._gate()
        r1 = g.check({"type": "x"}, ["r1"], [])
        r2 = g.check({"type": "x"}, ["r1"], [])
        assert r1.replay_hash == r2.replay_hash

    def test_replay_hash_changes_with_different_inputs(self):
        g = self._gate()
        r1 = g.check({"type": "x"}, ["r1"], [])
        r2 = g.check({"type": "y"}, ["r1", "r2"], [])
        assert r1.replay_hash != r2.replay_hash

    def test_gate_verdict_escalation_monotonic(self):
        """Multiple violations: verdict escalates, never downgrades."""
        g = self._gate(forbidden_actions=("bad_action",))
        result = g.check(
            {"type": "result", "action": "bad_action", "authority": True},
            [],  # no receipts -> BLOCK
            [],
        )
        assert result.verdict == "BLOCK"


# ═══════════════════════════════════════════════════════════════════════
# FACTORY TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestStreetFactory:
    """Factory creates valid streets from charter + shops."""

    def test_create_minimal(self):
        charter = _minimal_charter()
        shops = [_minimal_shop()]
        street = StreetFactory.create(charter=charter, shops=shops)
        assert isinstance(street, ConcreteStreet)
        assert isinstance(street, AbstractStreet)

    def test_factory_rejects_sovereign_shop(self):
        charter = _minimal_charter()
        shops = [_minimal_shop(non_sovereign=False)]
        with pytest.raises(ValueError, match="non_sovereign"):
            StreetFactory.create(charter=charter, shops=shops)

    def test_factory_rejects_unknown_role(self):
        charter = _minimal_charter()
        shops = [_minimal_shop(role="wizard")]
        with pytest.raises(ValueError, match="role"):
            StreetFactory.create(charter=charter, shops=shops)

    def test_factory_all_universal_roles_accepted(self):
        charter = _minimal_charter()
        for role in UNIVERSAL_ROLES:
            shops = [_minimal_shop(shop_id=f"{role}_shop", role=role)]
            street = StreetFactory.create(charter=charter, shops=shops)
            assert street.charter.street_id == "test"

    def test_custom_executor(self):
        charter = _minimal_charter()
        shops = [_minimal_shop()]
        calls = []

        def track_exec(envelope, spec):
            calls.append(spec.shop_id)
            return _minimal_envelope(
                sender=spec.shop_id,
                envelope_id=f"{envelope.envelope_id}-out",
            )

        street = StreetFactory.create(
            charter=charter, shops=shops, executor=track_exec,
        )
        street.run({"task_id": "T-1", "description": "test"})
        assert "worker" in calls

    def test_custom_shop_order(self):
        charter = _minimal_charter()
        shops = [
            _minimal_shop(shop_id="b", role="producer"),
            _minimal_shop(shop_id="a", role="critic"),
        ]
        street = StreetFactory.create(
            charter=charter, shops=shops, shop_order=["a", "b"],
        )
        order = street.route_task({"task_id": "T-1"})
        assert order == ["a", "b"]


# ═══════════════════════════════════════════════════════════════════════
# STREET LIFECYCLE TESTS
# ═══════════════════════════════════════════════════════════════════════

class TestStreetLifecycle:
    """Full lifecycle: route -> run -> aggregate -> gate -> emit."""

    def test_run_produces_artifact(self):
        charter = _minimal_charter()
        shops = [_minimal_shop()]
        street = StreetFactory.create(charter=charter, shops=shops)
        result = street.run({"task_id": "T-1", "description": "test"})
        assert "artifact" in result
        assert result["artifact"]["type"] == "street_output"
        assert result["artifact"]["authority"] is False

    def test_run_produces_gate_result(self):
        charter = _minimal_charter()
        shops = [_minimal_shop()]
        street = StreetFactory.create(charter=charter, shops=shops)
        result = street.run({"task_id": "T-1", "description": "test"})
        assert isinstance(result["gate_result"], StreetGateResult)

    def test_run_produces_packet_on_pass(self):
        charter = _minimal_charter()
        shops = [_minimal_shop()]
        street = StreetFactory.create(charter=charter, shops=shops)
        result = street.run({"task_id": "T-1", "description": "test"})
        assert result["packet"] is not None
        assert result["packet"]["street_id"] == "test"

    def test_street_ledger_append_only(self):
        """MANDATORY TEST 5: ledger grows, never shrinks."""
        charter = _minimal_charter()
        shops = [_minimal_shop()]
        street = StreetFactory.create(charter=charter, shops=shops)

        street.run({"task_id": "T-1", "description": "first"})
        count_1 = len(street.ledger)
        assert count_1 >= 1

        street.run({"task_id": "T-2", "description": "second"})
        count_2 = len(street.ledger)
        assert count_2 > count_1  # grew

        # Ledger property returns a copy — can't mutate the original
        external = street.ledger
        external.clear()
        assert len(street.ledger) == count_2  # original unaffected

    def test_bus_records_all_messages(self):
        charter = _minimal_charter()
        shops = [
            _minimal_shop(shop_id="a", role="producer"),
            _minimal_shop(shop_id="b", role="critic"),
        ]
        street = StreetFactory.create(charter=charter, shops=shops)
        street.run({"task_id": "T-1", "description": "test"})
        # 1 initial TASK + 2 shop outputs = at least 3 messages
        assert street.bus.count() >= 3

    def test_multi_shop_pipeline(self):
        charter = _minimal_charter()
        shops = [
            _minimal_shop(shop_id="s1", role="producer"),
            _minimal_shop(shop_id="s2", role="critic"),
            _minimal_shop(shop_id="s3", role="gate"),
        ]
        street = StreetFactory.create(charter=charter, shops=shops)
        result = street.run({"task_id": "T-1", "description": "test"})
        assert result["artifact"]["shop_count"] == 3


# ═══════════════════════════════════════════════════════════════════════
# CODING STREET (FIRST INSTANCE)
# ═══════════════════════════════════════════════════════════════════════

class TestCodingStreet:
    """Coding Street instantiated from factory."""

    def test_coding_street_instantiates_from_factory(self):
        """MANDATORY TEST 7: Coding Street creates from factory."""
        street = create_coding_street()
        assert isinstance(street, ConcreteStreet)
        assert isinstance(street, AbstractStreet)
        assert street.charter.street_id == "coding"
        assert street.charter.name == "Coding Street"

    def test_coding_charter_mandate(self):
        assert "code" in CODING_CHARTER.mandate.lower()

    def test_coding_has_five_shops(self):
        assert len(CODING_SHOPS) == 5

    def test_coding_shop_roles_universal(self):
        for shop in CODING_SHOPS:
            assert shop.role in UNIVERSAL_ROLES

    def test_coding_all_non_sovereign(self):
        for shop in CODING_SHOPS:
            assert shop.non_sovereign is True

    def test_coding_forbidden_actions(self):
        assert "deploy_production" in CODING_CHARTER.forbidden_actions

    def test_coding_runs_task(self):
        street = create_coding_street()
        result = street.run({"task_id": "T-1", "description": "Build API"})
        assert result["artifact"]["type"] == "street_output"
        assert result["artifact"]["domain"] == "code"
        assert result["ledger_count"] >= 1


# ═══════════════════════════════════════════════════════════════════════
# MARKETING STREET (SECOND INSTANCE — PROVES FACTORY)
# ═══════════════════════════════════════════════════════════════════════

class TestMarketingStreet:
    """Marketing Street instantiated from same factory."""

    def test_marketing_street_instantiates_from_same_factory(self):
        """MANDATORY TEST 8: same factory, different charter."""
        street = create_marketing_street()
        assert isinstance(street, ConcreteStreet)
        assert isinstance(street, AbstractStreet)
        assert street.charter.street_id == "marketing"
        assert street.charter.name == "Marketing Street"

    def test_marketing_charter_mandate(self):
        assert "marketing" in MARKETING_CHARTER.mandate.lower()

    def test_marketing_has_five_shops(self):
        assert len(MARKETING_SHOPS) == 5

    def test_marketing_shop_roles_universal(self):
        for shop in MARKETING_SHOPS:
            assert shop.role in UNIVERSAL_ROLES

    def test_marketing_all_non_sovereign(self):
        for shop in MARKETING_SHOPS:
            assert shop.non_sovereign is True

    def test_marketing_forbidden_actions(self):
        assert "publish" in MARKETING_CHARTER.forbidden_actions

    def test_marketing_runs_task(self):
        street = create_marketing_street()
        result = street.run({
            "task_id": "T-1",
            "description": "Launch product campaign",
        })
        assert result["artifact"]["type"] == "street_output"
        assert result["artifact"]["domain"] == "copy"
        assert result["ledger_count"] >= 1


# ═══════════════════════════════════════════════════════════════════════
# CROSS-STREET VALIDATION
# ═══════════════════════════════════════════════════════════════════════

class TestCrossStreet:
    """Both streets share the same base layer."""

    def test_cross_street_schema_identical_at_base_layer(self):
        """MANDATORY TEST 9: same factory, same interface, different domain."""
        coding = create_coding_street()
        marketing = create_marketing_street()

        # Both are AbstractStreet
        assert isinstance(coding, AbstractStreet)
        assert isinstance(marketing, AbstractStreet)

        # Both are ConcreteStreet (same implementation class)
        assert type(coding) is type(marketing)

        # Both have the required lifecycle methods
        for method in ("load_charter", "load_shops", "route_task",
                       "run_shop", "aggregate", "gate_check",
                       "write_ledger", "emit_packet", "run"):
            assert hasattr(coding, method)
            assert hasattr(marketing, method)

    def test_different_charters_same_shape(self):
        """Charters have the same fields but different values."""
        coding_fields = set(CODING_CHARTER.__dataclass_fields__.keys())
        marketing_fields = set(MARKETING_CHARTER.__dataclass_fields__.keys())
        assert coding_fields == marketing_fields

    def test_different_domains(self):
        assert CODING_CHARTER.street_id != MARKETING_CHARTER.street_id
        assert CODING_CHARTER.allowed_domains != MARKETING_CHARTER.allowed_domains

    def test_both_produce_same_result_shape(self):
        coding = create_coding_street()
        marketing = create_marketing_street()
        r1 = coding.run({"task_id": "T-1", "description": "test"})
        r2 = marketing.run({"task_id": "T-1", "description": "test"})
        assert set(r1.keys()) == set(r2.keys())
        assert r1["artifact"]["type"] == r2["artifact"]["type"]

    def test_ledger_entries_same_schema(self):
        coding = create_coding_street()
        marketing = create_marketing_street()
        coding.run({"task_id": "T-1", "description": "c"})
        marketing.run({"task_id": "T-1", "description": "m"})
        cl = coding.ledger[0]
        ml = marketing.ledger[0]
        assert set(cl.__dataclass_fields__.keys()) == set(ml.__dataclass_fields__.keys())
        assert cl.street_id == "coding"
        assert ml.street_id == "marketing"


# ═══════════════════════════════════════════════════════════════════════
# ADVERSARIAL
# ═══════════════════════════════════════════════════════════════════════

class TestAdversarialStreet:
    """Adversarial attempts that must be blocked."""

    def test_authority_injection_blocked_by_gate(self):
        """Artifact with authority=True is blocked at gate."""
        charter = _minimal_charter()
        gate = StreetGate(charter)
        result = gate.check(
            {"type": "evil", "authority": True},
            ["r1"],
            [],
        )
        assert result.verdict == "BLOCK"

    def test_domain_escape_blocked(self):
        """Artifact from wrong domain is blocked at gate."""
        charter = _minimal_charter(allowed_domains=("code",))
        gate = StreetGate(charter)
        result = gate.check(
            {"type": "result", "domain": "finance"},
            ["r1"],
            [],
        )
        assert result.verdict == "BLOCK"

    def test_freeform_message_rejected(self):
        """Prose messages (not in allowed types) rejected by bus."""
        bus = StreetBus("test")
        for bad_type in ("CHAT", "FREEFORM", "COMMENT", "OPINION", "GOSSIP"):
            with pytest.raises(BusError):
                bus.send(_minimal_envelope(message_type=bad_type))
