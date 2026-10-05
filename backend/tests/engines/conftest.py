"""Phase 4.0 — a live harness for the commercial engines.

    pytest tests/engines -q

Each test drives a real document through the real pipeline:

    upload (HTTP) -> document.extract (the PDF's own text layer; OCR is the
    ML_STUBS stub for scanned pages) -> document.enrich -> post-enrichment
    fan-out -> every engine's job handler -> the engine's HTTP API

Three things are substituted, and only these three:

* `SessionLocal` points at the test database, so the job handlers (which open
  their own sessions, as they do in the worker) see what the test wrote.
* The language model. There is no LLM key in CI, so `RecordedLLM` answers the
  three enrichment calls with a RECORDED response a test registers against a
  marker string in the document (the shape a real model returns for the
  platform's extraction prompt). Everything downstream of the model — field
  reading, matching, posting, graphing, scheduling — runs for real. This is
  the "recorded model response fixture" F-063 asked for.
* Jobs run in-process through `drain()`, which mirrors `worker.run_jobs_loop`
  (claim, run the registered handler under the job's egress attribution, mark
  succeeded or failed) instead of waiting on a separate worker process.

`drain()` fails the test on any job that raised, unless the test says it
expects failures: a silent failure in a background job is exactly the kind of
defect these tests exist to find.
"""

from __future__ import annotations

import io
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Sequence

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

import app.db.session as session_module
from app.models.job import Job, JobStatus
from app.models.work_item import WorkItem
from tests.conftest import Fixture, TestSessionLocal
from tests.security.plans import put_on_plan

API = "/api/v1"


# ---------------------------------------------------------------------------
# SessionLocal -> the test database
# ---------------------------------------------------------------------------


@pytest.fixture()
def test_sessions(monkeypatch: pytest.MonkeyPatch, db_session: Session):
    """Every `SessionLocal` the app holds now opens a test-database session."""
    original = session_module.SessionLocal
    for module in list(sys.modules.values()):
        if module is None or not getattr(module, "__name__", "").startswith("app"):
            continue
        if getattr(module, "SessionLocal", None) is original:
            monkeypatch.setattr(module, "SessionLocal", TestSessionLocal, raising=False)
    monkeypatch.setattr(session_module, "SessionLocal", TestSessionLocal)
    yield TestSessionLocal


# ---------------------------------------------------------------------------
# The recorded language model
# ---------------------------------------------------------------------------


@dataclass
class Recording:
    classification: str
    entities: dict[str, Any]
    summary: str = "Recorded summary."
    #: What each verification agent answers, in turn; None = every agent repeats `entities`.
    agents: Optional[list[dict[str, Any]]] = None


@dataclass
class RecordedLLM:
    """Answers enrichment calls from recordings keyed by a marker in the text."""

    recordings: dict[str, Recording] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    memory_contexts: list[Optional[str]] = field(default_factory=list)

    agent_turns: dict[str, int] = field(default_factory=dict)
    agent_prompts: list[str] = field(default_factory=list)

    def record(self, marker: str, classification: str, entities: dict[str, Any], summary: str = "Recorded summary.",
               agents: Optional[list[dict[str, Any]]] = None) -> None:
        self.recordings[marker] = Recording(classification, entities, summary, agents)

    def _find(self, text: str) -> Optional[Recording]:
        # Newest first: a document often quotes another's marker (an invoice
        # names its PO number), and `process` records just before uploading.
        for marker, recording in reversed(list(self.recordings.items())):
            if marker in (text or ""):
                return recording
        return None

    def classify_document(self, text: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("classify", text[:40]))
        found = self._find(text)
        return {"document_classification": found.classification if found else "Other"}

    def extract_entities(self, text: str, document_class: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("entities", text[:40]))
        self.memory_contexts.append(kwargs.get("memory_context"))
        found = self._find(text)
        return dict(found.entities) if found else {}

    def generate_summary(self, text: str, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("summary", text[:40]))
        found = self._find(text)
        return found.summary if found else "A document."

    def execute_prompt(self, *, prompt: str, temperature: float = 0.0, ai_settings: Any = None):
        """A verification agent's answer: the prompt carries the document text."""
        import json

        from app.schemas.assistant import TokenUsage

        self.calls.append(("agent", prompt[-40:]))
        self.agent_prompts.append(prompt)
        found = None
        marker_hit = None
        for marker, recording in reversed(list(self.recordings.items())):
            if marker in prompt:
                found, marker_hit = recording, marker
                break
        if found is None:
            answer: dict[str, Any] = {}
        elif found.agents:
            turn = self.agent_turns.get(marker_hit, 0)
            self.agent_turns[marker_hit] = turn + 1
            answer = found.agents[turn % len(found.agents)]
        else:
            answer = found.entities
        usage = TokenUsage(provider="groq", model="recorded", prompt_tokens=100, completion_tokens=50,
                           total_tokens=150, estimated_cost=0.0)
        return json.dumps(answer), usage


@pytest.fixture()
def recorded_llm(monkeypatch: pytest.MonkeyPatch) -> RecordedLLM:
    from app.services.llm_service import llm_service

    fake = RecordedLLM()
    monkeypatch.setattr(llm_service, "classify_document", fake.classify_document)
    monkeypatch.setattr(llm_service, "extract_entities", fake.extract_entities)
    monkeypatch.setattr(llm_service, "generate_summary", fake.generate_summary)
    monkeypatch.setattr(llm_service, "execute_prompt", fake.execute_prompt)
    return fake


# ---------------------------------------------------------------------------
# Running jobs the way the worker does
# ---------------------------------------------------------------------------


@dataclass
class JobRun:
    job_type: str
    payload: dict[str, Any]
    ok: bool
    result: Any = None
    error: Optional[str] = None


def drain(
    *,
    include_delayed: bool = True,
    allow_failures: bool = False,
    only: Optional[Iterable[str]] = None,
    max_rounds: int = 12,
) -> list[JobRun]:
    """Run claimable jobs until none are left (or `max_rounds` passes)."""
    from app.core import egress
    from app.core.principal import system_principal
    from app.core.request_context import job_scope
    from app.services.job_service import JOB_HANDLERS, trace_context_from
    from app.workers.claim import claim_jobs, mark_job_failed, mark_job_succeeded
    from app.workers.handlers import register_all

    register_all(replace=True)
    types = list(only) if only else None
    runs: list[JobRun] = []
    for _ in range(max_rounds):
        with TestSessionLocal() as db:
            if include_delayed:
                pending = update(Job).where(Job.status == JobStatus.PENDING)
                if types:
                    pending = pending.where(Job.job_type.in_(types))
                db.execute(pending.values(available_at=datetime.now(timezone.utc)))
            claimed = claim_jobs(db, worker_id="engine-tests", batch_size=100, lease_seconds=300, job_types=types)
            snapshot = [(j.id, j.job_type, dict(j.payload or {}), j.attempts, j.organization_id) for j in claimed]
            db.commit()
        if not snapshot:
            break
        for job_id, job_type, payload, attempts, org_id in snapshot:
            handler = JOB_HANDLERS.get(job_type)
            assert handler is not None, f"no handler for {job_type}"
            body = dict(payload)
            body["job_id"] = str(job_id)
            try:
                with job_scope(job_id=job_id, job_type=job_type, context=trace_context_from(payload)), \
                        system_principal(job_name=f"jobs.{job_type}", job_id=job_id), egress.attributed(org_id):
                    result = handler(body)
            except Exception as exc:  # noqa: BLE001
                with TestSessionLocal() as db:
                    # attempts=max so a failed job is not re-claimed in the next round.
                    mark_job_failed(db, job_id, attempts=attempts, error=f"{type(exc).__name__}: {exc}")
                    db.execute(update(Job).where(Job.id == job_id).values(status=JobStatus.DEAD))
                    db.commit()
                runs.append(JobRun(job_type, payload, False, error=f"{type(exc).__name__}: {exc}"))
                continue
            with TestSessionLocal() as db:
                mark_job_succeeded(db, job_id, result=result)
                db.commit()
            runs.append(JobRun(job_type, payload, True, result=result))
    failures = [r for r in runs if not r.ok]
    if failures and not allow_failures:
        raise AssertionError("background job(s) failed:\n" + "\n".join(f"  {r.job_type}: {r.error}" for r in failures))
    return runs


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


def make_pdf(pages: Sequence[Sequence[str]], *, font_size: int = 11) -> bytes:
    """A real, text-layer PDF: one list of lines per page."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    for lines in pages:
        pdf.setFont("Helvetica", font_size)
        y = height - 60
        for line in lines:
            pdf.drawString(50, y, line)
            y -= font_size + 6
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@dataclass
class Engines:
    client: TestClient
    db: Session
    tenant: Fixture
    llm: RecordedLLM

    @property
    def ws(self) -> uuid.UUID:
        return self.tenant.workspace.id

    @property
    def org(self) -> uuid.UUID:
        return self.tenant.organization.id

    def url(self, path: str) -> str:
        return f"{API}/workspaces/{self.ws}{path}"

    def org_url(self, path: str) -> str:
        return f"{API}/organizations/{self.org}{path}"

    def plan(self, key: str) -> None:
        put_on_plan(self.db, self.tenant.organization, key)

    def upload(self, filename: str, data: bytes, *, mime: str = "application/pdf", as_user=None) -> uuid.UUID:
        persona = as_user or self.tenant.owner
        response = self.client.post(
            self.url("/work-items"),
            files={"file": (filename, data, mime)},
            headers=persona.headers,
        )
        assert response.status_code == 201, response.text
        return uuid.UUID(response.json()["id"])

    def process(self, filename: str, pages: Sequence[Sequence[str]], *, marker: Optional[str] = None,
                classification: Optional[str] = None, entities: Optional[dict[str, Any]] = None,
                agents: Optional[list[dict[str, Any]]] = None,
                drain_jobs: bool = True, allow_failures: bool = False) -> uuid.UUID:
        """Upload a PDF whose model reading is recorded, and run the pipeline."""
        if marker is not None:
            self.llm.record(marker, classification or "Other", entities or {}, agents=agents)
        work_item_id = self.upload(filename, make_pdf(pages))
        if drain_jobs:
            drain(allow_failures=allow_failures)
        return work_item_id

    def item(self, work_item_id: uuid.UUID) -> WorkItem:
        self.db.expire_all()
        return self.db.execute(select(WorkItem).where(WorkItem.id == work_item_id)).scalar_one()

    def refresh(self) -> None:
        self.db.commit()
        self.db.expire_all()

    def get(self, path: str, *, as_user=None, org: bool = False, **kw: Any):
        persona = as_user or self.tenant.owner
        return self.client.get(self.org_url(path) if org else self.url(path), headers=persona.headers, **kw)

    def post(self, path: str, body: Any = None, *, as_user=None, org: bool = False, **kw: Any):
        persona = as_user or self.tenant.owner
        return self.client.post(self.org_url(path) if org else self.url(path), json=body, headers=persona.headers, **kw)

    def put(self, path: str, body: Any = None, *, as_user=None, org: bool = False, **kw: Any):
        persona = as_user or self.tenant.owner
        return self.client.put(self.org_url(path) if org else self.url(path), json=body, headers=persona.headers, **kw)

    def patch(self, path: str, body: Any = None, *, as_user=None, org: bool = False, **kw: Any):
        persona = as_user or self.tenant.owner
        return self.client.patch(self.org_url(path) if org else self.url(path), json=body, headers=persona.headers, **kw)

    def delete(self, path: str, *, as_user=None, org: bool = False, **kw: Any):
        persona = as_user or self.tenant.owner
        return self.client.delete(self.org_url(path) if org else self.url(path), headers=persona.headers, **kw)


@pytest.fixture()
def engines(client: TestClient, db_session: Session, tenant: Fixture, test_sessions, recorded_llm,
            monkeypatch: pytest.MonkeyPatch) -> Engines:
    """An Enterprise-plan tenant with the live pipeline wired to the test DB."""
    from app.core.config import settings
    from app.services.embedding_service import embedding_service

    # Deterministic OCR for scanned pages and embeddings, whatever the environment says; the model the
    # embedding service cached (if any) is put back afterwards so no other test inherits the stub.
    monkeypatch.setattr(settings, "ML_STUBS", True)
    monkeypatch.setattr(embedding_service, "_model", None)
    harness = Engines(client=client, db=db_session, tenant=tenant, llm=recorded_llm)
    harness.plan("enterprise")
    return harness
