"""The Documents settings controls change what happens to an upload (campaign session 3, settings ledger).

`Settings > Documents` edits, per workspace, which file types may be uploaded, the largest upload,
and whether documents are classified automatically. Saving them was tested; what they do was not.
Each is proven here through the real upload API and pipeline.
"""

from __future__ import annotations

import io

from PIL import Image

from tests.engines.conftest import Engines, make_pdf

INVOICE = ["ACME SUPPLIES LTD", "TAX INVOICE", "Invoice Number: INV-CTL-1", "Total: 12.00 USD"]


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), (200, 30, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(engines: Engines, filename: str, data: bytes, mime: str):
    return engines.client.post(engines.url("/work-items"), files={"file": (filename, data, mime)},
                               headers=engines.tenant.owner.headers)


def test_only_the_allowed_file_types_are_accepted(engines: Engines) -> None:
    assert engines.put("/document-settings/", {"allowed_file_types": "pdf"}).status_code == 200
    refused = _upload(engines, "scan.png", _png(), "image/png")
    assert refused.status_code in (400, 415, 422), refused.text
    assert _upload(engines, "doc.pdf", make_pdf([["Hello"]]), "application/pdf").status_code == 201

    assert engines.put("/document-settings/", {"allowed_file_types": "pdf,png"}).status_code == 200
    assert _upload(engines, "scan2.png", _png(), "image/png").status_code == 201


def test_an_upload_over_the_workspace_limit_is_refused(engines: Engines) -> None:
    assert engines.put("/document-settings/", {"max_upload_size": 1}).status_code == 200
    big = make_pdf([["x" * 80] * 40]) + b"%" + b"0" * (1024 * 1024 + 10)
    refused = _upload(engines, "big.pdf", big, "application/pdf")
    assert refused.status_code == 413, refused.text
    assert _upload(engines, "small.pdf", make_pdf([["Hello"]]), "application/pdf").status_code == 201


def test_with_automatic_classification_off_a_document_is_not_classified(engines: Engines) -> None:
    assert engines.put("/document-settings/", {"automatic_classification": False}).status_code == 200
    work_item_id = engines.process("inv.pdf", [INVOICE], marker="INV-CTL-1", classification="Invoice",
                                   entities={"invoice_number": "INV-CTL-1"})
    details = (engines.item(work_item_id).extracted_entities or {}).get("classification_details") or {}
    assert details.get("document_classification") == "Other", details

    assert engines.put("/document-settings/", {"automatic_classification": True}).status_code == 200
    again = engines.process("inv2.pdf", [INVOICE + ["copy"]], marker="INV-CTL-1", classification="Invoice",
                            entities={"invoice_number": "INV-CTL-1"})
    details = (engines.item(again).extracted_entities or {}).get("classification_details") or {}
    assert details.get("document_classification") == "Invoice", details
