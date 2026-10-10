"""F-220 — an empty or oversized file on a public document-request link answered 500.

    pytest tests/engines/test_public_document_request_refusals.py -q

The recipient of a missing-document request uploads through a public link. The
route spooled the upload before its `try`, so the two refusals the spooler
raises (the file is empty, the file is over the size limit) escaped as an
unhandled FileValidationError: a 500 to someone with no account and no way to
know what went wrong. They are refusals like any other: 422 for an empty file,
413 for one over the limit, and the link stays open for a corrected upload.
"""

from __future__ import annotations

from app.core.config import settings
from tests.engines.conftest import Engines

REQUIRED = [{"doc_type": "invoice", "label": "Invoice"}]


def _request_token(engines: Engines) -> str:
    template = engines.post("/case-templates", {"key": "intake", "name": "Intake", "required_documents": REQUIRED,
                                                 "rules": []})
    assert template.status_code == 201, template.text
    published = engines.post(f"/case-templates/{template.json()['id']}/publish")
    assert published.status_code == 200, published.text
    case = engines.post("/cases", {"template_id": template.json()["id"], "title": "Supplier onboarding"})
    assert case.status_code == 201, case.text
    created = engines.post(f"/cases/{case.json()['case']['id']}/requests",
                           {"document_type": "invoice", "recipient_label": "Supplier"})
    assert created.status_code == 201, created.text
    return created.json()["token"]


def _upload(engines: Engines, token: str, data: bytes):
    return engines.client.post(f"/api/v1/public/document-requests/{token}",
                               files={"file": ("invoice.pdf", data, "application/pdf")})


def test_an_empty_file_is_refused_and_the_link_stays_open(engines: Engines) -> None:
    token = _request_token(engines)
    response = _upload(engines, token, b"")
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "FILE_REJECTED"
    assert engines.client.get(f"/api/v1/public/document-requests/{token}").status_code == 200


def test_a_file_over_the_limit_is_refused_and_the_link_stays_open(engines: Engines, monkeypatch) -> None:
    token = _request_token(engines)
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE", 1024)
    response = _upload(engines, token, b"%PDF-1.4\n" + b"0" * 4096)
    assert response.status_code == 413, response.text
    assert response.json()["detail"]["code"] == "FILE_TOO_LARGE"
    assert engines.client.get(f"/api/v1/public/document-requests/{token}").status_code == 200
