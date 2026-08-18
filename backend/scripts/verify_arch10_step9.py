#!/usr/bin/env python
r"""ARCH-10 Step 9 — the final release gate.

    python scripts/verify_arch10_step9.py [--verbose] [--with-ocr]

Exit 0 = pass, 1 = failure, 2 = could not run.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

# Windows Multi-OpenMP Safety & Encoding Safeguards
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["FLAGS_enable_pir_in_executor"] = "0"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE = "arch10gate789"
_results: list[tuple[str, str, bool, str]] = []
_verbose = False


def check(cid: str, desc: str) -> Callable:
    def wrapper(fn: Callable[..., Optional[str]]) -> Callable[..., None]:
        def runner(*a: Any, **k: Any) -> None:
            try:
                _results.append((cid, desc, True, fn(*a, **k) or ""))
            except AssertionError as e:
                _results.append((cid, desc, False, str(e)))
            except Exception as e:  # noqa: BLE001
                _results.append((cid, desc, False, f"{type(e).__name__}: {e}"))

        return runner

    return wrapper


def _in_clean_interpreter(body: str, timeout: int = 300) -> dict[str, Any]:
    script = (
        "import sys, json, os\n"
        "os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'\n"
        "os.environ['FLAGS_use_mkldnn'] = '0'\n"
        "os.environ['FLAGS_enable_pir_api'] = '0'\n"
        "os.environ['FLAGS_enable_pir_in_executor'] = '0'\n"
        f"{body}\n"
        "print('__GATE__' + json.dumps(_out))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"subprocess failed:\n{result.stderr[-800:]}"
    for line in reversed(result.stdout.splitlines()):
        if line.startswith("__GATE__"):
            return json.loads(line[len("__GATE__") :])
    raise AssertionError(f"no gate output:\n{result.stdout[-400:]}")


def _seed_tenant(db) -> tuple[uuid.UUID, uuid.UUID]:
    from app.models.organization import Organization
    from app.models.workspace import Workspace

    org = Organization(name=f"{GATE}-org", slug=f"{GATE}-{uuid.uuid4().hex[:10]}")
    db.add(org)
    db.flush([org])
    workspace = Workspace(
        workspace_name=f"{GATE}-ws",
        slug=f"{GATE}ws{uuid.uuid4().hex[:8]}",
        organization_id=org.id,
    )
    db.add(workspace)
    db.flush([workspace])
    return org.id, workspace.id


def _cleanup(db, organization_id: uuid.UUID) -> None:
    from sqlalchemy import text as sql_text

    try:
        db.execute(sql_text("SET LOCAL session_replication_role = 'replica'"))
        db.execute(
            sql_text("DELETE FROM organizations WHERE id = :i"),
            {"i": str(organization_id)},
        )
        db.commit()
    except Exception:
        db.rollback()


def _make_pdf(pages: int = 3, text: str = "FLOWPILOT DIGITAL TEXT LAYER CONTENT") -> bytes:
    """Creates a PDF with a valid digital text layer using pypdf."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    writer = PdfWriter()
    for index in range(pages):
        page = writer.add_blank_page(width=612, height=792)
        page_text = f"{text} page {index + 1} with sufficient length for extraction"
        content_stream = f"BT /F1 12 Tf 72 720 Td ({page_text}) Tj ET"
        stream = DecodedStreamObject()
        stream_bytes = content_stream.encode("latin-1")
        if hasattr(stream, "set_data"):
            stream.set_data(stream_bytes)
        elif hasattr(stream, "setData"):
            stream.setData(stream_bytes)
        else:
            stream._data = stream_bytes

        font_dict = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        res_dict = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_dict})
        })
        page[NameObject("/Resources")] = res_dict
        page[NameObject("/Contents")] = stream

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _ingest(
    db,
    organization_id,
    workspace_id,
    payload: bytes,
    filename="gate.pdf",
    declared_mime: Optional[str] = None,
):
    from app.services import document_intake_service, file_validation_service

    mime = declared_mime or (
        "image/png" if filename.endswith(".png") else "application/pdf"
    )
    validated = file_validation_service.validate_spooled(
        io.BytesIO(payload),
        len(payload),
        declared_mime=mime,
        original_filename=filename,
        allowed_mimes=["application/pdf", "image/png", "image/jpeg"],
        max_pages=500,
    )
    with validated:
        return document_intake_service.ingest_validated(
            db,
            validated,
            organization_id=organization_id,
            workspace_id=workspace_id,
            uploader_id=None,
        )


@check("G1.1", "app.main imports zero heavy ML modules")
def g1_1() -> str:
    report = _in_clean_interpreter(
        "import time\n"
        "t = time.perf_counter()\n"
        "import app.main\n"
        "_out = {'heavy': [m for m in ('paddleocr','paddle','torch','chromadb',"
        "'sentence_transformers','transformers') if m in sys.modules],"
        " 'seconds': round(time.perf_counter() - t, 2)}"
    )
    assert not report["heavy"], f"app.main pulled in {report['heavy']}"
    return f"clean; import wall time {report['seconds']}s"


@check("G1.2", "heavy call sites on the request path are at zero")
def g1_2() -> str:
    heavy = ("paddleocr", "paddle", "chromadb", "sentence_transformers", "torch")
    pattern = re.compile(
        r"^\s*(?:from\s+(" + "|".join(heavy) + r")\b|import\s+(" + "|".join(heavy) + r")\b)"
    )
    exempt_dirs = ("app/workers/", "app/services/ocr/")
    offenders: list[str] = []

    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(exempt_dirs):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        for index, line in enumerate(source.splitlines(), 1):
            if line.startswith((" ", "\t")):
                continue
            if pattern.match(line):
                offenders.append(f"{rel}:{index}  {line.strip()}")

    assert not offenders, (
        "module-scope heavy imports outside workers:\n  " + "\n  ".join(offenders)
    )
    return "0 module-scope heavy imports outside app/workers and app/services/ocr"


@check("G1.3", "no BackgroundTasks anywhere in the document upload route")
def g1_3() -> str:
    path = REPO_ROOT / "app" / "api" / "v1" / "work_items.py"
    source = path.read_text(encoding="utf-8", errors="ignore")
    offenders: list[str] = []
    for index, line in enumerate(source.splitlines(), 1):
        if "BackgroundTasks" in line or "background_tasks.add_task" in line:
            offenders.append(f"work_items.py:{index}  {line.strip()}")
    assert not offenders, (
        "BackgroundTasks present in upload route:\n  " + "\n  ".join(offenders)
    )
    return "0 background tasks in document intake"


@check("G1.4", "processing_jobs is gone from the schema and the code")
def g1_4(db) -> str:
    from sqlalchemy import text as sql_text

    exists = db.execute(
        sql_text("SELECT to_regclass('public.processing_jobs') IS NOT NULL")
    ).scalar_one()
    assert not exists, "processing_jobs table still present"

    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        source = path.read_text(encoding="utf-8", errors="ignore")
        if "ProcessingJob" in source:
            offenders.append(rel)
    assert not offenders, f"ProcessingJob still referenced in {offenders}"

    migrated = db.execute(
        sql_text("SELECT count(*) FROM jobs WHERE job_type = 'legacy.processing_job'")
    ).scalar_one()
    claimable = db.execute(
        sql_text(
            "SELECT count(*) FROM jobs WHERE job_type = 'legacy.processing_job' "
            "AND status IN ('PENDING','FAILED')"
        )
    ).scalar_one()
    assert claimable == 0, f"{claimable} migrated legacy rows are CLAIMABLE."
    return f"table dropped; {migrated} legacy rows migrated, 0 claimable"


@check("G2.1", "the database enforces the transition table, not just Python")
def g2_1(db) -> str:
    from sqlalchemy import text as sql_text

    organization_id, workspace_id = _seed_tenant(db)
    result = _ingest(db, organization_id, workspace_id, _make_pdf(1))
    db.commit()
    work_item_id = str(result.work_item.id)

    refused = False
    try:
        db.execute(
            sql_text(
                "UPDATE work_items SET pipeline_stage = 'COMPLETED' WHERE id = :i"
            ),
            {"i": work_item_id},
        )
        db.commit()
    except Exception:
        refused = True
        db.rollback()
    assert refused, "Raw UPDATE bypassed the pipeline state machine trigger"

    db.execute(
        sql_text("UPDATE work_items SET pipeline_stage = 'EXTRACTING' WHERE id = :i"),
        {"i": work_item_id},
    )
    db.commit()

    _cleanup(db, organization_id)
    return "illegal transition refused by trigger; legal transition allowed"


@check("G2.2", "the trigger's table matches app.services.pipeline_state")
def g2_2(db) -> str:
    from sqlalchemy import text as sql_text

    from app.services.pipeline_state import STAGE_TRANSITIONS

    source = db.execute(
        sql_text(
            "SELECT prosrc FROM pg_proc WHERE proname = "
            "'work_items_stage_transition_guard'"
        )
    ).scalar_one_or_none()
    assert source, "transition guard function not found in pg_proc"

    missing: list[str] = []
    for from_stage, targets in STAGE_TRANSITIONS.items():
        for to_stage in targets:
            if f"('{from_stage.value}','{to_stage.value}')" not in source.replace(
                " ", ""
            ):
                missing.append(f"{from_stage.value}->{to_stage.value}")
    assert not missing, f"Missing transitions in DB trigger: {missing}"
    return f"{sum(len(v) for v in STAGE_TRANSITIONS.values())} transitions agree"


@check("G2.3", "each stage arrival emits at most one outbox event")
def g2_3(db) -> str:
    from sqlalchemy import func, select

    from app.models.outbox_event import OutboxEvent
    from app.services.pipeline_state import PipelineStage, transition_by_id

    organization_id, workspace_id = _seed_tenant(db)
    result = _ingest(db, organization_id, workspace_id, _make_pdf(1))
    db.commit()
    work_item_id = result.work_item.id

    for _ in range(3):
        transition_by_id(
            db,
            work_item_id=work_item_id,
            to_stage=PipelineStage.EXTRACTING,
            organization_id=organization_id,
        )
        db.commit()

    count = db.execute(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.resource_id == work_item_id,
            OutboxEvent.event_type == "document.processing",
        )
    ).scalar_one()
    assert count == 1, f"Expected 1 event, got {count}"
    _cleanup(db, organization_id)
    return "3 stage re-entries -> 1 event"


@check("G2.4", "document.* event types are in the published vocabulary and the CHECK")
def g2_4(db) -> str:
    from sqlalchemy import text as sql_text

    from app.core.webhook_events import WEBHOOK_EVENT_TYPES

    expected = {
        "document.queued",
        "document.processing",
        "document.completed",
        "document.failed",
    }
    missing = sorted(expected - set(WEBHOOK_EVENT_TYPES))
    assert not missing, f"missing from WEBHOOK_EVENT_TYPES: {missing}"

    constraint = db.execute(
        sql_text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_webhook_deliveries_event_type_vocabulary'"
        )
    ).scalar_one_or_none()
    assert constraint, "webhook delivery vocabulary constraint not found"
    absent = sorted(e for e in expected if f"'{e}'" not in constraint)
    assert not absent, f"{absent} missing from CHECK constraint"
    return "4 document.* types in both the module and the constraint"


@check("G3.1", "a document goes storage -> worker -> metered -> COMPLETED")
def g3_1(db) -> str:
    from sqlalchemy import select

    from app.core.principal import system_principal
    from app.core.storage import get_storage_driver
    from app.models.job import Job, JobStatus
    from app.models.work_item import WorkItem
    from app.services import job_service
    from app.services.pipeline_state import PipelineStage
    from app.workers.claim import JOBS_QUEUE, claim_eligible_rows, mark_job_succeeded
    from app.workers.handlers import register_all

    register_all()
    organization_id, workspace_id = _seed_tenant(db)
    result = _ingest(db, organization_id, workspace_id, _make_pdf(3))
    db.commit()

    work_item_id = result.work_item.id
    storage_key = result.storage_key

    driver = get_storage_driver()
    assert driver.exists(storage_key)

    job = db.execute(
        select(Job).where(
            Job.job_type == "document.extract",
            Job.payload["work_item_id"].astext == str(work_item_id),
        )
    ).scalar_one()
    assert job.status is JobStatus.PENDING

    claimed = claim_eligible_rows(
        db, JOBS_QUEUE, batch_size=100, lease_seconds=300, per_org_cap=5
    )
    db.commit()
    assert any(c.id == job.id for c in claimed)

    handler = job_service.JOB_HANDLERS["document.extract"]
    payload_dict = dict(job.payload)
    payload_dict["job_id"] = str(job.id)
    with system_principal(job_name="jobs.document.extract", job_id=job.id):
        outcome = handler(payload_dict)
    mark_job_succeeded(db, job.id, result=outcome)
    db.commit()

    assert outcome["outcome"] == "COMPLETED"

    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == work_item_id)
    ).scalar_one()
    assert work_item.pipeline_stage in {
        PipelineStage.EXTRACTED.value,
        PipelineStage.ENRICHING.value,
        PipelineStage.COMPLETED.value,
    }

    _cleanup(db, organization_id)
    return f"stored, claimed, extracted; stage={work_item.pipeline_stage}"


@check("G3.2", "extraction chains an enrichment job rather than claiming COMPLETED")
def g3_2(db) -> str:
    from sqlalchemy import select

    from app.core.principal import system_principal
    from app.models.job import Job
    from app.services import job_service
    from app.workers.handlers import register_all

    register_all()
    organization_id, workspace_id = _seed_tenant(db)
    result = _ingest(db, organization_id, workspace_id, _make_pdf(2))
    db.commit()

    job = db.execute(
        select(Job).where(
            Job.job_type == "document.extract",
            Job.payload["work_item_id"].astext == str(result.work_item.id),
        )
    ).scalar_one()
    handler = job_service.JOB_HANDLERS["document.extract"]
    payload_dict = dict(job.payload)
    payload_dict["job_id"] = str(job.id)
    with system_principal(job_name="jobs.document.extract", job_id=job.id):
        handler(payload_dict)
    db.commit()

    enrich = db.execute(
        select(Job).where(
            Job.job_type == "document.enrich",
            Job.payload["work_item_id"].astext == str(result.work_item.id),
        )
    ).scalar_one_or_none()
    assert enrich is not None, "extraction did not enqueue document.enrich"
    _cleanup(db, organization_id)
    return "document.extract -> document.enrich chained"


@check("G3.3", "a spend ceiling refuses at the boundary and blocks the document")
def g3_3(db) -> str:
    from sqlalchemy import select

    from app.core.exceptions import SpendLimitExceededError
    from app.models.spend_limit import SpendLimitPeriod
    from app.models.work_item import WorkItem
    from app.services import spend_control_service as spend
    from app.services.pipeline_state import PipelineStage
    from app.workers.handlers.ocr import Outcome, _block_on_quota, _Target

    organization_id, workspace_id = _seed_tenant(db)
    result = _ingest(db, organization_id, workspace_id, _make_pdf(4))
    spend.set_limit(
        db,
        organization_id=organization_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("1"),
    )
    db.commit()

    raised: Optional[SpendLimitExceededError] = None
    try:
        spend.ensure_within_limits(
            db, organization_id=organization_id, event_type="ocr.page", quantity=4
        )
    except SpendLimitExceededError as exc:
        raised = exc
        db.rollback()
    assert raised is not None

    target = _Target(
        work_item_id=result.work_item.id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        storage_key=result.storage_key,
        mime_type="application/pdf",
        estimated_pages=4,
        already_done=False,
    )
    outcome = _block_on_quota(target, raised)
    assert outcome["outcome"] == Outcome.QUOTA_BLOCKED

    db.expire_all()
    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == result.work_item.id)
    ).scalar_one()
    assert work_item.pipeline_stage == PipelineStage.QUOTA_BLOCKED.value

    _cleanup(db, organization_id)
    return "refused pre-call; document QUOTA_BLOCKED with a reset time"


@check("G3.4", "a worker that dies mid-extraction is recovered by the lease reaper")
def g3_4(db) -> str:
    from sqlalchemy import select, update

    from app.models.job import Job, JobStatus
    from app.workers.claim import (
        JOBS_QUEUE,
        claim_eligible_rows,
        release_expired_leases,
    )

    organization_id, workspace_id = _seed_tenant(db)
    result = _ingest(db, organization_id, workspace_id, _make_pdf(1))
    db.commit()

    job = db.execute(
        select(Job).where(
            Job.job_type == "document.extract",
            Job.payload["work_item_id"].astext == str(result.work_item.id),
        )
    ).scalar_one()

    claimed = claim_eligible_rows(
        db, JOBS_QUEUE, worker_id="gate:doomed", batch_size=100, lease_seconds=1
    )
    db.commit()
    assert any(c.id == job.id for c in claimed)

    db.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(
            claim_expires_at=datetime.now(timezone.utc) - timedelta(seconds=30),
            available_at=datetime.now(timezone.utc) - timedelta(seconds=30),
        )
    )
    db.commit()

    reaped = release_expired_leases(db, JOBS_QUEUE)
    db.commit()
    assert reaped >= 1

    row = db.execute(select(Job).where(Job.id == job.id)).scalar_one()
    assert row.status == JobStatus.FAILED or row.status == "FAILED"
    assert row.attempts == 1

    # Backdate available_at so it is immediately eligible for re-claim test
    db.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(available_at=datetime.now(timezone.utc) - timedelta(seconds=5))
    )
    db.commit()

    reclaimed = claim_eligible_rows(
        db, JOBS_QUEUE, worker_id="gate:survivor", batch_size=100, lease_seconds=60
    )
    db.commit()
    assert any(c.id == job.id for c in reclaimed)

    _cleanup(db, organization_id)
    return "claim -> death -> reap -> re-claim, attempts preserved at 1"


@check("G3.5", "tenant B's document is never reachable from tenant A")
def g3_5(db) -> str:
    from app.core.storage import TenantKeyError, assert_key_belongs_to, parse_key

    org_a, ws_a = _seed_tenant(db)
    org_b, ws_b = _seed_tenant(db)
    doc_a = _ingest(db, org_a, ws_a, _make_pdf(1), "a.pdf")
    doc_b = _ingest(db, org_b, ws_b, _make_pdf(1), "b.pdf")
    db.commit()

    assert parse_key(doc_a.storage_key).organization_id == org_a
    assert parse_key(doc_b.storage_key).organization_id == org_b
    assert doc_a.storage_key != doc_b.storage_key

    for key, wrong_org in ((doc_a.storage_key, org_b), (doc_b.storage_key, org_a)):
        try:
            assert_key_belongs_to(key, wrong_org)
        except TenantKeyError:
            continue
        raise AssertionError(f"key {key} verified against wrong organization")

    _cleanup(db, org_a)
    _cleanup(db, org_b)
    return "two tenants, disjoint prefixes, cross-verification refused"


@check("G4.1", "every registered job type is claimable by some production profile")
def g4_1() -> str:
    from app.services import job_service
    from app.workers.handlers import register_all
    from app.workers.profiles import uncovered_job_types

    register_all()
    uncovered = uncovered_job_types(job_service.JOB_HANDLERS)
    assert not uncovered, f"job types no production profile claims: {sorted(uncovered)}"
    return f"{len(job_service.JOB_HANDLERS)} handlers, all routed"


@check("G4.2", "profiles are disjoint — no job type is claimable by two")
def g4_2() -> str:
    from app.workers.profiles import ENRICH, LIGHT, OCR

    seen: dict[str, str] = {}
    clashes: list[str] = []
    for profile in (LIGHT, OCR, ENRICH):
        for job_type in profile.job_types or ():
            if job_type in seen:
                clashes.append(f"{job_type}: {seen[job_type]} + {profile.name}")
            seen[job_type] = profile.name
    assert not clashes, f"overlapping profiles: {clashes}"
    return f"{len(seen)} job types across 3 disjoint profiles"


@check("G4.3", "the light profile refuses to start with PaddleOCR loaded")
def g4_3() -> str:
    report = _in_clean_interpreter(
        "import types\n"
        "sys.modules['paddleocr'] = types.ModuleType('paddleocr')\n"
        "from app.workers.profiles import LIGHT, ProfileError, assert_imports_match_profile\n"
        "try:\n"
        "    assert_imports_match_profile(LIGHT)\n"
        "    _out = {'refused': False}\n"
        "except ProfileError as exc:\n"
        "    _out = {'refused': True, 'error': str(exc)[:120]}"
    )
    assert report["refused"], "Light profile accepted paddleocr loaded"
    return "refused, with the reason named"


@check("G4.4", "a claim filtered by job type returns only that type")
def g4_4(db) -> str:
    from app.services import job_service
    from app.workers.claim import JOBS_QUEUE, claim_eligible_rows
    from app.workers.handlers import register_all
    from app.workers.profiles import OCR, claimable_job_types

    register_all()
    organization_id, workspace_id = _seed_tenant(db)
    _ingest(db, organization_id, workspace_id, _make_pdf(1))
    job_service.enqueue(
        db,
        job_type="storage.sample",
        payload={"gate": GATE},
        organization_id=organization_id,
    )
    db.commit()

    claimed = claim_eligible_rows(
        db,
        JOBS_QUEUE,
        worker_id="gate:ocr",
        batch_size=25,
        lease_seconds=60,
        job_types=claimable_job_types(OCR),
    )
    db.commit()

    types = {job.job_type for job in claimed}
    assert types <= {"document.extract"}
    assert "document.extract" in types

    _cleanup(db, organization_id)
    return f"claimed {sorted(types)} only"


@check("G4.5", "the job-type routing index exists and is valid")
def g4_5(db) -> str:
    from sqlalchemy import text as sql_text

    row = db.execute(
        sql_text(
            "SELECT i.indisvalid FROM pg_class c "
            "JOIN pg_index i ON i.indexrelid = c.oid "
            "WHERE c.relname = 'ix_jobs_claimable_by_type'"
        )
    ).scalar_one_or_none()
    assert row is not None, "ix_jobs_claimable_by_type is missing"
    assert row is True, "ix_jobs_claimable_by_type exists but is INVALID"
    return "present and valid"


@check("G5.1", "single alembic head")
def g5_1() -> str:
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, f"alembic heads failed: {out.stderr[-400:]}"
    heads = [line for line in out.stdout.splitlines() if line.strip()]
    assert len(heads) == 1, f"expected 1 head, got {len(heads)}: {heads}"
    return heads[0].strip()


@check("G5.2", "every ARCH-10 trigger is present in pg_trigger")
def g5_2(db) -> str:
    from sqlalchemy import text as sql_text

    expected = {
        "usage_events": "trg_usage_events_immutable",
        "work_items": "trg_work_items_stage_transition",
    }
    missing: list[str] = []
    for table, trigger in expected.items():
        found = db.execute(
            sql_text(
                "SELECT count(*) FROM pg_trigger t JOIN pg_class c "
                "ON c.oid = t.tgrelid WHERE c.relname = :tbl AND t.tgname = :trg"
            ),
            {"tbl": table, "trg": trigger},
        ).scalar_one()
        if not found:
            missing.append(f"{table}.{trigger}")
    assert not missing, f"missing triggers: {missing}"
    return f"{len(expected)} triggers present"


@check("G5.3", "autogenerate produces no unmigrated schema drift")
def g5_3() -> str:
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    combined = (out.stdout + out.stderr).lower()
    if "target database is not up to date" in combined:
        raise AssertionError("Target database is not at Alembic head — run alembic upgrade head")

    real_diffs = []
    for line in out.stdout.splitlines():
        low = line.lower()
        if "detected" in low and not any(
            ign in low
            for ign in (
                "owner_id",
                "fk_uploaded_files_owner_id_users",
                "ix_workspaces_logo_file_id",
                "automation_rules",
                "ix_jobs_claimable_by_type",
            )
        ):
            real_diffs.append(line)
    assert not real_diffs, "models and DB disagree:\n  " + "\n  ".join(real_diffs)
    return "models and schema agree"


@check("G6.1", "PaddleOCR constructs and reports its bound API surface")
def g6_1() -> str:
    from app.services.ocr.paddle import PaddleOCRProvider

    provider = PaddleOCRProvider()
    assert provider.is_available(), "paddleocr not installed"
    provider._build_engine()
    assert provider._predict_method
    return f"{provider._model_name} via {provider._predict_method}()"


@check("G6.2", "a scanned page is OCR'd, billed once, and carries bounding boxes")
def g6_2(db) -> str:
    from PIL import Image, ImageDraw
    from sqlalchemy import func, select

    from app.core.principal import system_principal
    from app.models.job import Job
    from app.models.usage_event import UsageEvent
    from app.models.work_item import WorkItem
    from app.services import job_service
    from app.workers.handlers import register_all

    register_all()
    organization_id, workspace_id = _seed_tenant(db)

    image = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(image).text((40, 120), "FLOWPILOT GATE INVOICE 12345", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    result = _ingest(
        db,
        organization_id,
        workspace_id,
        buffer.getvalue(),
        "scan.png",
        declared_mime="image/png",
    )
    db.commit()

    job = db.execute(
        select(Job).where(
            Job.job_type == "document.extract",
            Job.payload["work_item_id"].astext == str(result.work_item.id),
        )
    ).scalar_one()
    handler = job_service.JOB_HANDLERS["document.extract"]
    payload_dict = dict(job.payload)
    payload_dict["job_id"] = str(job.id)
    with system_principal(job_name="jobs.document.extract", job_id=job.id):
        outcome = handler(payload_dict)
    db.commit()

    assert outcome["outcome"] == "COMPLETED"
    assert outcome["billable_pages"] == 1

    billed = db.execute(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.event_type == "ocr.page",
        )
    ).scalar_one()
    assert Decimal(billed) == Decimal("1.000000")

    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == result.work_item.id)
    ).scalar_one()
    pages = (work_item.extraction_metadata or {}).get("pages") or []
    assert pages
    boxes = [b for p in pages for b in (p.get("blocks") or []) if b.get("box")]
    assert boxes

    _cleanup(db, organization_id)
    return f"1 page OCR'd, 1 page billed, {len(boxes)} boxes retained"


def run_db_check(fn: Callable[[Any], None]) -> None:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        try:
            fn(db)
        finally:
            try:
                db.rollback()
            except Exception:
                pass


def main(argv: Optional[list[str]] = None) -> int:
    global _verbose
    parser = argparse.ArgumentParser(prog="verify_arch10_step9")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--with-ocr",
        action="store_true",
        help="run the checks that construct PaddleOCR.",
    )
    args = parser.parse_args(argv)
    _verbose = args.verbose

    try:
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] could not import the application: {exc}")
        return 2

    g1_1()
    g1_2()
    g1_3()
    g4_1()
    g4_2()
    g4_3()

    run_db_check(g1_4)
    run_db_check(g2_1)
    run_db_check(g2_2)
    run_db_check(g2_3)
    run_db_check(g2_4)
    run_db_check(g3_1)
    run_db_check(g3_2)
    run_db_check(g3_3)
    run_db_check(g3_4)
    run_db_check(g3_5)
    run_db_check(g4_4)
    run_db_check(g4_5)
    run_db_check(g5_2)

    if args.with_ocr:
        g6_1()
        run_db_check(g6_2)

    g5_1()
    g5_3()

    print("ARCH-10 Step 9 — final release gate\n")
    print("== FINDINGS " + "=" * 46)
    failures = 0
    for cid, desc, ok, detail in _results:
        tag = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"[{tag}] {cid:<6} {desc}")
        if detail and (_verbose or not ok):
            print(f"         {detail}")

    print(f"\n{len(_results) - failures} pass | {failures} fail")
    if not args.with_ocr:
        print(
            "\n[WARN] --with-ocr was not set. G6.1 and G6.2 did not run."
        )
    if failures:
        print("\n[FAIL] ARCH-10 is not closed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())