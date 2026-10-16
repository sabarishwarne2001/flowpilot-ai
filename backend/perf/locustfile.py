"""The load test: several tenants and about fifty people working at once (campaign session 4, §B.2).

    python perf/load_seed.py --tenants 5 --users 10 --out /tmp/flowpilot-load-users.json
    pip install locust==2.32.4          # in its own virtualenv; not a dependency of the app
    LOAD_USERS=/tmp/flowpilot-load-users.json locust -f perf/locustfile.py --headless \
        --host http://127.0.0.1:8000 -u 50 -r 5 -t 15m --csv /tmp/flowpilot-load

Each simulated person takes one of the seeded sessions and refreshes it as the web app does (the
access token lasts ten minutes; the refresh token rotates on every use). Their work is a mix:

* browsing (most of it): their organizations, the document list, the dashboard, one document;
* uploading a one-page PDF, which the worker then processes;
* asking the assistant a question and reading the streamed answer to the end;
* an owner's or admin's administration: members, the audit log, usage and the subscription.

A health probe runs beside them: `/api/v1/health` must stay fast while the API is busy (a slow
answer there means the event loop is blocked). Read the results with `perf/load_report.py`; the
numbers of the last run are in docs/hardening/PERFORMANCE.md.
"""

from __future__ import annotations

import io
import itertools
import json
import os
import random
import threading
import uuid

from locust import HttpUser, between, events, task

API = "/api/v1"
REFRESH_COOKIE = "servabo_refresh"


def _pdf(label: str) -> bytes:
    """A small valid one-page PDF with a line of text (no extra dependency in the load runner)."""
    text = f"Load test invoice {label}".replace("(", "").replace(")", "")
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


class _Pool:
    """Hands each simulated person a different seeded session."""

    def __init__(self) -> None:
        path = os.environ.get("LOAD_USERS", "/tmp/flowpilot-load-users.json")
        with open(path) as handle:
            self._people = itertools.cycle(json.load(handle)["users"])
        self._lock = threading.Lock()

    def next(self) -> dict:
        with self._lock:
            return dict(next(self._people))


POOL: _Pool | None = None


@events.init.add_listener
def _load_pool(environment, **_kwargs) -> None:  # noqa: ANN001 - locust's hook
    global POOL
    POOL = _Pool()


class Person(HttpUser):
    wait_time = between(1, 5)

    def on_start(self) -> None:
        assert POOL is not None
        self.person = POOL.next()
        self.ws = f"{API}/workspaces/{self.person['workspace_id']}"
        self.org = f"{API}/organizations/{self.person['organization_id']}"
        # The refresh token rotates on every use and the server treats a reused one as stolen (it ends
        # the session family). It is carried here by hand, never in the client's cookie jar, where a
        # second copy of the cookie would send the old token next to the new one.
        self.refresh_token = self.person["refresh_token"]
        self.token = ""
        self.documents: list[str] = []
        self._refresh()

    def _refresh(self) -> None:
        self.client.cookies.clear()
        with self.client.post(f"{API}/auth/refresh", name="auth: refresh", catch_response=True,
                              headers={"Cookie": f"{REFRESH_COOKIE}={self.refresh_token}"}) as response:
            if response.status_code == 200:
                self.token = response.json()["access_token"]
                self.refresh_token = response.cookies.get(REFRESH_COOKIE) or self.refresh_token
                self.client.cookies.clear()
                response.success()
            else:
                response.failure(f"refresh {response.status_code}: {response.text[:200]}")

    def _call(self, method: str, path: str, name: str, **kwargs):
        # The access token runs out every ten minutes. The web app then refreshes it once and repeats
        # the request (frontend/src/services/api/client.ts); so does this. That first 401 is the
        # expected signal, not a failure, and is not counted as one; a 401 after the refresh is.
        with self.client.request(method, path, name=name, headers=self._auth(kwargs), catch_response=True,
                                 **kwargs) as response:
            if response.status_code != 401:
                return response
            response.success()
        self._refresh()
        return self.client.request(method, path, name=name, headers=self._auth(kwargs), **kwargs)

    def _auth(self, kwargs: dict) -> dict:
        return {**kwargs.pop("headers", {}), "Authorization": f"Bearer {self.token}"}

    # -- browsing --------------------------------------------------------------------------------

    @task(10)
    def browse(self) -> None:
        self._call("GET", f"{API}/me/context", "browse: my organizations")
        listed = self._call("GET", f"{self.ws}/work-items", "browse: document list", params={"limit": 25})
        if listed.status_code == 200:
            body = listed.json()
            items = body if isinstance(body, list) else body.get("items", [])
            self.documents = [item["id"] for item in items[:25]]
        self._call("GET", f"{self.ws}/dashboard/overview", "browse: dashboard")

    @task(4)
    def open_document(self) -> None:
        if self.documents:
            self._call("GET", f"{self.ws}/work-items/{random.choice(self.documents)}", "browse: one document")

    # -- uploading --------------------------------------------------------------------------------

    @task(2)
    def upload(self) -> None:
        if self.person["workspace_role"] == "VIEWER":
            return
        label = uuid.uuid4().hex[:8]
        self._call("POST", f"{self.ws}/work-items", "upload: one PDF",
                   files={"file": (f"load-{label}.pdf", _pdf(label), "application/pdf")})

    # -- the assistant ---------------------------------------------------------------------------

    @task(1)
    def ask_the_assistant(self) -> None:
        created = self._call("POST", f"{self.ws}/assistant/conversations", "assistant: new conversation",
                             json={"title": "Load test"})
        if created.status_code != 201:
            return
        stream = f"{self.ws}/assistant/conversations/{created.json()['id']}/messages/stream"
        question = {"content": "Which suppliers sent invoices this month, and what is the total?"}
        for attempt in (1, 2):  # as in _call: a 401 refreshes the access token once and asks again
            with self.client.post(stream, name="assistant: streamed answer", json=question, stream=True,
                                  catch_response=True,
                                  headers={"Authorization": f"Bearer {self.token}", "Accept": "text/event-stream"},
                                  ) as response:
                body = b"".join(response.iter_content(chunk_size=None)) if response.status_code == 200 else b""
                if response.status_code == 401 and attempt == 1:
                    response.success()
                elif response.status_code == 200 and b"done" in body:
                    response.success()
                elif response.status_code in (402, 429):  # a plan limit or the per-person rate limit: by design
                    response.success()
                else:
                    response.failure(f"stream {response.status_code}: {body[-200:]!r}")
            if response.status_code != 401:
                return
            self._refresh()

    # -- administration (owners and admins) ------------------------------------------------------

    @task(2)
    def administer(self) -> None:
        if self.person["role"] not in ("OWNER", "ADMIN"):
            return
        self._call("GET", f"{self.org}/members", "admin: members")
        self._call("GET", f"{self.org}/audit-logs", "admin: audit log", params={"limit": 50})
        self._call("GET", f"{self.org}/billing/subscription", "admin: subscription")
        self._call("GET", f"{self.ws}/usage/summary", "admin: usage")


class HealthProbe(HttpUser):
    """One probe beside the people: the API's health answer must stay fast under load."""

    fixed_count = 1
    wait_time = between(2, 2)

    @task
    def health(self) -> None:
        self.client.get(f"{API}/health", name="health")
