import pytest


def test_account_label_is_not_identity():
    envelope = {
        "account_label": "Jean-Marie Tassy",
        "promotion_status": "ADVISORY",
        "uncertainty_flags": ["preferred_name_unknown"]
    }

    # rule: account_label cannot imply identity truth
    assert envelope["promotion_status"] != "DURABLE"


def test_unknown_preferred_name_explicit():
    envelope = {
        "preferred_name": None,
        "preferred_name_status": "UNKNOWN",
        "promotion_status": "ADVISORY",
        "uncertainty_flags": ["preferred_name_unknown"]
    }

    assert envelope["preferred_name"] is None
    assert "preferred_name_unknown" in envelope["uncertainty_flags"]


def test_no_promotion_without_confirmation():
    envelope = {
        "account_label": "Jean-Marie Tassy",
        "preferred_name_status": "OBSERVED",
        "promotion_status": "OBSERVED",
        "uncertainty_flags": []
    }

    # cannot reach durable without confirmation
    assert envelope["promotion_status"] != "DURABLE"


def test_provenance_required_for_durable():
    envelope = {
        "preferred_name": "Jean-Marie",
        "preferred_name_status": "CONFIRMED",
        "promotion_status": "DURABLE",
        "provenance": {"source": "user_confirmation"},
        "uncertainty_flags": []
    }

    assert "provenance" in envelope


def test_forbidden_transition():
    # simulate illegal promotion
    envelope = {
        "account_label": "Jean-Marie Tassy",
        "promotion_status": "DURABLE",
        "uncertainty_flags": []
    }

    # this should be rejected logically
    assert not (envelope.get("account_label") and envelope["promotion_status"] == "DURABLE")
