"""ERP delivery over HTTPS, retry and accept, live against the mock ERP server (campaign session 4).

    pytest tests/engines/test_erp_delivery_through_the_mock_target_live.py -q

Session 2 proved file targets (download, SFTP formats) and left `erp.deliver_posting` over HTTP
blocked for want of a real ERP. `scripts/erp_mock_targets.py` is that stand-in: a generic REST
endpoint over TLS that can refuse a request or acknowledge a different document than it was sent.
On an approved three-way match:

* an administrator sets the target's bearer credential (a viewer cannot, and the secret is never
  read back, only its fingerprint);
* a vendor bill the target refuses (HTTP 400) is REJECTED with the target's answer; after the
  cause is fixed, Retry sends it again and it is posted;
* a bill the target acknowledges under another number is a MISMATCH; a person who checked the
  books accepts it, and it is posted with the target's reference.

The mock listens on 127.0.0.1, which the production client refuses (proved in
`test_tenant_destinations_cannot_reach_the_platform_live.py`); this test swaps in the SSRF-safe
client with private ranges allowed and the mock's certificate trusted, as the ARCH-47 gates did.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.engines.conftest import Engines, drain
from tests.engines.test_erp_posting_live import _post, approved_case  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from erp_mock_targets import MockErp  # noqa: E402


@pytest.fixture()
def mock_erp(monkeypatch: pytest.MonkeyPatch):
    from app.core.ssrf_client import SSRFSafeHTTPClient
    from app.services.erp import service as erp_service
    from app.services.erp.transport import http as erp_http

    mock = MockErp().start()
    monkeypatch.setattr(erp_service, "_preflight_url", lambda url, what: None)
    monkeypatch.setattr(erp_http, "client_factory", lambda timeout: SSRFSafeHTTPClient(
        connect_timeout=min(10.0, timeout), total_timeout=timeout, allow_private_ranges=True,
        test_ssl_context=mock.client_ssl_context()))
    try:
        yield mock
    finally:
        mock.stop()


def _state(engines: Engines, posting_id: str) -> dict:
    return engines.get(f"/erp/postings/{posting_id}").json()["posting"]


def test_a_refused_bill_is_retried_and_a_mismatch_is_accepted(engines: Engines, approved_case, mock_erp) -> None:  # noqa: F811
    created = engines.post("/erp/targets", {
        "name": "Books over REST", "format": "JSON", "transport": "HTTP", "preset": "GENERIC_REST",
        "auth_mode": "BEARER", "ack_mode": "SYNC",
        "config": {"http": {"base_url": mock_erp.base},
                   # A generic target compares the fields its owner lists under rest.echo.
                   "rest": {"paths": {"VENDOR_BILL": "/postings/bills", "JOURNAL_ENTRY": "/postings/journals"},
                            "echo": {"documentNumber": "/documentNumber"}}},
    })
    assert created.status_code == 201, created.text
    target_id = created.json()["target"]["id"]

    assert engines.put(f"/erp/targets/{target_id}/credential", {"credential": {"token": "static-token"}},
                       as_user=engines.tenant.contributor).status_code == 403
    credential = engines.put(f"/erp/targets/{target_id}/credential", {"credential": {"token": "static-token"}})
    assert credential.status_code == 200, credential.text
    assert "static-token" not in credential.text and credential.json().get("credential_fingerprint"), \
        credential.json()
    assert engines.post(f"/erp/targets/{target_id}/test").status_code == 200

    # The target refuses the first bill.
    mock_erp.fault("POST /postings/", "status_400")
    bill = _post(engines, target_id, approved_case["id"], ["VENDOR_BILL"])
    assert bill.status_code == 201, bill.text
    bill_id = bill.json()["results"][0]["posting_id"]
    drain(only=["erp.deliver_posting"])
    refused = _state(engines, bill_id)
    assert refused["state"] in ("REJECTED", "FAILED"), refused

    retried = engines.post(f"/erp/postings/{bill_id}/retry", {"note": "Supplier code fixed in the books"})
    assert retried.status_code == 200, retried.text
    drain(only=["erp.deliver_posting"])
    posted = _state(engines, bill_id)
    assert posted["state"] == "DONE", posted
    assert mock_erp.count("POST", "/postings/") == 2, mock_erp.requests

    # The target acknowledges another document number than the one sent: a person decides.
    mock_erp.fault("POST /postings/", "mismatch")
    second = _post(engines, target_id, approved_case["id"], ["JOURNAL_ENTRY"])
    assert second.status_code == 201, second.text
    second_id = second.json()["results"][0]["posting_id"]
    drain(only=["erp.deliver_posting"])
    assert _state(engines, second_id)["state"] == "MISMATCH", _state(engines, second_id)
    assert engines.post(f"/erp/postings/{second_id}/accept", {"note": "x"},
                        as_user=engines.tenant.contributor).status_code == 403
    accepted = engines.post(f"/erp/postings/{second_id}/accept", {"note": "The books show it under -X; correct"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["posting"]["state"] == "DONE", accepted.json()
