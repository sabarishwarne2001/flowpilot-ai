"""Phase 1 batch engine: schema self-healing and dispatch decisions, as pure functions.

No database: the planner and the lane decision read only what they are given.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.batches import canonical as c
from app.services.batches import healing as h
from app.services.batches.confidence import DocumentConfidence
from app.services.batches.dispatch import Policy, decide

pytestmark = pytest.mark.no_db

INVOICE = c.BUILTIN["invoice"]
CONTRACT = c.BUILTIN["contract"]
POLICY = Policy(Decimal("0.90"), Decimal("0.60"), True, True, True)


def _by_field(plan: h.HealingPlan) -> dict[str, list[h.Change]]:
    out: dict[str, list[h.Change]] = {}
    for change in plan.changes:
        out.setdefault(change.field, []).append(change)
    return out


# ------------------------------------------------------------------ value readers

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$1,250.00", Decimal("1250.00")),
        ("1.234,56 EUR", Decimal("1234.56")),
        ("(45.10)", Decimal("-45.10")),
        ("12,5", Decimal("12.5")),
        ("1,234,567", Decimal("1234567")),
        ("₹ 9,999", Decimal("9999")),
        ("100 CR", Decimal("100")),
        ("250-", Decimal("-250")),
        (" USD 75 ", Decimal("75")),
        (42, Decimal("42")),
    ],
)
def test_numbers_are_read_from_money_text(raw, expected):
    assert h.parse_number(raw) == expected


@pytest.mark.parametrize("raw", ["TBC", "", "12/03/2026", "1.2.3", True, None, "abc 12"])
def test_text_that_is_not_a_number_is_not_guessed(raw):
    assert h.parse_number(raw) is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-01-12", ("2026-01-12", False)),
        ("2026-01-12T10:00:00Z", ("2026-01-12", False)),
        ("2026/1/12", ("2026-01-12", False)),
        ("20260112", ("2026-01-12", False)),
        ("12 Jan 2026", ("2026-01-12", False)),
        ("12th January 2026", ("2026-01-12", False)),
        ("Jan 12, 2026", ("2026-01-12", False)),
        ("25/12/2026", ("2026-12-25", False)),
        ("12/25/2026", ("2026-12-25", False)),
        ("03/04/2026", (None, True)),
        ("07/07/26", ("2026-07-07", False)),
        ("31/02/2026", (None, False)),
        ("soon", (None, False)),
    ],
)
def test_dates_are_read_and_ambiguity_is_reported(raw, expected):
    assert h.parse_date(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("usd", "USD"), ("US Dollars", "USD"), ("€", "EUR"), ("pounds sterling", "GBP"), ("Rs.", "INR"), ("XYZ", "XYZ"),
     ("monopoly money", None)],
)
def test_currencies_become_iso_codes(raw, expected):
    assert h.parse_currency(raw) == expected


# ------------------------------------------------------------------ the planner

def test_drifted_keys_are_renamed_and_values_retyped():
    entities = {
        "Vendor Name": "  Acme   Industrial  Ltd ",
        "invoice_total": "$1,250.00",
        "VAT": "250,00",
        "currency_code": "us dollars",
        "Invoice Date": "12 Jan 2026",
        "invoiceNo": "INV-1",
        "classification_details": {"document_classification": "Invoice"},
        "custom_reference": "keep me",
    }

    plan = h.plan(entities, INVOICE)

    assert plan.healed["vendor_name"] == "Acme Industrial Ltd"
    assert plan.healed["total_amount"] == 1250
    assert plan.healed["tax_amount"] == 250
    assert plan.healed["currency"] == "USD"
    assert plan.healed["date"] == "2026-01-12"
    assert plan.healed["invoice_number"] == "INV-1"
    assert plan.healed["custom_reference"] == "keep me"
    assert plan.healed["classification_details"] == {"document_classification": "Invoice"}
    for drifted in ("Vendor Name", "invoice_total", "VAT", "currency_code", "Invoice Date", "invoiceNo"):
        assert drifted not in plan.healed
    renames = {ch.from_field: ch.field for ch in plan.changes if ch.kind == "RENAME"}
    assert renames["Vendor Name"] == "vendor_name"
    assert renames["invoice_total"] == "total_amount"
    assert plan.issues == []
    assert plan.state == "HEALABLE"
    assert plan.completeness == 1.0
    assert plan.document_type == "Invoice"


def test_a_close_misspelling_is_renamed_but_a_distant_key_is_left_alone():
    plan = h.plan({"vendr_name": "Acme", "totl_amount": "10", "date": "2026-01-01", "weather": "sunny"}, INVOICE)

    assert plan.healed["vendor_name"] == "Acme"
    assert plan.healed["total_amount"] == 10
    assert plan.healed["weather"] == "sunny"


def test_missing_required_fields_and_unreadable_values_are_reported_not_guessed():
    plan = h.plan({"vendor_name": "Acme", "total_amount": "TBC"}, INVOICE)

    kinds = {(i.kind, i.field) for i in plan.issues}
    assert ("UNPARSEABLE", "total_amount") in kinds
    assert ("MISSING_REQUIRED", "date") in kinds
    assert plan.healed["total_amount"] == "TBC"
    assert plan.missing_required == ["date"]
    assert plan.state == "NEEDS_ATTENTION"
    assert plan.completeness == round(2 / 3, 4)


def test_an_ambiguous_date_is_flagged():
    plan = h.plan({"vendor_name": "A", "total_amount": 1, "date": "03/04/2026"}, INVOICE)

    assert [i.kind for i in plan.issues] == ["AMBIGUOUS_DATE"]
    assert plan.healed["date"] == "03/04/2026"


def test_a_drifted_key_that_disagrees_with_the_schema_field_is_a_conflict():
    plan = h.plan({"vendor_name": "Acme", "supplier": "Globex", "total_amount": 1, "date": "2026-01-01"}, INVOICE)

    assert [(i.kind, i.field) for i in plan.issues] == [("CONFLICT", "vendor_name")]
    assert plan.healed["vendor_name"] == "Acme"
    assert plan.healed["supplier"] == "Globex"


def test_a_drifted_key_repeating_the_schema_field_is_dropped():
    plan = h.plan({"vendor_name": "Acme", "supplier": "Acme", "total_amount": 1, "date": "2026-01-01"}, INVOICE)

    assert "supplier" not in plan.healed
    assert plan.issues == []


def test_lists_are_split_and_a_healthy_document_needs_nothing():
    plan = h.plan({"agreement_date": "1 March 2026", "parties": "Acme Ltd; Contoso Retail"}, CONTRACT)
    assert plan.healed["party_names"] == ["Acme Ltd", "Contoso Retail"]
    assert plan.healed["agreement_date"] == "2026-03-01"

    healthy = h.plan(plan.healed, CONTRACT)
    assert healthy.changes == [] and healthy.issues == []
    assert healthy.state == "HEALTHY"


def test_no_schema_means_no_changes():
    plan = h.plan({"anything": "x"}, None)

    assert plan.state == "NO_SCHEMA"
    assert plan.healed == {"anything": "x"}


def test_the_digest_ignores_key_order():
    assert h.entities_digest({"a": 1, "b": [1, 2]}) == h.entities_digest({"b": [1, 2], "a": 1})
    assert h.entities_digest({"a": 1}) != h.entities_digest({"a": 2})


def test_classifier_labels_choose_a_builtin_schema():
    assert c.normalize_key("Purchase Order") == "purchase_order"
    assert c.normalize_key("vendorName") == "vendor_name"
    assert c.document_type_of({"classification_details": {"document_classification": "Receipt"}}) == "Receipt"


# ------------------------------------------------------------------ dispatch

def _verified(score, *, blocking=False, reason=None):
    return DocumentConfidence(work_item_id=None, confidence=score, verification_status="AGREED",
                              blocking=blocking, reason=reason)


HEALTHY = h.plan({"vendor_name": "A", "total_amount": 1, "date": "2026-01-01"}, INVOICE)
ENTITIES = {"vendor_name": "A", "total_amount": 1, "date": "2026-01-01"}


def _decide(**overrides):
    args = dict(status="COMPLETED", failure_reason=None, entities=ENTITIES, confidence=_verified(0.97),
                healing=HEALTHY, policy=POLICY)
    args.update(overrides)
    return decide(**args)


def test_a_confident_complete_document_goes_straight_through():
    decision = _decide()
    assert decision.lane == "STRAIGHT_THROUGH"
    assert decision.reasons == ("Confidence 97% and every required field is present.",)


def test_below_the_threshold_goes_to_review_and_below_the_floor_is_an_exception():
    assert _decide(confidence=_verified(0.85)).lane == "REVIEW"
    low = _decide(confidence=_verified(0.42))
    assert low.lane == "EXCEPTION"
    assert low.reasons[0] == "Confidence 42% is below the 60% floor."


def test_an_unverified_document_is_reviewed_not_assumed_confident():
    decision = _decide(confidence=None)
    assert decision.lane == "REVIEW"
    assert "no confidence score" in decision.reasons[0]


def test_the_review_queue_holds_win_over_a_high_score():
    decision = _decide(confidence=_verified(1.0, blocking=True, reason="No calibration has been fitted."))
    assert decision.lane == "REVIEW"
    assert decision.reasons == ("No calibration has been fitted.",)


def test_failures_missing_fields_and_processing_documents():
    failed = _decide(status="FAILED", failure_reason="PDF could not be parsed")
    assert (failed.lane, failed.reasons) == ("EXCEPTION", ("Processing failed: PDF could not be parsed",))
    assert _decide(status="PROCESSING").lane is None
    assert _decide(entities={"classification_details": {}}).lane == "EXCEPTION"
    missing = _decide(healing=h.plan({"vendor_name": "A", "total_amount": 1}, INVOICE))
    assert missing.lane == "REVIEW"
    assert missing.reasons == ("Required field missing: date.",)


def test_a_policy_that_does_not_require_fields_lets_them_through():
    lenient = Policy(Decimal("0.90"), Decimal("0.60"), False, True, False)
    decision = _decide(policy=lenient, healing=h.plan({"vendor_name": "A", "total_amount": 1}, INVOICE))
    assert decision.lane == "STRAIGHT_THROUGH"
