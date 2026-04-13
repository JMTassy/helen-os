"""HELEN OS — Court (Unified Kernel v2) Tests.

Proves the 4 canonical decision paths plus ledger integrity,
hash chain verification, replay, and execution binding.

Governing invariant:
    SHIP ⟹ (missing = ∅) ∧ (kill = false)

Test classes:
    1. TestReducer              — Pure decision function (4 paths)
    2. TestClaim                — Claim construction + frozen
    3. TestObligation           — Obligation types
    4. TestAttestation          — Attestation construction + hashing
    5. TestOracleObligations    — Obligation generation heuristics
    6. TestCheckObligations     — LEGORACLE hard gate
    7. TestRunPipeline          — Full pipeline integration
    8. TestCourtLedger          — SQLite ledger CRUD
    9. TestLedgerHashChain      — Hash chain integrity + tamper detection
    10. TestLedgerReplay        — Decision replay from ledger
    11. TestExecutionBinding    — Real tool execution → attestation
    12. TestCourtDecision       — Decision structure + non-sovereignty
"""
import pytest

from helensh.court import (
    Claim,
    Obligation,
    Attestation,
    CourtDecision,
    CourtLedger,
    oracle_obligations,
    check_obligations,
    reducer,
    run_pipeline,
    execute_witness,
    attest_from_execution,
    GENESIS_HASH,
)
from helensh.state import canonical_hash


# ── 1. Reducer (Pure Decision Function) ──────────────────────────


class TestReducer:
    """The reducer is the SOLE authority. 4 paths only."""

    def test_ship_when_nothing_missing_no_kill(self):
        """SHIP ⟹ (missing = ∅) ∧ (kill = false)"""
        assert reducer(kill_flag=False, missing=[]) == "SHIP"

    def test_no_ship_when_missing(self):
        """missing ≠ ∅ → NO_SHIP"""
        assert reducer(kill_flag=False, missing=["basic_proof"]) == "NO_SHIP"

    def test_no_ship_when_kill(self):
        """kill = true → NO_SHIP regardless"""
        assert reducer(kill_flag=True, missing=[]) == "NO_SHIP"

    def test_no_ship_when_both(self):
        """kill + missing → NO_SHIP"""
        assert reducer(kill_flag=True, missing=["x", "y"]) == "NO_SHIP"

    def test_reducer_is_binary(self):
        """Only two possible outputs."""
        outcomes = set()
        for kill in (True, False):
            for missing in ([], ["a"], ["a", "b"]):
                outcomes.add(reducer(kill, missing))
        assert outcomes == {"SHIP", "NO_SHIP"}

    def test_empty_missing_list_ships(self):
        assert reducer(False, []) == "SHIP"

    def test_single_missing_blocks(self):
        assert reducer(False, ["one"]) == "NO_SHIP"

    def test_many_missing_blocks(self):
        assert reducer(False, ["a", "b", "c", "d"]) == "NO_SHIP"


# ── 2. Claim ─────────────────────────────────────────────────────


class TestClaim:
    """Claim construction and immutability."""

    def test_claim_construction(self):
        c = Claim(claim_id="c1", text="hello")
        assert c.claim_id == "c1"
        assert c.text == "hello"
        assert c.payload == {}
        assert c.requires_receipts is True

    def test_claim_with_payload(self):
        c = Claim(claim_id="c2", text="compute", payload={"x": 1})
        assert c.payload == {"x": 1}

    def test_claim_frozen(self):
        c = Claim(claim_id="c1", text="hello")
        with pytest.raises(AttributeError):
            c.text = "modified"

    def test_claim_equality(self):
        c1 = Claim(claim_id="c1", text="same")
        c2 = Claim(claim_id="c1", text="same")
        assert c1 == c2


# ── 3. Obligation ────────────────────────────────────────────────


class TestObligation:
    """Obligation types and properties."""

    def test_basic_obligation(self):
        o = Obligation(name="basic_proof")
        assert o.name == "basic_proof"
        assert o.attestable is True
        assert o.requires_execution is False

    def test_execution_obligation(self):
        o = Obligation(name="code_exec", requires_execution=True)
        assert o.requires_execution is True

    def test_non_attestable_obligation(self):
        o = Obligation(name="manual_only", attestable=False)
        assert o.attestable is False

    def test_obligation_frozen(self):
        o = Obligation(name="x")
        with pytest.raises(AttributeError):
            o.name = "y"


# ── 4. Attestation ──────────────────────────────────────────────


class TestAttestation:
    """Attestation construction, strength, and immutability."""

    def test_manual_attestation(self):
        a = Attestation(claim_id="c1", obligation_name="basic_proof", evidence="yes")
        assert a.tool_result_hash is None
        assert a.valid is True

    def test_execution_attestation(self):
        a = Attestation(
            claim_id="c1",
            obligation_name="code_execution",
            evidence="output",
            tool_result_hash="abc123",
        )
        assert a.tool_result_hash == "abc123"

    def test_invalid_attestation(self):
        a = Attestation(claim_id="c1", obligation_name="x", valid=False)
        assert a.valid is False

    def test_attestation_frozen(self):
        a = Attestation(claim_id="c1", obligation_name="x")
        with pytest.raises(AttributeError):
            a.valid = False


# ── 5. Oracle Obligations ───────────────────────────────────────


class TestOracleObligations:
    """Obligation generation heuristics."""

    def test_basic_claim_gets_basic_proof(self):
        c = Claim(claim_id="c1", text="hello world")
        obls = oracle_obligations(c)
        names = [o.name for o in obls]
        assert "basic_proof" in names

    def test_code_claim_gets_execution(self):
        c = Claim(claim_id="c1", text="write a function to compute fibonacci")
        obls = oracle_obligations(c)
        names = [o.name for o in obls]
        assert "code_execution" in names
        assert "output_verification" in names

    def test_code_execution_requires_execution(self):
        c = Claim(claim_id="c1", text="evaluate this code")
        obls = oracle_obligations(c)
        exec_obl = [o for o in obls if o.name == "code_execution"][0]
        assert exec_obl.requires_execution is True

    def test_non_code_claim_no_execution(self):
        c = Claim(claim_id="c1", text="what is the weather?")
        obls = oracle_obligations(c)
        names = [o.name for o in obls]
        assert "code_execution" not in names

    def test_all_obligations_have_names(self):
        c = Claim(claim_id="c1", text="a code function to calculate sums")
        obls = oracle_obligations(c)
        for o in obls:
            assert len(o.name) > 0

    def test_oracle_is_non_sovereign(self):
        """Oracle generates signals, does not decide."""
        c = Claim(claim_id="c1", text="anything")
        obls = oracle_obligations(c)
        # Obligations are proposals, not decisions
        assert isinstance(obls, list)
        for o in obls:
            assert isinstance(o, Obligation)


# ── 6. Check Obligations (LEGORACLE) ────────────────────────────


class TestCheckObligations:
    """LEGORACLE hard gate. Strictly binary."""

    def test_all_satisfied(self):
        c = Claim(claim_id="c1", text="hello")
        obls = [Obligation(name="basic_proof")]
        atts = [Attestation(claim_id="c1", obligation_name="basic_proof", evidence="yes")]
        result = check_obligations(c, obls, atts)
        assert result["missing"] == []
        assert result["satisfied"] == ["basic_proof"]

    def test_none_satisfied(self):
        c = Claim(claim_id="c1", text="hello")
        obls = [Obligation(name="basic_proof")]
        result = check_obligations(c, obls, [])
        assert result["missing"] == ["basic_proof"]
        assert result["satisfied"] == []

    def test_partial_satisfaction(self):
        c = Claim(claim_id="c1", text="hello")
        obls = [
            Obligation(name="proof_a"),
            Obligation(name="proof_b"),
        ]
        atts = [Attestation(claim_id="c1", obligation_name="proof_a", evidence="yes")]
        result = check_obligations(c, obls, atts)
        assert "proof_a" in result["satisfied"]
        assert "proof_b" in result["missing"]

    def test_execution_required_with_hash(self):
        c = Claim(claim_id="c1", text="code test")
        obls = [Obligation(name="code_execution", requires_execution=True)]
        atts = [Attestation(
            claim_id="c1",
            obligation_name="code_execution",
            evidence="ok",
            tool_result_hash="hash123",
        )]
        result = check_obligations(c, obls, atts)
        assert result["satisfied"] == ["code_execution"]

    def test_execution_required_without_hash_fails(self):
        """Manual attestation insufficient for execution obligation."""
        c = Claim(claim_id="c1", text="code test")
        obls = [Obligation(name="code_execution", requires_execution=True)]
        atts = [Attestation(
            claim_id="c1",
            obligation_name="code_execution",
            evidence="trust me",
            tool_result_hash=None,  # no execution proof
        )]
        result = check_obligations(c, obls, atts)
        assert result["missing"] == ["code_execution"]

    def test_invalid_attestation_ignored(self):
        c = Claim(claim_id="c1", text="hello")
        obls = [Obligation(name="basic_proof")]
        atts = [Attestation(claim_id="c1", obligation_name="basic_proof", valid=False)]
        result = check_obligations(c, obls, atts)
        assert result["missing"] == ["basic_proof"]

    def test_wrong_claim_id_ignored(self):
        c = Claim(claim_id="c1", text="hello")
        obls = [Obligation(name="basic_proof")]
        atts = [Attestation(claim_id="WRONG", obligation_name="basic_proof", evidence="yes")]
        result = check_obligations(c, obls, atts)
        assert result["missing"] == ["basic_proof"]

    def test_non_attestable_skipped(self):
        c = Claim(claim_id="c1", text="hello")
        obls = [
            Obligation(name="auto_only", attestable=False),
            Obligation(name="basic_proof"),
        ]
        atts = [Attestation(claim_id="c1", obligation_name="basic_proof", evidence="yes")]
        result = check_obligations(c, obls, atts)
        assert "auto_only" not in result["required"]
        assert result["missing"] == []


# ── 7. Full Pipeline ────────────────────────────────────────────


class TestRunPipeline:
    """Complete court pipeline integration."""

    def test_basic_claim_no_attestations_no_ship(self):
        """No receipts → NO_SHIP"""
        c = Claim(claim_id="c1", text="hello world")
        decision = run_pipeline(c, attestations=[])
        assert decision.decision == "NO_SHIP"
        assert len(decision.missing) > 0

    def test_basic_claim_with_proof_ships(self):
        """Full attestations → SHIP"""
        c = Claim(claim_id="c1", text="hello world")
        atts = [Attestation(claim_id="c1", obligation_name="basic_proof", evidence="yes")]
        decision = run_pipeline(c, atts)
        assert decision.decision == "SHIP"
        assert decision.missing == ()

    def test_code_claim_partial_no_ship(self):
        """Partial attestations → NO_SHIP"""
        c = Claim(claim_id="c1", text="write code to compute sum")
        # Only provide basic_proof, not code_execution or output_verification
        atts = [Attestation(claim_id="c1", obligation_name="basic_proof", evidence="yes")]
        decision = run_pipeline(c, atts)
        assert decision.decision == "NO_SHIP"
        assert len(decision.missing) > 0

    def test_code_claim_full_attestations_ships(self):
        """Code claim with all attestations → SHIP"""
        c = Claim(claim_id="c1", text="write code to compute sum")
        atts = [
            Attestation(
                claim_id="c1",
                obligation_name="code_execution",
                evidence="42",
                tool_result_hash="hash_abc",
            ),
            Attestation(claim_id="c1", obligation_name="output_verification", evidence="42"),
            Attestation(claim_id="c1", obligation_name="basic_proof", evidence="yes"),
        ]
        decision = run_pipeline(c, atts)
        assert decision.decision == "SHIP"

    def test_kill_switch_overrides(self):
        """Kill switch → NO_SHIP regardless of attestations."""
        c = Claim(claim_id="c1", text="hello world")
        atts = [Attestation(claim_id="c1", obligation_name="basic_proof", evidence="yes")]
        decision = run_pipeline(c, atts, kill_flag=True)
        assert decision.decision == "NO_SHIP"
        assert decision.kill_flag is True

    def test_decision_has_receipt_hash(self):
        c = Claim(claim_id="c1", text="hello")
        decision = run_pipeline(c, [])
        assert len(decision.receipt_hash) == 64

    def test_decision_non_sovereign(self):
        """authority is always False."""
        c = Claim(claim_id="c1", text="hello")
        decision = run_pipeline(c, [])
        assert decision.authority is False

    def test_pipeline_deterministic(self):
        """Same inputs → same decision."""
        c = Claim(claim_id="c1", text="hello")
        atts = [Attestation(claim_id="c1", obligation_name="basic_proof", evidence="yes")]
        d1 = run_pipeline(c, atts)
        d2 = run_pipeline(c, atts)
        assert d1.receipt_hash == d2.receipt_hash
        assert d1.decision == d2.decision

    def test_decision_required_tuple(self):
        c = Claim(claim_id="c1", text="hello")
        decision = run_pipeline(c, [])
        assert isinstance(decision.required, tuple)
        assert isinstance(decision.satisfied, tuple)
        assert isinstance(decision.missing, tuple)


# ── 8. Court Ledger ─────────────────────────────────────────────


class TestCourtLedger:
    """SQLite ledger CRUD operations."""

    def test_empty_ledger(self):
        ledger = CourtLedger(":memory:")
        assert ledger.count() == 0
        assert ledger.get_all() == []
        ledger.close()

    def test_record_claim(self):
        ledger = CourtLedger(":memory:")
        c = Claim(claim_id="c1", text="hello")
        receipt = ledger.record_claim(c)
        assert receipt["type"] == "CLAIM"
        assert receipt["payload"]["claim_id"] == "c1"
        assert ledger.count() == 1
        ledger.close()

    def test_record_attestation(self):
        ledger = CourtLedger(":memory:")
        a = Attestation(claim_id="c1", obligation_name="proof", evidence="yes")
        receipt = ledger.record_attestation(a)
        assert receipt["type"] == "ATTESTATION"
        assert receipt["payload"]["obligation_name"] == "proof"
        ledger.close()

    def test_record_decision(self):
        ledger = CourtLedger(":memory:")
        d = CourtDecision(
            claim_id="c1",
            decision="SHIP",
            required=("basic_proof",),
            satisfied=("basic_proof",),
            missing=(),
            kill_flag=False,
            receipt_hash="abc",
        )
        receipt = ledger.record_decision(d)
        assert receipt["type"] == "DECISION"
        assert receipt["payload"]["decision"] == "SHIP"
        ledger.close()

    def test_get_by_type(self):
        ledger = CourtLedger(":memory:")
        c = Claim(claim_id="c1", text="hello")
        a = Attestation(claim_id="c1", obligation_name="proof", evidence="yes")
        ledger.record_claim(c)
        ledger.record_attestation(a)
        claims = ledger.get_by_type("CLAIM")
        atts = ledger.get_by_type("ATTESTATION")
        assert len(claims) == 1
        assert len(atts) == 1
        ledger.close()

    def test_get_all_preserves_order(self):
        ledger = CourtLedger(":memory:")
        ledger.record_claim(Claim(claim_id="c1", text="first"))
        ledger.record_claim(Claim(claim_id="c2", text="second"))
        ledger.record_claim(Claim(claim_id="c3", text="third"))
        entries = ledger.get_all()
        assert entries[0]["payload"]["claim_id"] == "c1"
        assert entries[1]["payload"]["claim_id"] == "c2"
        assert entries[2]["payload"]["claim_id"] == "c3"
        ledger.close()

    def test_receipt_has_hash(self):
        ledger = CourtLedger(":memory:")
        receipt = ledger.record_claim(Claim(claim_id="c1", text="hello"))
        assert len(receipt["hash"]) == 64
        ledger.close()

    def test_receipt_has_timestamp(self):
        ledger = CourtLedger(":memory:")
        receipt = ledger.record_claim(Claim(claim_id="c1", text="hello"))
        assert receipt["timestamp"] > 0
        ledger.close()

    def test_genesis_previous_hash(self):
        ledger = CourtLedger(":memory:")
        receipt = ledger.record_claim(Claim(claim_id="c1", text="hello"))
        assert receipt["previous_hash"] == GENESIS_HASH
        ledger.close()

    def test_get_attestations_for_claim(self):
        ledger = CourtLedger(":memory:")
        ledger.record_attestation(Attestation(claim_id="c1", obligation_name="a", evidence="1"))
        ledger.record_attestation(Attestation(claim_id="c2", obligation_name="b", evidence="2"))
        ledger.record_attestation(Attestation(claim_id="c1", obligation_name="c", evidence="3"))
        atts = ledger.get_attestations_for("c1")
        assert len(atts) == 2
        names = [a.obligation_name for a in atts]
        assert "a" in names
        assert "c" in names
        ledger.close()


# ── 9. Ledger Hash Chain ────────────────────────────────────────


class TestLedgerHashChain:
    """Hash chain integrity and tamper detection."""

    def test_chain_links_correctly(self):
        ledger = CourtLedger(":memory:")
        r1 = ledger.record_claim(Claim(claim_id="c1", text="first"))
        r2 = ledger.record_claim(Claim(claim_id="c2", text="second"))
        assert r2["previous_hash"] == r1["hash"]
        ledger.close()

    def test_three_entry_chain(self):
        ledger = CourtLedger(":memory:")
        r1 = ledger.record_claim(Claim(claim_id="c1", text="one"))
        r2 = ledger.record_attestation(Attestation(claim_id="c1", obligation_name="p", evidence="e"))
        r3 = ledger.record_decision(CourtDecision(
            claim_id="c1", decision="SHIP",
            required=("p",), satisfied=("p",), missing=(),
            kill_flag=False, receipt_hash="x",
        ))
        assert r1["previous_hash"] == GENESIS_HASH
        assert r2["previous_hash"] == r1["hash"]
        assert r3["previous_hash"] == r2["hash"]
        ledger.close()

    def test_verify_chain_valid(self):
        ledger = CourtLedger(":memory:")
        ledger.record_claim(Claim(claim_id="c1", text="one"))
        ledger.record_claim(Claim(claim_id="c2", text="two"))
        ledger.record_claim(Claim(claim_id="c3", text="three"))
        ok, errors = ledger.verify_chain()
        assert ok is True
        assert errors == []
        ledger.close()

    def test_verify_chain_detects_tamper(self):
        """Tampered payload breaks hash chain."""
        ledger = CourtLedger(":memory:")
        ledger.record_claim(Claim(claim_id="c1", text="one"))
        ledger.record_claim(Claim(claim_id="c2", text="two"))
        # Tamper with the first entry's payload
        ledger._conn.execute(
            "UPDATE ledger SET payload = ? WHERE seq = 1",
            ('{"claim_id":"c1","payload":{},"requires_receipts":true,"text":"TAMPERED"}',),
        )
        ledger._conn.commit()
        ok, errors = ledger.verify_chain()
        assert ok is False
        assert len(errors) > 0
        ledger.close()

    def test_verify_chain_detects_hash_swap(self):
        """Swapped hash breaks chain."""
        ledger = CourtLedger(":memory:")
        ledger.record_claim(Claim(claim_id="c1", text="one"))
        ledger.record_claim(Claim(claim_id="c2", text="two"))
        ledger.record_claim(Claim(claim_id="c3", text="three"))
        # Swap hashes of entry 1 and 2
        ledger._conn.execute(
            "UPDATE ledger SET hash = 'bogus_hash_000000000000000000000000000000000000000000000000000000000000' WHERE seq = 2"
        )
        ledger._conn.commit()
        ok, errors = ledger.verify_chain()
        assert ok is False
        ledger.close()

    def test_empty_chain_is_valid(self):
        ledger = CourtLedger(":memory:")
        ok, errors = ledger.verify_chain()
        assert ok is True
        assert errors == []
        ledger.close()

    def test_single_entry_chain_valid(self):
        ledger = CourtLedger(":memory:")
        ledger.record_claim(Claim(claim_id="c1", text="only"))
        ok, errors = ledger.verify_chain()
        assert ok is True
        ledger.close()


# ── 10. Ledger Replay ───────────────────────────────────────────


class TestLedgerReplay:
    """Decision replay from ledger."""

    def test_replay_empty(self):
        ledger = CourtLedger(":memory:")
        decisions = ledger.replay_decisions()
        assert decisions == []
        ledger.close()

    def test_replay_captures_decisions(self):
        ledger = CourtLedger(":memory:")
        d = CourtDecision(
            claim_id="c1", decision="SHIP",
            required=("basic_proof",), satisfied=("basic_proof",), missing=(),
            kill_flag=False, receipt_hash="abc",
        )
        ledger.record_decision(d)
        decisions = ledger.replay_decisions()
        assert len(decisions) == 1
        assert decisions[0]["payload"]["decision"] == "SHIP"
        ledger.close()

    def test_replay_preserves_order(self):
        ledger = CourtLedger(":memory:")
        for i in range(5):
            ledger.record_decision(CourtDecision(
                claim_id=f"c{i}", decision="SHIP" if i % 2 == 0 else "NO_SHIP",
                required=(), satisfied=(), missing=() if i % 2 == 0 else ("x",),
                kill_flag=False, receipt_hash=f"h{i}",
            ))
        decisions = ledger.replay_decisions()
        assert len(decisions) == 5
        assert decisions[0]["payload"]["claim_id"] == "c0"
        assert decisions[4]["payload"]["claim_id"] == "c4"
        ledger.close()

    def test_replay_ignores_non_decisions(self):
        ledger = CourtLedger(":memory:")
        ledger.record_claim(Claim(claim_id="c1", text="claim"))
        ledger.record_attestation(Attestation(claim_id="c1", obligation_name="p", evidence="e"))
        ledger.record_decision(CourtDecision(
            claim_id="c1", decision="SHIP",
            required=("p",), satisfied=("p",), missing=(),
            kill_flag=False, receipt_hash="x",
        ))
        decisions = ledger.replay_decisions()
        assert len(decisions) == 1
        ledger.close()


# ── 11. Execution Binding ───────────────────────────────────────


class TestExecutionBinding:
    """Real tool execution produces witnessed attestations."""

    def test_execute_witness_success(self):
        result, result_hash = execute_witness("x = 2 + 2\nprint(x)")
        assert result.success is True
        assert "4" in result.output
        assert len(result_hash) == 64

    def test_execute_witness_failure(self):
        result, result_hash = execute_witness("raise ValueError('boom')")
        assert result.success is False
        assert len(result_hash) == 64

    def test_attest_from_execution_success(self):
        att, result = attest_from_execution("c1", "code_execution", "print('hello')")
        assert att.claim_id == "c1"
        assert att.obligation_name == "code_execution"
        assert att.tool_result_hash is not None
        assert att.valid is True
        assert "hello" in att.evidence

    def test_attest_from_execution_failure(self):
        att, result = attest_from_execution("c1", "code_execution", "1/0")
        assert att.valid is False
        assert att.tool_result_hash is not None  # still hashed

    def test_execution_attestation_satisfies_requirement(self):
        """Execution-backed attestation satisfies requires_execution obligation."""
        att, _ = attest_from_execution("c1", "code_execution", "print(42)")
        c = Claim(claim_id="c1", text="code")
        obls = [Obligation(name="code_execution", requires_execution=True)]
        result = check_obligations(c, obls, [att])
        assert result["satisfied"] == ["code_execution"]
        assert result["missing"] == []

    def test_execution_output_is_deterministic(self):
        """Same code → same output (hash includes timing so differs)."""
        r1, _ = execute_witness("print(42)")
        r2, _ = execute_witness("print(42)")
        assert r1.output == r2.output
        assert r1.success == r2.success


# ── 12. Court Decision Structure ────────────────────────────────


class TestCourtDecision:
    """Decision structure and non-sovereignty."""

    def test_decision_frozen(self):
        d = CourtDecision(
            claim_id="c1", decision="SHIP",
            required=(), satisfied=(), missing=(),
            kill_flag=False, receipt_hash="x",
        )
        with pytest.raises(AttributeError):
            d.decision = "NO_SHIP"

    def test_authority_always_false(self):
        d = CourtDecision(
            claim_id="c1", decision="SHIP",
            required=(), satisfied=(), missing=(),
            kill_flag=False, receipt_hash="x",
        )
        assert d.authority is False

    def test_authority_cannot_be_set_true(self):
        """Even if constructed with authority=True, frozen prevents mutation."""
        d = CourtDecision(
            claim_id="c1", decision="SHIP",
            required=(), satisfied=(), missing=(),
            kill_flag=False, receipt_hash="x",
            authority=True,  # attempted override
        )
        # The value is set but the reducer never produces this
        # Structural guard: run_pipeline always produces authority=False
        with pytest.raises(AttributeError):
            d.authority = True

    def test_pipeline_never_sets_authority(self):
        """run_pipeline structurally produces authority=False."""
        c = Claim(claim_id="c1", text="hello")
        atts = [Attestation(claim_id="c1", obligation_name="basic_proof", evidence="yes")]
        d = run_pipeline(c, atts)
        assert d.authority is False

    def test_decision_claim_id_matches(self):
        c = Claim(claim_id="my_claim", text="hello")
        d = run_pipeline(c, [])
        assert d.claim_id == "my_claim"


# ── 13. End-to-End Integration ──────────────────────────────────


class TestEndToEnd:
    """Full lifecycle: claim → attest → decide → ledger → verify."""

    def test_full_lifecycle(self):
        ledger = CourtLedger(":memory:")

        # 1. Submit claim
        claim = Claim(claim_id="e2e_1", text="compute 2+2 with code")
        ledger.record_claim(claim)

        # 2. Generate obligations
        obls = oracle_obligations(claim)
        assert len(obls) >= 2  # code_execution + basic_proof at minimum

        # 3. Execute and attest
        att_exec, _ = attest_from_execution("e2e_1", "code_execution", "print(2+2)")
        ledger.record_attestation(att_exec)

        att_verify = Attestation(
            claim_id="e2e_1",
            obligation_name="output_verification",
            evidence="4",
        )
        ledger.record_attestation(att_verify)

        att_proof = Attestation(
            claim_id="e2e_1",
            obligation_name="basic_proof",
            evidence="executed and verified",
        )
        ledger.record_attestation(att_proof)

        # 4. Run pipeline
        all_atts = [att_exec, att_verify, att_proof]
        decision = run_pipeline(claim, all_atts)
        ledger.record_decision(decision)

        # 5. Verify
        assert decision.decision == "SHIP"
        assert decision.missing == ()

        # 6. Chain integrity
        ok, errors = ledger.verify_chain()
        assert ok is True
        assert errors == []

        # 7. Replay
        decisions = ledger.replay_decisions()
        assert len(decisions) == 1
        assert decisions[0]["payload"]["decision"] == "SHIP"

        # 8. Ledger count
        assert ledger.count() == 5  # 1 claim + 3 attestations + 1 decision

        ledger.close()

    def test_lifecycle_no_ship_then_ship(self):
        """First attempt NO_SHIP, then fix and SHIP."""
        ledger = CourtLedger(":memory:")

        claim = Claim(claim_id="retry_1", text="hello world")
        ledger.record_claim(claim)

        # First attempt: no attestations
        d1 = run_pipeline(claim, [])
        ledger.record_decision(d1)
        assert d1.decision == "NO_SHIP"

        # Fix: provide attestation
        att = Attestation(claim_id="retry_1", obligation_name="basic_proof", evidence="done")
        ledger.record_attestation(att)

        d2 = run_pipeline(claim, [att])
        ledger.record_decision(d2)
        assert d2.decision == "SHIP"

        # Both decisions in replay
        decisions = ledger.replay_decisions()
        assert len(decisions) == 2
        assert decisions[0]["payload"]["decision"] == "NO_SHIP"
        assert decisions[1]["payload"]["decision"] == "SHIP"

        # Chain still valid
        ok, _ = ledger.verify_chain()
        assert ok is True

        ledger.close()
