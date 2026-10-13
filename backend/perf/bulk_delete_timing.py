"""Time one 500-document bulk delete against a running FlowPilot (campaign session 4, PERFORMANCE.md).

    python backend/perf/bulk_delete_timing.py --api http://127.0.0.1:8000/api/v1 \
        --email c-owner@e2e.example.com --password '...' --org caretakers-global [--count 500]

Uploads COUNT one-page PDFs through the real upload API, waits for processing to settle, deletes
them all in one `work-items/bulk` request and prints how long that request took. For a test or
staging deployment only: it creates and deletes real documents in the account it signs in as.
"""

from __future__ import annotations

import argparse
import io
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from reportlab.pdfgen import canvas


def _pdf(n: int) -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer)
    page.drawString(50, 800, f"Bulk timing document {n} {uuid.uuid4()}")
    page.showPage()
    page.save()
    return buffer.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--org", required=True, help="organization slug")
    parser.add_argument("--count", type=int, default=500)
    args = parser.parse_args()

    client = httpx.Client(timeout=120)
    token = client.post(f"{args.api}/auth/login",
                        data={"username": args.email, "password": args.password}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    context = client.get(f"{args.api}/me/context", headers=headers).json()
    organization = next(o for o in context["organizations"] if o["organization_slug"] == args.org)
    workspace = organization["workspaces"][0]["id"]

    def upload(n: int) -> str:
        response = client.post(f"{args.api}/workspaces/{workspace}/work-items", headers=headers,
                               files={"file": (f"bulk-{n}.pdf", _pdf(n), "application/pdf")})
        response.raise_for_status()
        return response.json()["id"]

    started = time.monotonic()
    with ThreadPoolExecutor(8) as pool:
        ids = list(pool.map(upload, range(args.count)))
    print(f"uploaded {len(ids)} in {time.monotonic() - started:.1f}s")
    deadline = time.monotonic() + 900
    pending = -1
    while time.monotonic() < deadline:
        listed = client.get(f"{args.api}/workspaces/{workspace}/work-items", headers=headers,
                            params={"limit": 1, "status": "PROCESSING"})
        pending = listed.json().get("total", 0) if listed.status_code == 200 else -1
        if pending == 0:
            break
        time.sleep(5)
    print(f"processing settled after {time.monotonic() - started:.1f}s (still processing: {pending})")

    began = time.monotonic()
    response = client.post(f"{args.api}/workspaces/{workspace}/work-items/bulk", headers=headers,
                           json={"action": "delete", "ids": ids, "idempotency_key": str(uuid.uuid4())})
    elapsed = time.monotonic() - began
    body = response.json()
    print(f"bulk delete of {len(ids)}: HTTP {response.status_code} in {elapsed:.2f}s; "
          f"succeeded={body.get('succeeded')} refused={body.get('refused')}")


if __name__ == "__main__":
    main()
