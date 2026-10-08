"""TruthMesh's pure engine across domains: twins, links, conflicts, ripples and risk. No database.

Each case is a small mesh a reviewer would recognise, and the assertion is what that reviewer would
conclude: a statement of work governed by an MSA must not change its payment terms; an invoice dated
after the agreement ends has no cover; an invoice may not exceed its order; a claim is made under a
policy; a waybill is declared in a customs manifest; spend with nothing behind it is flagged.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.truthmesh import conflicts as C
from app.services.truthmesh import facts as F
from app.services.truthmesh import linker as L
from app.services.truthmesh import ripple as R
from app.services.truthmesh import risk as RK

pytestmark = pytest.mark.no_db

ACME = "Acme Industrial Supplies Ltd"
CARE = "Caretakers Global Inc"


def twin(name: str, entities: dict, *, text: str = "", label: str | None = None, role: str | None = None,
         number: str | None = None, total: int | None = None, currency: str | None = None,
         centroid: list[float] | None = None) -> F.Twin:
    return F.read(F.DocSource(work_item_id=uuid.uuid5(uuid.NAMESPACE_URL, name), filename=name, entities=entities,
                              text=text, classification=label, role=role, role_number=number,
                              role_total_micros=total, role_currency=currency, centroid=centroid))


def msa(**extra) -> F.Twin:
    return twin("msa.pdf", {"party_names": [CARE, ACME], "agreement_number": "MSA-2026-14",
                            "agreement_date": "2026-01-01", "termination_date": "2026-12-31", **extra},
                text="1. Payment. Customer shall pay each undisputed invoice within 30 days of receipt.\n"
                     "2. Delivery. Supplier shall deliver the goods within 21 days of each order.\n"
                     "3. Late delivery. Liquidated damages of 1% of the order value per week of delay.\n"
                     "4. Termination. Either party may terminate with 60 days written notice before 2026-11-01.",
                label="Master Services Agreement")


def links_and_conflicts(*twins: F.Twin):
    links = L.build_links(list(twins))
    return links, C.detect(list(twins), links)


def kinds(conflicts) -> set[tuple[str, str]]:
    return {(c.kind, c.severity) for c in conflicts}


def relation(links, a: F.Twin, b: F.Twin) -> str:
    pair = {a.work_item_id, b.work_item_id}
    [link] = [l for l in links if {l.source, l.target} == pair]
    return link.relation


# --------------------------------------------------------------------------- twins


def test_kinds_come_from_the_label_then_the_role_then_the_file_name() -> None:
    assert F.kind_of(role="CONTRACT", classification="Contract", filename="contract-MSA-E2E.pdf", entities={}) \
        == "MASTER_AGREEMENT"
    assert F.kind_of(role=None, classification="Statement of Work", filename="x.pdf", entities={}) \
        == "STATEMENT_OF_WORK"
    assert F.kind_of(role="GOODS_RECEIPT", classification="Receipt", filename="g.pdf", entities={}) == "GOODS_RECEIPT"
    assert F.kind_of(role=None, classification="Other", filename="AWB-123 airway bill.pdf", entities={}) == "WAYBILL"
    assert F.kind_of(role=None, classification=None, filename="scan.pdf",
                     entities={"claim_number": "C-1", "policy_number": "P-1"}) == "INSURANCE_CLAIM"
    # A policy ABOUT purchase orders is not one: the matcher's role (from the header) says OTHER.
    assert F.kind_of(role="OTHER", classification="Purchase Order", filename="Procurement_Policy.pdf",
                     entities={}) == "OTHER"


def test_terms_are_read_with_the_sentence_they_come_from() -> None:
    m = msa()
    assert m.terms["payment_days"]["value"] == 30
    assert m.terms["delivery_days"]["value"] == 21
    assert m.terms["notice_days"]["value"] == 60 and m.terms["termination_deadline"]["value"] == "2026-11-01"
    assert m.terms["liquidated_damages_pct"]["value"] == 1.0 and m.terms["liquidated_damages_pct"]["unit"] == "%/week"
    assert "within 30 days" in m.terms["payment_days"]["quote"]


def test_own_numbers_and_references_are_told_apart() -> None:
    inv = twin("inv.pdf", {"invoice_number": "INV-7", "po_number": "PO-9", "vendor_tax_id": "GB123456789"},
               role="INVOICE", text="As agreed under MSA-2026-14.")
    assert inv.identifiers == ["INV7"]
    assert inv.referenced_identifiers == ["PO9"]
    assert "MSA202614" in inv.text_references
    assert "GB123456789" not in inv.referenced_identifiers


# --------------------------------------------------------------------------- links and conflicts


def test_a_sow_with_different_payment_terms_than_its_msa_is_a_term_conflict() -> None:
    m = msa()
    sow = twin("sow.pdf", {"sow_number": "SOW-3", "msa_reference": "MSA-2026-14", "party_names": [CARE, ACME],
                           "payment_terms": "Net 45", "date": "2026-03-01"}, label="Statement of Work")
    links, found = links_and_conflicts(m, sow)
    assert relation(links, sow, m) == "GOVERNED_BY"
    term = [c for c in found if c.kind == "TERM_CONFLICT" and c.concept == "payment_days"]
    assert term and "45 days" in term[0].summary and "30 days" in term[0].summary


def test_an_invoice_dated_after_the_agreement_ends_is_out_of_term() -> None:
    m = msa()
    late = twin("late.pdf", {"invoice_number": "INV-99", "vendor_name": ACME, "customer_name": CARE,
                             "date": "2027-02-01", "total_amount": "500.00", "currency": "USD"},
                role="INVOICE", text="Billed under MSA-2026-14.")
    links, found = links_and_conflicts(m, late)
    assert relation(links, late, m) == "BILLED_UNDER"
    assert ("OUT_OF_TERM", "HIGH") in kinds(found)


def test_an_invoice_over_its_order_is_an_unauthorised_commitment() -> None:
    po = twin("po.pdf", {"po_number": "PO-1", "vendor_name": ACME, "total_amount": "1000.00", "currency": "USD",
                         "date": "2026-05-01"}, role="PURCHASE_ORDER")
    inv = twin("inv.pdf", {"invoice_number": "INV-1", "po_number": "PO-1", "vendor_name": ACME,
                           "total_amount": "1400.00", "currency": "USD", "date": "2026-05-10"}, role="INVOICE")
    _, found = links_and_conflicts(po, inv)
    over = next(c for c in found if c.kind == "UNAUTHORIZED_COMMITMENT")
    assert over.severity == "CRITICAL" and over.exposure_micros == 400_000_000
    assert "USD 400.00 (40.0%)" in over.summary


def test_tax_on_top_of_an_order_is_not_an_overrun() -> None:
    po = twin("po.pdf", {"po_number": "PO-2", "total_amount": "1100.00", "currency": "USD"}, role="PURCHASE_ORDER")
    inv = twin("inv.pdf", {"invoice_number": "INV-2", "po_number": "PO-2", "subtotal": "1100.00",
                           "tax_amount": "150.00", "total_amount": "1250.00", "currency": "USD"}, role="INVOICE")
    _, found = links_and_conflicts(po, inv)
    assert "UNAUTHORIZED_COMMITMENT" not in {c.kind for c in found}


def test_an_invoice_before_its_order_and_in_another_currency() -> None:
    po = twin("po.pdf", {"po_number": "PO-3", "total_amount": "100.00", "currency": "EUR", "date": "2026-06-10"},
              role="PURCHASE_ORDER")
    inv = twin("inv.pdf", {"invoice_number": "INV-3", "po_number": "PO-3", "total_amount": "100.00",
                           "currency": "USD", "date": "2026-06-01"}, role="INVOICE")
    _, found = links_and_conflicts(po, inv)
    assert {("DATE_CONTRADICTION", "MEDIUM"), ("CURRENCY_MISMATCH", "HIGH")} <= kinds(found)


def test_a_vendor_that_is_not_a_party_to_the_agreement() -> None:
    m = msa()
    other = twin("inv.pdf", {"invoice_number": "INV-4", "agreement_number": "MSA-2026-14",
                             "vendor_name": "Globex Corporation", "date": "2026-04-01"}, role="INVOICE")
    _, found = links_and_conflicts(m, other)
    assert ("PARTY_MISMATCH", "HIGH") in kinds(found)


def test_one_change_of_payee_account_is_one_conflict_however_many_copies() -> None:
    def invoice(name: str, number: str, account: str, day: str) -> F.Twin:
        return twin(name, {"invoice_number": number, "po_number": "PO-7001", "vendor_name": ACME,
                           "vendor_bank_account": account, "total_amount": "10.00", "currency": "USD",
                           "date": day}, role="INVOICE")

    po = twin("po.pdf", {"po_number": "PO-7001", "vendor_name": ACME, "total_amount": "100.00", "currency": "USD"},
              role="PURCHASE_ORDER")
    old = invoice("old.pdf", "INV-P1", "GB29 NWBK 6016 1331 9268 19", "2026-01-01")
    new = invoice("new.pdf", "INV-P2", "GB94 BARC 1020 1530 0934 59", "2026-02-01")
    copy = invoice("new-copy.pdf", "INV-P2", "GB94 BARC 1020 1530 0934 59", "2026-02-01")
    _, found = links_and_conflicts(po, old, new, copy)
    assert [c.kind for c in found].count("PAYEE_ACCOUNT_CHANGED") == 1


def test_two_versions_of_one_invoice_disagree_on_the_amount() -> None:
    a = twin("a.pdf", {"invoice_number": "INV-5", "total_amount": "900.00", "currency": "USD"}, role="INVOICE")
    b = twin("b.pdf", {"invoice_number": "INV-5", "total_amount": "990.00", "currency": "USD"}, role="INVOICE")
    links, found = links_and_conflicts(a, b)
    assert relation(links, a, b) == "VERSION_OF"
    mismatch = next(c for c in found if c.kind == "AMOUNT_MISMATCH")
    assert mismatch.exposure_micros == 90_000_000


def test_a_claim_is_made_under_its_policy_and_a_waybill_is_declared_in_its_manifest() -> None:
    policy = twin("policy.pdf", {"policy_number": "POL-77", "insured_name": "Jane Roe", "sum_insured": "50000",
                                 "effective_date": "2026-01-01", "termination_date": "2026-12-31"},
                  label="Insurance Policy")
    claim = twin("claim.pdf", {"claim_number": "CLM-1", "policy_number": "POL-77", "insured_name": "Jane Roe",
                               "total_amount": "1200.00", "date": "2026-07-01"}, label="Insurance Claim")
    note = twin("note.pdf", {"record_number": "MRN-55", "claim_number": "CLM-1", "patient_name": "Jane Roe"},
                label="Clinical Note")
    manifest = twin("manifest.pdf", {"manifest_number": "MAN-9", "date": "2026-02-02"}, label="Customs Manifest")
    waybill = twin("awb.pdf", {"waybill_number": "AWB-1", "manifest_number": "MAN-9"}, label="Air Waybill")
    links = L.build_links([policy, claim, note, manifest, waybill])
    assert relation(links, claim, policy) == "CLAIMS_UNDER"
    assert relation(links, note, claim) == "SUPPORTS"
    assert relation(links, waybill, manifest) == "DECLARED_IN"


def test_spend_with_nothing_behind_it_is_flagged() -> None:
    lonely = twin("inv.pdf", {"invoice_number": "INV-6", "vendor_name": "Initech", "total_amount": "75.00",
                              "currency": "USD"}, role="INVOICE")
    _, found = links_and_conflicts(lonely)
    assert ("UNSUPPORTED_COMMITMENT", "MEDIUM") in kinds(found)


def test_a_common_party_alone_does_not_link_everything() -> None:
    # The tenant is on every document; a shared tenant name is weak evidence.
    docs = [twin(f"d{i}.pdf", {"customer_name": CARE, "vendor_name": f"Vendor {i}"}, role="OTHER") for i in range(8)]
    links = L.build_links(docs)
    assert links == []


def test_close_centroids_link_documents_that_share_nothing_else() -> None:
    a = twin("a.pdf", {}, label="Report", centroid=[1.0, 0.0, 0.0])
    b = twin("b.pdf", {}, label="Report", centroid=[0.99, 0.05, 0.0])
    links = L.build_links([a, b], semantic={(a.work_item_id, b.work_item_id): 0.99})
    assert len(links) == 1 and links[0].method == "SEMANTIC" and links[0].relation == "RELATES_TO"


def test_signals_combine_by_noisy_or() -> None:
    assert L.party_weight(1, 100) > L.party_weight(90, 100)
    assert L.semantic_weight(0.79) is None and 0.2 <= L.semantic_weight(0.9) <= 0.6


# --------------------------------------------------------------------------- ripple and risk


def test_a_delay_breaches_the_delivery_term_and_prices_liquidated_damages() -> None:
    m = msa()
    po = twin("po.pdf", {"po_number": "PO-8", "agreement_number": "MSA-2026-14", "vendor_name": ACME,
                         "customer_name": CARE, "total_amount": "10000.00", "currency": "USD", "date": "2026-05-01"},
              role="PURCHASE_ORDER")
    inv = twin("inv.pdf", {"invoice_number": "INV-8", "po_number": "PO-8", "vendor_name": ACME,
                           "total_amount": "10000.00", "currency": "USD", "date": "2026-05-20",
                           "due_date": "2026-06-19"}, role="INVOICE")
    links = L.build_links([m, po, inv])
    result = R.simulate(origin=po.work_item_id, scenario="DELAY", parameters={"days": 14},
                        twins={t.work_item_id: t for t in (m, po, inv)}, links=links)
    effects = [e for n in result["nodes"] for e in n["effects"]]
    breach = next(e for e in effects if "delivery term" in e["title"])
    assert breach["breach"] and "21 days" in breach["quote"]
    damages = next(e for e in effects if "Liquidated damages" in e["title"])
    assert damages["amount_micros"] == 200_000_000  # 1% x 2 weeks x 10,000
    assert result["summary"]["documents_affected"] == 2


def test_unknown_scenarios_and_origins_are_refused() -> None:
    m = msa()
    with pytest.raises(ValueError):
        R.simulate(origin=m.work_item_id, scenario="METEOR", parameters={}, twins={m.work_item_id: m}, links=[])
    with pytest.raises(ValueError):
        R.simulate(origin=uuid.uuid4(), scenario="DELAY", parameters={}, twins={m.work_item_id: m}, links=[])


def test_risk_rises_with_severity_and_the_index_weighs_by_size() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()

    class Row:
        def __init__(self, severity, ids, status="OPEN"):
            self.severity, self.work_item_ids, self.status = severity, ids, status

    risks = RK.node_risks([Row("CRITICAL", [a]), Row("CRITICAL", [a]), Row("LOW", [b]), Row("HIGH", [b], "DISMISSED")])
    assert risks[a] == 84.0 and risks[b] == 8.0
    assert RK.risk_index(risks, {a: 1_000_000_000_000, b: 1_000_000}) > RK.risk_index(risks, {a: 1_000_000, b: 1_000_000_000_000})
    assert RK.band(84) == "CRITICAL" and RK.band(8) == "LOW"
