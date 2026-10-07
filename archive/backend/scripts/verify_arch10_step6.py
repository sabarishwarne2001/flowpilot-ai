#!/usr/bin/env python
r"""ARCH-10 Steps 4-6 gate — object storage, validation, metered OCR.

    python scripts/verify_arch10_step6.py [--verbose] [--skip-live-storage]

Exit 0 = pass, 1 = failure, 2 = could not run.
"""

from __future__ import annotations

import argparse
import io
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import uuid
from typing import Any, Callable, Optional

# Windows Encoding Safeguards
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

GATE_PREFIX = "arch10gate456"
_results: list[tuple[str, str, bool, str]] = []
_verbose = False
_skip_live = False


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


@check("S4.1", "every S3_* setting the factory reads exists on Settings")
def s4_1() -> str:
    from app.core.config import settings

    required = [
        "S3_BUCKET",
        "S3_REGION",
        "S3_ENDPOINT_URL",
        "S3_PREFIX",
        "S3_SERVER_SIDE_ENCRYPTION",
        "S3_MAX_POOL_CONNECTIONS",
        "S3_MULTIPART_THRESHOLD",
        "S3_MULTIPART_CHUNKSIZE",
        "S3_MAX_CONCURRENCY",
        "STORAGE_SAMPLE_INTERVAL_MINUTES",
    ]
    missing = [name for name in required if not hasattr(settings, name)]
    assert not missing, f"Settings is missing {missing}."
    return f"{len(required)} settings present"


@check("S4.2", "the factory builds a driver for every S3-compatible backend")
def s4_2() -> str:
    from app.core import config as config_module
    from app.core.storage import get_storage_driver, reset_storage_driver

    settings = config_module.settings
    saved = {
        name: getattr(settings, name)
        for name in ("STORAGE_BACKEND", "S3_BUCKET", "S3_ENDPOINT_URL", "ENVIRONMENT")
    }
    built: list[str] = []
    try:
        for backend, endpoint in (
            ("s3", None),
            ("r2", "https://acct.r2.cloudflarestorage.com"),
            ("minio", "http://localhost:9000"),
        ):
            reset_storage_driver()
            object.__setattr__(settings, "STORAGE_BACKEND", backend)
            object.__setattr__(settings, "S3_BUCKET", "gate-bucket")
            object.__setattr__(settings, "S3_ENDPOINT_URL", endpoint)
            driver = get_storage_driver()
            assert driver.supports_presigned, f"{backend} must support presigning"
            assert driver.supports_multipart, f"{backend} must support multipart"
            built.append(f"{backend}->{driver.backend_name}")
    finally:
        for name, value in saved.items():
            object.__setattr__(settings, name, value)
        reset_storage_driver()
    return ", ".join(built)


@check("S4.3", "R2 and MinIO omit the ServerSideEncryption header")
def s4_3() -> str:
    from app.core.storage.s3 import (
        MinIOStorageDriver,
        R2StorageDriver,
        S3StorageDriver,
    )

    class _FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def put_object(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {}

    results = []
    for cls, kwargs, expect_sse in (
        (S3StorageDriver, {}, True),
        (R2StorageDriver, {"endpoint_url": "https://a.r2.cloudflarestorage.com"}, False),
        (MinIOStorageDriver, {"endpoint_url": "http://localhost:9000"}, False),
    ):
        fake = _FakeClient()
        driver = cls(bucket="b", client=fake, **kwargs)
        driver.put("t/documents/x", b"data", "application/pdf")
        has_sse = "ServerSideEncryption" in fake.calls[0]
        assert has_sse is expect_sse
        results.append(f"{cls.__name__}:{'sse' if has_sse else 'no-sse'}")
    return ", ".join(results)


@check("S4.4", "tenant keys carry the organization id and reject cross-tenant reads")
def s4_4() -> str:
    from app.core.storage import (
        StorageNamespace,
        TenantKeyError,
        assert_key_belongs_to,
        parse_key,
        tenant_key,
    )

    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    file_id = uuid.uuid4()
    key = tenant_key(
        organization_id=org_a,
        namespace=StorageNamespace.DOCUMENTS,
        file_id=file_id,
        suffix="pdf",
    )
    assert key.startswith(f"{org_a}/documents/"), key

    parsed = parse_key(key)
    assert parsed.organization_id == org_a
    assert parsed.namespace is StorageNamespace.DOCUMENTS

    assert_key_belongs_to(key, org_a)
    try:
        assert_key_belongs_to(key, org_b)
    except TenantKeyError:
        pass
    else:
        raise AssertionError("Cross-tenant verification failed to raise TenantKeyError")

    for bad in ("../etc/passwd", "documents/x", f"{org_a}/nope/{file_id}", "/abs/key"):
        try:
            parse_key(bad)
        except Exception:
            continue
        raise AssertionError(f"malformed key {bad!r} was accepted")
    return "grammar enforced, cross-tenant verification refused"


@check("S4.5", "no storage key is built by f-string outside keys.py")
def s4_5() -> str:
    pattern = re.compile(
        r"f[\"'][^\"']*\{\s*(organization_id|org_id|context\.organization_id)"
        r"\s*\}[^\"']*/"
    )
    allowed = {"app/core/storage/keys.py"}
    offenders: list[str] = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in allowed:
            continue
        for index, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            if pattern.search(line):
                offenders.append(f"{rel}:{index}  {line.strip()}")
    assert not offenders, (
        "storage keys constructed outside keys.py:\n  " + "\n  ".join(offenders)
    )
    return "tenant_key() is the only key builder"


@check("S4.6", "put_stream round-trips and reports the right size and checksum")
def s4_6() -> str:
    import hashlib

    from app.core.storage.local import LocalStorageDriver
    from app.core.storage import StorageNamespace, tenant_key

    org = uuid.uuid4()
    payload = os.urandom(3 * 1024 * 1024)
    expected = hashlib.sha256(payload).hexdigest()

    with tempfile.TemporaryDirectory(prefix="fp-gate-") as root:
        driver = LocalStorageDriver(root=pathlib.Path(root))
        key = tenant_key(
            organization_id=org,
            namespace=StorageNamespace.DOCUMENTS,
            file_id=uuid.uuid4(),
            suffix="pdf",
        )
        stored = driver.put_stream(key, io.BytesIO(payload), "application/pdf")
        assert stored.size == len(payload), f"size {stored.size} != {len(payload)}"
        assert stored.checksum_sha256 == expected
        assert driver.get(key) == payload

        sink = io.BytesIO()
        written = driver.download_to(key, sink)
        assert written == len(payload) and sink.getvalue() == payload

        total, count = driver.usage_bytes(f"{org}/")
        assert total == len(payload) and count == 1, (total, count)
    return f"{len(payload)} bytes, checksum matched, usage_bytes agreed"


@check("S4.7", "local backend is refused in a production environment")
def s4_7() -> str:
    from app.core import config as config_module
    from app.core.storage import StorageError, get_storage_driver, reset_storage_driver

    settings = config_module.settings
    saved = (settings.ENVIRONMENT, settings.STORAGE_BACKEND)
    try:
        reset_storage_driver()
        object.__setattr__(settings, "ENVIRONMENT", "production")
        object.__setattr__(settings, "STORAGE_BACKEND", "local")
        try:
            get_storage_driver()
        except StorageError:
            return "refused in production environment"
        raise AssertionError("STORAGE_BACKEND=local was accepted in production.")
    finally:
        object.__setattr__(settings, "ENVIRONMENT", saved[0])
        object.__setattr__(settings, "STORAGE_BACKEND", saved[1])
        reset_storage_driver()


@check("S4.8", "the storage sampler emits storage.gb_month once per interval")
def s4_8(db) -> str:
    from decimal import Decimal
    from sqlalchemy import select

    from app.models.uploaded_file import UploadedFile
    from app.models.usage_event import UsageEvent
    from app.services import storage_sampler_service as sampler

    org_id = _seed_org(db)
    db.add(
        UploadedFile(
            organization_id=org_id,
            file_path=f"{org_id}/documents/{uuid.uuid4()}.pdf",
            original_filename="gate.pdf",
            mime_type="application/pdf",
            file_size=5 * 10**9,
            checksum_sha256="0" * 64,
        )
    )
    db.commit()

    first = sampler.sample_once(db)
    db.commit()
    target_sample = next((t for t in first.tenants if t.organization_id == org_id), None)
    assert target_sample is not None and target_sample.recorded, f"org not sampled: {first.as_result()}"

    second = sampler.sample_once(db)
    db.commit()
    target_second = next((t for t in second.tenants if t.organization_id == org_id), None)
    assert target_second is not None and not target_second.recorded, f"duplicate not suppressed: {second.as_result()}"

    rows = db.execute(
        select(UsageEvent).where(
            UsageEvent.organization_id == org_id,
            UsageEvent.event_type == "storage.gb_month",
        )
    ).scalars().all()
    assert len(rows) == 1, f"expected 1 usage row, got {len(rows)}"
    assert rows[0].quantity > Decimal(0), rows[0].quantity
    assert rows[0].unit == "gb_month"

    _cleanup(db, org_id)
    return f"5 GB sampled -> {rows[0].quantity} gb_month, duplicate suppressed"


@check("S5.1", "a PDF whose bytes are a ZIP is refused as a mismatch")
def s5_1() -> str:
    from app.services.file_validation_service import (
        FileValidationError,
        RejectionReason,
        validate_spooled,
    )

    payload = b"PK\x03\x04" + os.urandom(512)
    try:
        validate_spooled(
            io.BytesIO(payload),
            len(payload),
            declared_mime="application/pdf",
            original_filename="invoice.pdf",
            allowed_mimes=["application/pdf"],
            max_pages=500,
        )
    except FileValidationError as exc:
        assert exc.reason is RejectionReason.MIME_MISMATCH
        assert exc.should_quarantine
        return "refused, quarantine flagged"
    raise AssertionError("a ZIP declared as application/pdf was accepted")


@check("S5.2", "the size ceiling stops the read rather than measuring afterwards")
def s5_2() -> str:
    from app.services.file_validation_service import (
        FileValidationError,
        RejectionReason,
        spool_stream,
    )

    consumed = {"chunks": 0}

    def chunks():
        for _ in range(100):
            consumed["chunks"] += 1
            yield b"\x00" * 1024

    try:
        spool_stream(chunks(), max_bytes=4096)
    except FileValidationError as exc:
        assert exc.reason is RejectionReason.TOO_LARGE
        assert consumed["chunks"] <= 6
        return f"stopped after {consumed['chunks']} chunks"
    raise AssertionError("the ceiling did not fire")


@check("S5.3", "EXIF is removed from an image and the checksum reflects the scrub")
def s5_3() -> str:
    from PIL import Image
    from app.services.file_validation_service import validate_spooled

    buffer = io.BytesIO()
    image = Image.new("RGB", (48, 48), (120, 30, 30))
    exif = image.getexif()
    exif[271] = "GateCamera"
    exif[305] = "FlowPilotGate"
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    original = buffer.getvalue()
    assert b"GateCamera" in original

    with validate_spooled(
        io.BytesIO(original),
        len(original),
        declared_mime="image/jpeg",
        original_filename="photo.jpg",
        allowed_mimes=["image/jpeg"],
        max_pages=1,
    ) as validated:
        assert validated.scrubbed
        validated.handle.seek(0)
        cleaned = validated.handle.read()
        assert b"GateCamera" not in cleaned
        assert b"FlowPilotGate" not in cleaned
        import hashlib

        assert validated.checksum_sha256 == hashlib.sha256(cleaned).hexdigest()
        assert validated.page_count == 1
    return "EXIF removed, checksum taken after the rewrite"


@check("S5.4", "page count is extracted and a page bomb is refused")
def s5_4() -> str:
    from pypdf import PdfWriter
    from app.services.file_validation_service import (
        FileValidationError,
        RejectionReason,
        validate_spooled,
    )

    def make_pdf(pages: int) -> bytes:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(width=200, height=200)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    small = make_pdf(7)
    with validate_spooled(
        io.BytesIO(small),
        len(small),
        declared_mime="application/pdf",
        original_filename="ok.pdf",
        allowed_mimes=["application/pdf"],
        max_pages=100,
    ) as validated:
        assert validated.page_count == 7

    big = make_pdf(12)
    try:
        validate_spooled(
            io.BytesIO(big),
            len(big),
            declared_mime="application/pdf",
            original_filename="bomb.pdf",
            allowed_mimes=["application/pdf"],
            max_pages=10,
        )
    except FileValidationError as exc:
        assert exc.reason is RejectionReason.TOO_MANY_PAGES
        return "7 pages counted; 12 pages refused against a 10-page ceiling"
    raise AssertionError("the page ceiling did not fire")


@check("S5.5", "the upload route no longer writes to disk or uses BackgroundTasks")
def s5_5() -> str:
    source = (REPO_ROOT / "app" / "api" / "v1" / "work_items.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    offenders: list[str] = []
    if re.search(r"\bopen\s*\(\s*safe_path", source):
        offenders.append("open(safe_path, 'wb')")
    if "get_safe_path" in source:
        offenders.append("utils.get_safe_path")
    if re.search(r"background_tasks\.add_task\s*\(\s*process_document_pipeline", source):
        offenders.append("BackgroundTasks.add_task(process_document_pipeline)")
    assert not offenders, "work_items.py still contains:\n  " + "\n  ".join(offenders)
    return "intake goes through document_intake_service"


@check("S6.1", "document.extract and storage.sample are registered handlers")
def s6_1() -> str:
    from app.services import job_service
    from app.workers.handlers import ARCH10_JOB_TYPES, register_all

    register_all()
    missing = sorted(ARCH10_JOB_TYPES - set(job_service.JOB_HANDLERS))
    assert not missing, f"unregistered job types: {missing}"
    return f"registered: {sorted(ARCH10_JOB_TYPES)}"


@check("S6.2", "registering handlers does not import paddleocr into this process")
def s6_2() -> str:
    script = (
        "import sys, json\n"
        "import app.workers.handlers as h\n"
        "h.register_all()\n"
        "heavy = [m for m in ('paddleocr','paddle','torch','chromadb',"
        "'sentence_transformers') if m in sys.modules]\n"
        "print(json.dumps(heavy))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-600:]
    leaked = result.stdout.strip().splitlines()[-1]
    assert leaked == "[]", f"heavy modules leaked: {leaked}"
    return "clean interpreter, no heavy modules"


@check("S6.3", "only pages that reached the engine are billable")
def s6_3() -> str:
    from app.services.ocr.base import OCRBlock, OCRPage, OCRResult

    result = OCRResult(
        pages=[
            OCRPage(page_number=1, text="from the text layer", ocr_applied=False),
            OCRPage(page_number=2, text="from the text layer", ocr_applied=False),
            OCRPage(
                page_number=3,
                text="scanned",
                blocks=[OCRBlock(text="scanned", confidence=0.93)],
                ocr_applied=True,
            ),
        ],
        provider="paddleocr",
    )
    assert result.page_count == 3
    assert result.billable_pages == 1
    return "3 pages, 1 billable"


@check("S6.4", "bounding boxes survive normalisation from both PaddleOCR shapes")
def s6_4() -> str:
    from app.services.ocr.paddle import PaddleOCRProvider

    polygon = [[10.0, 20.0], [90.0, 22.0], [92.0, 48.0], [12.0, 46.0]]
    legacy = [[[polygon, ("INVOICE", 0.98)]]]
    modern = [
        {
            "rec_texts": ["INVOICE"],
            "rec_scores": [0.98],
            "rec_polys": [polygon],
        }
    ]

    for label, raw in (("2.x", legacy), ("3.x", modern)):
        blocks = PaddleOCRProvider._blocks_from_raw(raw)
        assert len(blocks) == 1, f"{label}: expected 1 block, got {len(blocks)}"
        block = blocks[0]
        assert block.text == "INVOICE"
        assert abs(block.confidence - 0.98) < 1e-6
        assert block.box is not None
        assert (block.box.x0, block.box.y0) == (10.0, 20.0)
        assert (block.box.x1, block.box.y1) == (92.0, 48.0)
    return "coordinates and confidence preserved on both API shapes"


@check("S6.5", "OCR usage is metered under the tenant's ceiling, once")
def s6_5(db) -> str:
    from decimal import Decimal
    from sqlalchemy import func, select

    from app.models.spend_limit import SpendLimitPeriod
    from app.models.usage_event import UsageEvent
    from app.services import spend_control_service as spend

    org_id = _seed_org(db)
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("50"),
    )
    db.commit()

    key = f"ocr:{uuid.uuid4()}"
    for _ in range(2):
        savepoint = db.begin_nested()
        try:
            with spend.guard_usage(
                db,
                organization_id=org_id,
                event_type="ocr.page",
                estimated_quantity=12,
                idempotency_key=key,
            ) as guard:
                guard.record(quantity=12, provider="paddleocr")
            savepoint.commit()
        except Exception:
            savepoint.rollback()
    db.commit()

    total = db.execute(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.organization_id == org_id,
            UsageEvent.event_type == "ocr.page",
        )
    ).scalar_one()
    assert Decimal(total) == Decimal("12.000000")
    _cleanup(db, org_id)
    return "re-run suppressed; 12 pages billed once"


@check("S6.6", "a quota refusal is returned, not raised into a retry loop")
def s6_6(db) -> str:
    from decimal import Decimal

    from app.core.exceptions import SpendLimitExceededError
    from app.models.spend_limit import SpendLimitPeriod
    from app.services import spend_control_service as spend
    from app.workers.handlers.ocr import Outcome, _block_on_quota, _Target

    org_id = _seed_org(db)
    workspace_id = _seed_workspace(db, org_id)
    spend.set_limit(
        db,
        organization_id=org_id,
        limit_key="ocr.page",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("1"),
    )
    db.commit()

    raised: Optional[SpendLimitExceededError] = None
    try:
        spend.ensure_within_limits(
            db, organization_id=org_id, event_type="ocr.page", quantity=40
        )
    except SpendLimitExceededError as exc:
        raised = exc
        db.rollback()
    assert raised is not None

    target = _Target(
        work_item_id=uuid.uuid4(),
        organization_id=org_id,
        workspace_id=workspace_id,
        storage_key=f"{org_id}/documents/{uuid.uuid4()}.pdf",
        mime_type="application/pdf",
        estimated_pages=40,
        already_done=False,
    )
    result = _block_on_quota(target, raised)
    assert result["outcome"] == Outcome.QUOTA_BLOCKED
    assert result["retryable_after_reset"] is True
    assert result["resets_at"]

    _cleanup(db, org_id)
    return "returned QUOTA_BLOCKED with a reset time"


@check("S6.7", "app.main still imports no heavy ML module")
def s6_7() -> str:
    script = (
        "import sys, json, time\n"
        "t = time.perf_counter()\n"
        "import app.main\n"
        "elapsed = time.perf_counter() - t\n"
        "heavy = [m for m in ('paddleocr','paddle','torch','chromadb',"
        "'sentence_transformers') if m in sys.modules]\n"
        "print(json.dumps({'heavy': heavy, 'seconds': round(elapsed, 2)}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-600:]
    import json

    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert not report["heavy"], f"app.main pulled in {report['heavy']}."
    return f"clean; import wall time {report['seconds']}s"


@check("S6.8", "single alembic head after the Step 5 revision")
def s6_8() -> str:
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


def _seed_org(db) -> uuid.UUID:
    from app.models.organization import Organization

    org = Organization(
        name=f"{GATE_PREFIX}-org", slug=f"{GATE_PREFIX}-{uuid.uuid4().hex[:10]}"
    )
    db.add(org)
    db.flush([org])
    return org.id


def _seed_workspace(db, organization_id: uuid.UUID) -> uuid.UUID:
    from app.models.workspace import Workspace

    workspace = Workspace(
        workspace_name=f"{GATE_PREFIX}-ws",
        slug=f"{GATE_PREFIX}ws{uuid.uuid4().hex[:8]}",
        organization_id=organization_id,
    )
    db.add(workspace)
    db.flush([workspace])
    return workspace.id


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
    global _verbose, _skip_live
    parser = argparse.ArgumentParser(prog="verify_arch10_step6")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--skip-live-storage", action="store_true")
    args = parser.parse_args(argv)
    _verbose = args.verbose
    _skip_live = args.skip_live_storage

    try:
        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] could not import the application: {exc}")
        return 2

    s4_1()
    s4_2()
    s4_3()
    s4_4()
    s4_5()
    s4_6()
    s4_7()
    s5_1()
    s5_2()
    s5_3()
    s5_4()
    s5_5()
    s6_1()
    s6_2()
    s6_3()
    s6_4()
    s6_7()

    run_db_check(s4_8)
    run_db_check(s6_5)
    run_db_check(s6_6)

    s6_8()

    print("ARCH-10 Steps 4-6 gate\n")
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
    if failures:
        print("\n[FAIL] resolve before ARCH-10 Step 7 (pipeline state machine).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
