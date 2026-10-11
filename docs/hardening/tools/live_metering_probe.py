"""Live probe: every model call is metered exactly once (campaign session 2, brief E.3).

For each scenario the model stand-in counts the completions it actually served (GET /__stats)
and the probe counts the `llm.input_token` / `llm.output_token` usage rows written meanwhile.
Each served completion must produce exactly one input row and one output row: no call free,
none charged twice. Scenarios: a plain assistant answer, a streamed one, a stream the client
cancels after the first bytes, a provider error that the platform retries (POST /__fail), the
same for a stream, and a document's enrichment (classification, extraction, summary,
verification agents) on the worker.

    backend/.venv/bin/python docs/hardening/tools/live_metering_probe.py

Needs the live stack with E2E_LLM=1 (llm-mock.mjs with /__stats) and the e2e seed. Tenant C.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import time
import uuid

import httpx

BASE = os.environ.get("PROBE_API_ORIGIN", "http://127.0.0.1:8000") + "/api/v1"
LLM = os.environ.get("PROBE_LLM_ORIGIN", "http://127.0.0.1:11434")
PW = os.environ.get("E2E_PASSWORD", "E2e-FlowPilot-Pass-2026!")
PSQL = ["psql", "-h", "localhost", "-p", os.environ.get("PROBE_PG_PORT", "5433"), "-U", "postgres",
        "-d", os.environ.get("PROBE_PG_DB", "flowpilot_e2e"), "-tAc"]
RESULTS: list[tuple[str, int, int, int, bool]] = []


def sql(query: str) -> str:
    return subprocess.run([*PSQL, query], capture_output=True, text=True, check=True).stdout.strip()


def served() -> int:
    return int(httpx.get(f"{LLM}/__stats", timeout=10).json()["completions"])


def rows(kind: str, since: str) -> int:
    return int(sql(f"select count(*) from usage_events where event_type = 'llm.{kind}_token' "
                   f"and created_at >= '{since}'"))


def quiet(timeout: float = 240) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sql("select count(*) from jobs where status in ('PENDING','CLAIMED') and available_at <= now()") == "0":
            time.sleep(2)
            if sql("select count(*) from jobs where status in ('PENDING','CLAIMED') and available_at <= now()") == "0":
                return
        time.sleep(2)


def measure(name: str, action) -> None:
    quiet()
    since = sql("select now()")
    before = served()
    action()
    time.sleep(4)
    quiet()
    calls = served() - before
    inputs, outputs = rows("input", since), rows("output", since)
    ok = calls == inputs == outputs and calls > 0
    RESULTS.append((name, calls, inputs, outputs, ok))
    print(f"{'ok  ' if ok else 'FAIL'} {name}: served {calls}, input rows {inputs}, output rows {outputs}")


def main() -> int:
    token = httpx.post(f"{BASE}/auth/login", data={"username": "c-owner@e2e.example.com", "password": PW},
                       timeout=60).json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    ws = next(r for r in httpx.get(f"{BASE}/me/workspaces", headers=h, timeout=60).json()
              if r["organization_slug"] == "caretakers-global" and r["slug"] == "operations")["id"]

    def conversation() -> str:
        return httpx.post(f"{BASE}/workspaces/{ws}/assistant/conversations", json={"title": "metering"},
                          headers=h, timeout=60).json()["id"]

    def plain() -> None:
        r = httpx.post(f"{BASE}/workspaces/{ws}/assistant/conversations/{conversation()}/messages",
                       json={"content": "What is the total amount of the invoice?"}, headers=h, timeout=180)
        assert r.status_code < 300, r.text

    def streamed() -> None:
        with httpx.stream("POST", f"{BASE}/workspaces/{ws}/assistant/conversations/{conversation()}/messages/stream",
                          json={"content": "What is the total amount of the invoice?"}, headers=h, timeout=180) as s:
            for _ in s.iter_text():
                pass

    def cancelled() -> None:
        with httpx.stream("POST", f"{BASE}/workspaces/{ws}/assistant/conversations/{conversation()}/messages/stream",
                          json={"content": "Summarise every invoice in detail, line by line."}, headers=h,
                          timeout=180) as s:
            for chunk in s.iter_text():
                if "delta" in chunk or "token" in chunk:
                    break  # the client goes away mid-answer

    def retried(stream: bool):
        def run() -> None:
            httpx.post(f"{LLM}/__fail", params={"count": 1, "status": 503}, timeout=10)
            (streamed if stream else plain)()
        return run

    def document() -> None:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        page = canvas.Canvas(buffer, pagesize=A4)
        y = A4[1] - 60
        run = uuid.uuid4().hex[:6]
        for line in ("TAX INVOICE", f"Invoice Number: MET-{run}", "Vendor: Meter Supplies Ltd",
                     "Invoice Date: 2026-10-01", "Total: 1,234.50 USD"):
            page.drawString(50, y, line)
            y -= 18
        page.showPage()
        page.save()
        r = httpx.post(f"{BASE}/workspaces/{ws}/work-items", headers=h, timeout=120,
                       files={"file": (f"meter-{run}.pdf", buffer.getvalue(), "application/pdf")})
        assert r.status_code == 201, r.text
        time.sleep(3)

    measure("assistant answer", plain)
    measure("assistant stream", streamed)
    measure("assistant stream cancelled by the client", cancelled)
    measure("assistant answer after a provider 503", retried(False))
    measure("assistant stream after a provider 503", retried(True))
    measure("document enrichment on the worker", document)
    failed = [r for r in RESULTS if not r[4]]
    print(f"{len(RESULTS) - len(failed)} of {len(RESULTS)} scenarios metered exactly once")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
