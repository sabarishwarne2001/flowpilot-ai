"""Every document pack, live: enabling it changes what the model is asked for a new document of
its type, disabling it stops that, documents already read are left alone, and nothing crosses
into another workspace or organization.

    pytest tests/engines/test_document_packs_live.py -q

Each document below is what that type looks like on paper (the words a real one carries), run
through the real pipeline (upload -> extract -> enrich) with the model recorded as in
tests/engines/conftest.py. An enabled pack reaches the extraction prompt when the document's
text carries its classifier hints (app/services/entities/presets.py), so these documents are
the proof that each pack's hints fit the documents it is for.
"""

from __future__ import annotations

import uuid
from typing import Optional

import pytest

from tests.engines.conftest import Engines, drain, make_pdf
from tests.engines.test_batch_ingestion_live import platform_presets  # noqa: F401  (fixture)

#: (pack document_type, variant) -> the lines of a realistic document of that type.
DOCUMENTS: dict[tuple[str, str], list[str]] = {
    ("resume", "cv"): [
        "Curriculum Vitae - Priya Raman",
        "priya.raman@example.com  +91 98765 43210",
        "Work experience: Senior Analyst, Northwind Traders (2019 - 2026)",
        "Education: B.Com, University of Madras",
    ],
    ("offer_letter", "letter"): [
        "Northwind Traders Ltd - Offer of Employment",
        "Dear Arjun Mehta,",
        "We are pleased to offer you the position of Data Analyst.",
        "Start date: 1 December 2026. Annual compensation: INR 1,800,000.",
    ],
    ("intake_form", "clinic"): [
        "Riverside Clinic - Patient Intake Form",
        "Patient name: Maria Lopez   Date of birth: 04/12/1985",
        "Medical record number: 00482913",
        "Presenting complaint: persistent dry cough for three weeks",
    ],
    ("discharge_summary", "hospital"): [
        "St. Mary's Hospital - Discharge Summary",
        "Patient: Maria Lopez",
        "Date of admission: 02/10/2026   Date of discharge: 06/10/2026",
        "Primary diagnosis: community-acquired pneumonia",
        "Follow-up: GP review in two weeks",
    ],
    ("nda", "mutual"): [
        "Mutual Non-Disclosure Agreement",
        "Between Northwind Traders Ltd (Disclosing Party) and Contoso LLC (Receiving Party).",
        "Confidential Information means all non-public business information.",
        "Effective date: 1 October 2026. Term: 36 months. Governing law: England and Wales.",
    ],
    ("msa", "services"): [
        "Master Services Agreement",
        "Customer: Northwind Traders Ltd   Supplier: Contoso Consulting LLC",
        "Each Statement of Work issued under this agreement forms part of it.",
        "Payment terms: 45 days. Limitation of Liability: USD 500,000.",
    ],
    ("lease", "residential"): [
        "Residential Lease Agreement",
        "Landlord: Harbour Properties Ltd   Tenant: Arjun Mehta",
        "Premises: Flat 4B, 12 Marine Drive, Mumbai",
        "Monthly rent: INR 45,000. Term: 11 months.",
    ],
    ("bill_of_lading", "ocean"): [
        "Bill of Lading No. MAEU123456789",
        "Shipper: Northwind Traders Ltd",
        "Consignee: Contoso Imports GmbH",
        "Port of loading: Nhava Sheva   Port of discharge: Hamburg",
        "Container: MSKU1234565",
    ],
    ("customs_manifest", "export"): [
        "Customs Cargo Manifest",
        "Declaration number: CD-2026-004417",
        "HS code 8471.30 - portable computers",
        "Country of origin: India   Declared value: USD 48,200",
    ],
    ("passport", "identity_page"): [
        "REPUBLIC OF INDIA   PASSPORT",
        "Surname: RAMAN   Given names: PRIYA",
        "Passport No.: Z1234567   Nationality: INDIAN",
        "Date of expiry: 14/03/2033",
        "P<INDRAMAN<<PRIYA<<<<<<<<<<<<<<<<<<<<<<<<<<<<",
    ],
    ("india_id_card", "aadhaar"): [
        "Unique Identification Authority of India",
        "Government of India",
        "Priya Raman   DOB: 14/03/1990   Female",
        "XXXX XXXX 1234",
        "Aadhaar - Aam Aadmi ka Adhikar",
    ],
}


def _pack(engines: Engines, document_type: str, *, workspace_id: Optional[uuid.UUID] = None, as_user=None) -> dict:
    persona = as_user or engines.tenant.owner
    url = f"/api/v1/workspaces/{workspace_id or engines.ws}/document-presets"
    listed = engines.client.get(url, headers=persona.headers)
    assert listed.status_code == 200, listed.text
    return next(p for p in listed.json() if p["document_type"] == document_type)


def _switch(engines: Engines, pack: dict, enabled: bool, *, workspace_id: Optional[uuid.UUID] = None,
            as_user=None) -> None:
    persona = as_user or engines.tenant.owner
    base = f"/api/v1/workspaces/{workspace_id or engines.ws}/document-presets"
    if not pack["applied"]:
        applied = engines.client.post(f"{base}/apply", json={"preset_id": pack["id"]}, headers=persona.headers)
        assert applied.status_code == 200, applied.text
        assert applied.json()["enabled"] is False, "applying a pack enabled it"
    toggled = engines.client.post(f"{base}/{pack['id']}/enabled", json={"enabled": enabled}, headers=persona.headers)
    assert toggled.status_code == 200, toggled.text
    assert toggled.json()["enabled"] is enabled


def _read(engines: Engines, lines: list[str], marker: str, *, workspace_id: Optional[uuid.UUID] = None,
          as_user=None) -> tuple[uuid.UUID, Optional[str]]:
    """Upload a document carrying `marker`, run the pipeline, return (id, the preset block the model got)."""
    persona = as_user or engines.tenant.owner
    engines.llm.record(marker, "Other", {"reference": marker})
    pages = [[*lines, f"Ref: {marker}"]]
    response = engines.client.post(
        f"/api/v1/workspaces/{workspace_id or engines.ws}/work-items",
        files={"file": (f"{marker}.pdf", make_pdf(pages), "application/pdf")},
        headers=persona.headers,
    )
    assert response.status_code == 201, response.text
    before = len(engines.llm.preset_contexts)
    drain()
    assert len(engines.llm.preset_contexts) == before + 1, "the document's fields were never extracted"
    return uuid.UUID(response.json()["id"]), engines.llm.preset_contexts[-1]


@pytest.mark.usefixtures("platform_presets")
@pytest.mark.parametrize(("document_type", "variant"), sorted(DOCUMENTS), ids=[f"{t}-{v}" for t, v in sorted(DOCUMENTS)])
def test_enabling_a_pack_asks_the_model_for_its_fields_and_disabling_stops_it(
        engines: Engines, document_type: str, variant: str) -> None:
    lines = DOCUMENTS[(document_type, variant)]
    pack = _pack(engines, document_type)
    fields = list(pack["schema_fields"]["properties"])

    _, before = _read(engines, lines, f"{variant.upper()}-OFF-0")
    assert before is None, "a pack nobody enabled reached the model"

    _switch(engines, pack, True)
    _, context = _read(engines, lines, f"{variant.upper()}-ON-1")
    assert context is not None, f"the enabled {pack['label']} pack never reached a {variant} document"
    assert pack["label"] in context, context
    for field in fields:
        assert f"- {field} (" in context, (field, context)

    _switch(engines, _pack(engines, document_type), False)
    _, after = _read(engines, lines, f"{variant.upper()}-OFF-2")
    assert after is None, "a disabled pack still reached the model"


@pytest.mark.usefixtures("platform_presets")
def test_enabling_a_pack_leaves_documents_already_read_untouched(engines: Engines) -> None:
    lines = DOCUMENTS[("bill_of_lading", "ocean")]
    earlier, context = _read(engines, lines, "BOL-EARLIER-1")
    assert context is None
    item = engines.item(earlier)
    entities, summary = dict(item.extracted_entities or {}), item.summary
    calls = len(engines.llm.calls)

    _switch(engines, _pack(engines, "bill_of_lading"), True)
    drain()
    assert len(engines.llm.calls) == calls, "enabling a pack re-ran the model on stored documents"
    item = engines.item(earlier)
    assert dict(item.extracted_entities or {}) == entities and item.summary == summary

    _, later = _read(engines, lines, "BOL-LATER-1")
    assert later and "bl_number" in later
    assert dict(engines.item(earlier).extracted_entities or {}) == entities


@pytest.mark.usefixtures("platform_presets")
def test_a_pack_enabled_in_one_workspace_changes_nothing_in_another(engines: Engines) -> None:
    lines = DOCUMENTS[("resume", "cv")]
    _switch(engines, _pack(engines, "resume"), True)
    _, in_a = _read(engines, lines, "CV-WS-A")
    assert in_a and "candidate_name" in in_a

    created = engines.post("/workspaces", {"workspace_name": f"Packs B {uuid.uuid4().hex[:6]}"}, org=True)
    assert created.status_code == 201, created.text
    b = uuid.UUID(created.json()["id"])
    assert _pack(engines, "resume", workspace_id=b)["applied"] is False
    _, in_b = _read(engines, lines, "CV-WS-B", workspace_id=b)
    assert in_b is None, "workspace A's pack reached a document in workspace B of the same organization"

    # Workspace B enabling a different pack does not change what A asks for.
    _switch(engines, _pack(engines, "passport", workspace_id=b), True, workspace_id=b)
    _, passport_in_a = _read(engines, DOCUMENTS[("passport", "identity_page")], "PP-WS-A")
    assert passport_in_a is None, "workspace B's pack reached a document in workspace A"

    other = engines.tenant.other_org_member
    from tests.security.plans import put_on_plan

    put_on_plan(engines.db, engines.tenant.foreign_workspace.organization, "enterprise")
    engines.db.commit()
    foreign = engines.tenant.foreign_workspace.id
    assert _pack(engines, "resume", workspace_id=foreign, as_user=other)["applied"] is False
    _, in_other_org = _read(engines, lines, "CV-ORG-B", workspace_id=foreign, as_user=other)
    assert in_other_org is None, "one organization's pack reached another organization's document"
