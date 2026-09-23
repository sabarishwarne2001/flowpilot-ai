#!/usr/bin/env python3
"""ARCH-38 verification — Batch Ingestion & Universal Document Intelligence.

    python verify_arch38.py                      # offline gates only
    python verify_arch38.py --db                 # + live PostgreSQL
    python verify_arch38.py --mutate             # + mutation kills
    python verify_arch38.py --build              # + tsc, ESLint, vite build
    python verify_arch38.py --db --mutate --build

WHAT THESE GATES ARE FOR
========================

Offline gates EXECUTE code rather than grepping for it. Every archive limit is
exercised against a zip generated inside the test, every preset refusal is
raised by calling the validator, and every storage multipart operation runs
against a real temporary directory. A gate that only reads source proves the
source mentions a thing, not that the thing works.

`--db` runs every fixture inside one outer transaction with the engine's own
commits captured in SAVEPOINTs, and rolls the whole thing back at the end, so
running it against a populated database leaves nothing behind.

`--mutate` breaks one named thing at a time and asserts the named gate dies. A
mutation that kills nothing means the gate is decorative; a mutation that kills
everything means the harness broke rather than the gate firing. Benign edits are
included and must survive, which is what distinguishes the two.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import uuid
import zipfile
from pathlib import Path
from typing import Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"

sys.path.insert(0, str(BACKEND))

HEAD = "arch38_step1_batches"
STEP0 = "arch38_step0_batch_vocabulary"
PREVIOUS_HEAD = "arch37_step1_flow_builder"

NEW_FILES: tuple[str, ...] = (
    "alembic/versions/arch38_step0_batch_vocabulary.py",
    "alembic/versions/arch38_step1_batches.py",
    "app/models/ingestion.py",
    "app/schemas/ingestion.py",
    "app/api/v1/ingestion.py",
    "app/services/ingestion/__init__.py",
    "app/services/ingestion/archive.py",
    "app/services/ingestion/batch_service.py",
    "app/services/ingestion/bulk_service.py",
    "app/services/ingestion/preset_service.py",
    "app/services/ingestion/retention_service.py",
    "app/services/ingestion/upload_session_service.py",
    "app/workers/handlers/ingestion.py",
)

FRONTEND_NEW_FILES: tuple[str, ...] = (
    "src/services/api/ingestion.ts",
    "src/store/useUploadTrayStore.ts",
    "src/components/upload/UploadTray.tsx",
    "src/components/workItems/BulkActionBar.tsx",
    "src/components/settings/PresetGallery.tsx",
)

SENTINELS: dict[str, str] = {
    "app/core/storage/base.py": "ARCH38-S1:storage-multipart",
    "app/core/storage/local.py": "ARCH38-S1:storage-multipart-local",
    "app/core/storage/s3.py": "ARCH38-S1:storage-multipart-s3",
    "app/services/automation/triggers.py": "ARCH38-S1:batch-completed-trigger",
    "app/workers/profiles.py": "ARCH38-S1:ingestion-light-profile",
    "app/workers/handlers/__init__.py": "ARCH38-S1:ingestion-handlers",
    "app/workers/scheduler.py": "ARCH38-S1:ingestion-schedule",
    "app/api/v1/router.py": "ARCH38-S1:ingestion-routers",
    "app/models/audit_log.py": "ARCH38-S1:audit-ingestion",
    "app/models/__init__.py": "ARCH38-S1:models-ingestion",
    "verify_arch37.py": "ARCH38-S1:catalog-counts-37",
}

FRONTEND_SENTINELS: dict[str, str] = {
    "src/components/upload/UploadDropzone.tsx": "ARCH38-S2:tray-handoff",
    "src/layouts/DashboardLayout.tsx": "ARCH38-S2:upload-tray",
    "src/pages/Settings/DocumentSettings.tsx": "ARCH38-S2:preset-gallery",
    "src/services/api/endpoints.ts": "ARCH38-S2:ingestion-endpoints",
    "src/services/api/queryKeys.ts": "ARCH38-S2:ingestion-keys",
}

#: Every CHECK arch38_step1_batches adds, by the exact name it must carry.
#: `op.create_check_constraint` would emit `ck_<table>_ck_<table>_…` through the
#: metadata naming convention, so each of these is added with a raw
#: ALTER TABLE. Gate D2 asserts the live database has them under these names.
EXPECTED_CHECKS: tuple[str, ...] = (
    "ck_ingestion_batches_status",
    "ck_ingestion_batches_source",
    "ck_ingestion_batches_counts_nonnegative",
    "ck_ingestion_batches_counts_bounded",
    "ck_ingestion_batch_items_status",
    "ck_ingestion_batch_items_sha256_format",
    "ck_ingestion_batch_items_failed_has_code",
    "ck_upload_sessions_status",
    "ck_upload_sessions_part_size",
    "ck_upload_sessions_sha256_format",
    "ck_work_item_tags_tag_format",
    "ck_document_schema_presets_schema_object",
    "ck_document_schema_presets_assertions_array",
    "ck_document_schema_presets_version_positive",
    "ck_workspace_schema_presets_enabled_timestamp",
    "ck_retention_holds_scope_present",
)

EXPECTED_TABLES: tuple[str, ...] = (
    "ingestion_batches",
    "ingestion_batch_items",
    "upload_sessions",
    "work_item_tags",
    "document_schema_presets",
    "workspace_schema_presets",
    "retention_holds",
)

#: The composite foreign keys that make cross-workspace rows unrepresentable.
EXPECTED_COMPOSITE_FKS: tuple[str, ...] = (
    "fk_ingestion_batch_items_batch_workspace",
    "fk_ingestion_batch_items_work_item_workspace",
    "fk_work_item_tags_work_item_workspace",
)

ARCH38_JOB_TYPES: tuple[str, ...] = (
    "batch.expand_archive",
    "work_items.bulk",
    "ingestion.sweep_sessions",
)

PDF_BYTES = b"%PDF-1.4\n" + b"x" * 240 + b"\n%%EOF\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"y" * 600


# ===========================================================================
# Recorder
# ===========================================================================


class Recorder:
    def __init__(self) -> None:
        self.passed = 0
        self.failed: list[tuple[str, str]] = []

    def check(self, name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
        except AssertionError as exc:
            self.failed.append((name, str(exc) or "assertion failed"))
            print(f"  [FAIL] {name}")
            for line in str(exc).splitlines()[:4]:
                print(f"         {line}")
            return False
        except Exception as exc:  # noqa: BLE001
            detail = f"{type(exc).__name__}: {exc}"
            self.failed.append((name, detail))
            print(f"  [FAIL] {name}")
            print(f"         {detail}")
            if os.environ.get("VERIFY_ARCH38_TRACE"):
                traceback.print_exc()
            return False
        self.passed += 1
        print(f"  [PASS] {name}")
        return True


def _read(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8").replace("\r\n", "\n")


def make_zip(entries: list[tuple[str, bytes]], *, level: int = 9) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as z:
        for name, data in entries:
            z.writestr(name, data)
    return buffer.getvalue()


# ===========================================================================
# Offline gates
# ===========================================================================


def gates_offline(rec: Recorder, *, root: Path, only: Optional[set[str]]) -> None:
    def want(gate: str) -> bool:
        return only is None or gate in only

    # -- A: the apply landed --------------------------------------------------
    if want("A1"):
        def applied() -> None:
            missing = [rel for rel in NEW_FILES if not (root / rel).exists()]
            assert not missing, f"new files missing: {missing}. Run python apply_arch38.py"
            absent = [rel for rel, s in SENTINELS.items() if s not in _read(root / rel)]
            assert not absent, f"not applied: {absent}. Run python apply_arch38.py"
            fe = root.parent / "frontend"
            missing_fe = [rel for rel in FRONTEND_NEW_FILES if not (fe / rel).exists()]
            assert not missing_fe, f"frontend files missing: {missing_fe}"
            absent_fe = [
                rel for rel, s in FRONTEND_SENTINELS.items() if s not in _read(fe / rel)
            ]
            assert not absent_fe, f"frontend not applied: {absent_fe}"

        rec.check("A1 every new file and every sentinel is present", applied)

        if root == HERE and (HERE / "apply_arch38.py").exists():
            def idempotent() -> None:
                out = subprocess.run(
                    [sys.executable, str(HERE / "apply_arch38.py"), "--check"],
                    cwd=HERE, capture_output=True, text=True, timeout=300,
                )
                assert out.returncode == 0, out.stdout[-1500:] + out.stderr[-1500:]
                assert "0 file(s) would change" in out.stdout, out.stdout[-1500:]

            rec.check("A1 a second apply changes nothing", idempotent)

    # -- Z: archive limits, each exercised ------------------------------------
    archive = importlib.import_module("app.services.ingestion.archive")

    if want("Z1"):
        def bomb() -> None:
            payload = make_zip([("big.pdf", b"\x00" * (8 * 1024 * 1024))])
            try:
                list(archive.expand(payload))
            except archive.ArchiveRejected as exc:
                assert exc.code == "ARCHIVE_COMPRESSION_RATIO", exc.code
                return
            raise AssertionError("a zip bomb was accepted")

        rec.check("Z1 a zip bomb is refused by the compression ratio limit", bomb)

    if want("Z2"):
        def traversal() -> None:
            for evil in ("../escape.pdf", "/etc/passwd.pdf", "a/../../b.pdf", "..\\w.pdf"):
                try:
                    archive.safe_member_name(evil)
                except archive.ArchiveRejected as exc:
                    assert exc.code == "ARCHIVE_UNSAFE_PATH", (evil, exc.code)
                    continue
                raise AssertionError(f"{evil!r} was accepted")
            # An ordinary nested path is still fine.
            assert archive.safe_member_name("invoices/2026/a.pdf") == "invoices/2026/a.pdf"

        rec.check("Z2 four traversal shapes are refused; a normal path is not", traversal)

    if want("Z3"):
        def nested() -> None:
            inner = make_zip([("a.pdf", PDF_BYTES)])
            for name in ("inner.zip", "inner.pdf"):
                try:
                    list(archive.expand(make_zip([(name, inner)])))
                except archive.ArchiveRejected as exc:
                    assert exc.code == "ARCHIVE_NESTED", (name, exc.code)
                    continue
                raise AssertionError(f"a nested archive named {name} was accepted")

        rec.check("Z3 a nested archive is refused by name and by magic bytes", nested)

    if want("Z4"):
        def counts_and_types() -> None:
            many = make_zip([(f"f{i}.pdf", PDF_BYTES) for i in range(archive.MAX_ENTRIES + 1)])
            try:
                list(archive.expand(many))
                raise AssertionError("the entry count limit was not enforced")
            except archive.ArchiveRejected as exc:
                assert exc.code == "ARCHIVE_TOO_MANY_ENTRIES", exc.code
            try:
                list(archive.expand(make_zip([("notes.txt", b"hello" * 200)])))
                raise AssertionError("a disallowed member type was accepted")
            except archive.ArchiveRejected as exc:
                assert exc.code == "ARCHIVE_MEMBER_NOT_ALLOWED", exc.code

        rec.check("Z4 entry count and member MIME are enforced", counts_and_types)

    if want("Z5"):
        def happy() -> None:
            good = make_zip([("one.pdf", PDF_BYTES), ("sub/two.png", PNG_BYTES)])
            entries = list(archive.expand(good))
            assert len(entries) == 2, len(entries)
            assert {e.mime_type for e in entries} == {"application/pdf", "image/png"}
            assert entries[1].name == "two.png", entries[1].name
            assert entries[0].data == PDF_BYTES

        rec.check("Z5 a legitimate archive expands with correct MIMEs", happy)

    # -- P: presets -----------------------------------------------------------
    preset_service = importlib.import_module("app.services.ingestion.preset_service")

    if want("P1"):
        def schema_of_schemas() -> None:
            preset_service.validate_preset_schema(
                {
                    "type": "object",
                    "properties": {
                        "vendor_name": {"type": "string"},
                        "lines": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["vendor_name"],
                }
            )
            cases = [
                ({"type": "array", "properties": {"a": {"type": "string"}}}, "SCHEMA_TYPE"),
                ({"type": "object", "properties": {}}, "SCHEMA_NO_PROPERTIES"),
                ({"type": "object", "properties": {"A b": {"type": "string"}}}, "SCHEMA_FIELD_NAME"),
                ({"type": "object", "properties": {"a": {"type": "blob"}}}, "SCHEMA_FIELD_TYPE"),
                ({"type": "object", "properties": {"a": {"type": "array"}}}, "SCHEMA_ARRAY_ITEMS"),
                ({"type": "object", "properties": {"a": {"type": "string", "$ref": "#/x"}}}, "SCHEMA_UNSUPPORTED"),
                ({"type": "object", "properties": {"a": {"type": "string"}}, "required": ["z"]}, "SCHEMA_REQUIRED_UNKNOWN"),
            ]
            for schema, expected in cases:
                try:
                    preset_service.validate_preset_schema(schema)
                except preset_service.PresetError as exc:
                    assert exc.code == expected, (expected, exc.code)
                    continue
                raise AssertionError(f"{expected} was not raised")

        rec.check("P1 preset JSON validates against the schema-of-schemas", schema_of_schemas)

    if want("P2"):
        def profiles_exist() -> None:
            from app.services.redaction.vocabulary import PROFILE_KEYS

            preset_service.validate_redaction_profile("hipaa_safe_harbor")
            try:
                preset_service.validate_redaction_profile("not_a_profile")
                raise AssertionError("an unknown redaction profile was accepted")
            except preset_service.PresetError as exc:
                assert exc.code == "UNKNOWN_REDACTION_PROFILE", exc.code
            # Every profile a seeded preset names must exist in the vocabulary.
            # Read the migration's own constant rather than parsing its text:
            # grepping for a key also matches the seed loop's parameter binding,
            # which is a string that names the column, not a profile.
            module = _load_migration(root / "alembic/versions/arch38_step1_batches.py")
            for preset in module.PLATFORM_PRESETS:
                value = preset["redaction_profile"]
                assert value in PROFILE_KEYS, (
                    f"preset {preset['document_type']!r} names unknown redaction "
                    f"profile {value!r}"
                )

        rec.check("P2 every preset redaction profile exists in the vocabulary", profiles_exist)

    if want("P3"):
        def packs_seeded() -> None:
            module = _load_migration(root / "alembic/versions/arch38_step1_batches.py")
            presets = module.PLATFORM_PRESETS
            types = {p["document_type"] for p in presets}
            required = {
                "resume", "offer_letter",
                "intake_form", "discharge_summary",
                "nda", "msa", "lease",
                "bill_of_lading", "customs_manifest", "passport", "india_id_card",
            }
            missing = required - types
            assert not missing, f"preset packs missing: {sorted(missing)}"
            industries = {p["industry"] for p in presets}
            assert industries == {"HR", "HEALTHCARE", "LEGAL", "LOGISTICS", "KYC"}, industries
            for preset in presets:
                preset_service.validate_preset_schema(preset["schema"])
                preset_service.validate_assertions(preset["assertions"])

        rec.check("P3 all four vertical packs are seeded and every schema validates", packs_seeded)

    if want("P4"):
        def safe_harbor_wording() -> None:
            notice = preset_service.SAFE_HARBOR_NOTICE
            lowered = notice.lower()
            assert "identifier" in lowered, notice
            assert "business associate agreement" in lowered, notice
            assert "hipaa compliant" not in lowered, notice
            # And no product copy anywhere in the console may claim it.
            fe = root.parent / "frontend"
            offenders: list[str] = []
            for path in (fe / "src").rglob("*.ts*"):
                text = _read(path).lower()
                if "hipaa compliant" in text or "hipaa-compliant" in text:
                    offenders.append(str(path.relative_to(fe)))
            assert not offenders, f"product copy claims HIPAA compliance: {offenders}"

        rec.check("P4 Safe Harbor copy never claims HIPAA compliance", safe_harbor_wording)

    # -- T: tags --------------------------------------------------------------
    bulk_service = importlib.import_module("app.services.ingestion.bulk_service")

    if want("T1"):
        def tag_rules() -> None:
            assert bulk_service.normalise_tag("  Q3 Review ") == "q3-review"
            assert bulk_service.normalise_tag("invoice_2024") == "invoice_2024"
            for bad in ("-leading", "_leading", "x" * 49, "spaced/slash", ""):
                try:
                    bulk_service.normalise_tag(bad)
                except bulk_service.BulkError as exc:
                    assert exc.code == "TAG_INVALID", (bad, exc.code)
                    continue
                raise AssertionError(f"{bad!r} was accepted as a tag")

        rec.check("T1 tag normalisation accepts the legal and refuses the rest", tag_rules)

    if want("T2"):
        def tag_check_in_migration() -> None:
            text = _read(root / "alembic/versions/arch38_step1_batches.py")
            assert "ck_work_item_tags_tag_format" in text, (
                "the migration no longer adds the tag CHECK; the service alone is "
                "not the control, because a future caller can write the row directly"
            )
            assert "^[a-z0-9][a-z0-9_-]{0,47}$" in text, "the tag pattern changed"
            model = importlib.import_module("app.models.ingestion")
            assert model.TAG_PATTERN == "^[a-z0-9][a-z0-9_-]{0,47}$", model.TAG_PATTERN

        rec.check("T2 the tag CHECK exists in the migration and matches the model", tag_check_in_migration)

    # -- S: storage multipart -------------------------------------------------
    if want("S1"):
        def multipart_roundtrip() -> None:
            from app.core.storage.base import UploadedPart
            from app.core.storage.local import LocalStorageDriver

            workdir = Path(tempfile.mkdtemp(prefix="arch38-mp-"))
            try:
                driver = LocalStorageDriver(root=workdir)
                key = "org/ws/doc.pdf"
                begun = driver.create_multipart(key, "application/pdf")
                a, b = b"A" * 1000, b"B" * 500
                driver.upload_part(key, begun.upload_id, 1, a)
                driver.upload_part(key, begun.upload_id, 2, b)
                stored = driver.complete_multipart(
                    key, begun.upload_id,
                    [UploadedPart(1, "", 0), UploadedPart(2, "", 0)],
                    "application/pdf",
                )
                assert stored.size == 1500, stored.size
                assert stored.checksum_sha256 == hashlib.sha256(a + b).hexdigest()
                assert driver.get(key) == a + b
                assert stored.multipart is True
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        rec.check("S1 a multipart upload assembles in order and hashes correctly", multipart_roundtrip)

    if want("S2"):
        def part_idempotence_and_isolation() -> None:
            from app.core.storage.base import StorageError, UploadedPart
            from app.core.storage.local import LocalStorageDriver

            workdir = Path(tempfile.mkdtemp(prefix="arch38-mp2-"))
            try:
                driver = LocalStorageDriver(root=workdir)
                key = "org/ws/idem.pdf"
                begun = driver.create_multipart(key, "application/pdf")
                driver.upload_part(key, begun.upload_id, 1, b"first")
                driver.upload_part(key, begun.upload_id, 1, b"SECOND")
                driver.complete_multipart(
                    key, begun.upload_id, [UploadedPart(1, "", 0)], "application/pdf"
                )
                assert driver.get(key) == b"SECOND"

                other = driver.create_multipart("org/ws/a.pdf", "application/pdf")
                try:
                    driver.upload_part("org/ws/b.pdf", other.upload_id, 1, b"x")
                    raise AssertionError("a part for another key was accepted")
                except StorageError:
                    pass

                staged = driver.create_multipart("org/ws/c.pdf", "application/pdf")
                driver.upload_part("org/ws/c.pdf", staged.upload_id, 1, b"z" * 32)
                keys = driver.iter_keys()
                assert not any(k.startswith("_multipart/") for k in keys), keys
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        rec.check("S2 parts are idempotent, key-bound, and never listed as objects", part_idempotence_and_isolation)

    if want("S3"):
        def abc_refuses() -> None:
            from app.core.storage.base import (
                StorageCapabilityError,
                StorageDriver,
                StorageError,
            )
            from app.core.storage.local import LocalStorageDriver

            class Bare(StorageDriver):
                def put(self, key, data, mime_type): return key
                def get(self, key): return b""
                def delete(self, key): return False
                def exists(self, key): return False
                def stream(self, key): raise NotImplementedError
                def size(self, key): return 0

            bare = Bare()
            for call in (
                lambda: bare.create_multipart("k", "application/pdf"),
                lambda: bare.upload_part("k", "u", 1, b"x"),
                lambda: bare.complete_multipart("k", "u", [], "application/pdf"),
                lambda: bare.abort_multipart("k", "u"),
            ):
                try:
                    call()
                    raise AssertionError("a driver without multipart silently succeeded")
                except StorageCapabilityError:
                    pass

            workdir = Path(tempfile.mkdtemp(prefix="arch38-mp3-"))
            try:
                driver = LocalStorageDriver(root=workdir)
                for bad in (1024, 1024 * 1024, 128 * 1024 * 1024):
                    try:
                        driver.create_multipart("a/b.pdf", "application/pdf", part_size=bad)
                        raise AssertionError(f"part_size {bad} was accepted")
                    except StorageError:
                        pass
            finally:
                shutil.rmtree(workdir, ignore_errors=True)

        rec.check("S3 a driver without multipart refuses; part size bounds hold", abc_refuses)

    if want("S4"):
        def s3_uses_no_presigned_parts() -> None:
            text = _read(root / "app/core/storage/s3.py")
            assert "create_multipart_upload" in text, "the S3 driver has no multipart"
            assert "upload_part(" in text and "complete_multipart_upload" in text
            # ARCH-08 §B.11: parts must not be written through a bearer URL.
            for offender in ("generate_presigned_url" ,):
                segment = text.split("ARCH38-S1:storage-multipart-s3")[1]
                segment = segment.split("def presigned_get_url")[0]
                assert offender not in segment, (
                    "the multipart path builds a presigned URL; ARCH-08 §B.11 "
                    "requires parts to pass the tenant guard"
                )

        rec.check("S4 multipart parts never travel through a presigned URL", s3_uses_no_presigned_parts)

    # -- B: batch behaviour ---------------------------------------------------
    if want("B1"):
        def try_is_inside_the_loop() -> None:
            import ast

            source = _read(root / "app/services/ingestion/batch_service.py")
            tree = ast.parse(source)
            target = None
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "process_items":
                    target = node
            assert target is not None, "process_items is gone"
            loops = [n for n in ast.walk(target) if isinstance(n, ast.For)]
            assert loops, "process_items no longer loops"
            loop = loops[0]
            in_body = any(isinstance(stmt, ast.Try) for stmt in loop.body)
            assert in_body, (
                "the try is no longer the first statement inside the loop body. "
                "A try that wraps the whole loop means the first failure abandons "
                "every file after it -- the pre-ARCH-38 defect this replaces."
            )
            wrapping = [
                n for n in target.body
                if isinstance(n, ast.Try) and any(
                    isinstance(inner, ast.For) for inner in ast.walk(n)
                )
            ]
            assert not wrapping, "a try wraps the whole loop again"

        rec.check("B1 the per-item try is inside the loop, not around it", try_is_inside_the_loop)

    if want("B2"):
        def trigger_catalogued() -> None:
            from app.core import automation_events as ae
            from app.services.automation import triggers

            assert "trigger.batch.completed" in ae.INTERNAL_EVENT_TYPES
            assert "trigger.batch.completed" in triggers.CATALOG_EVENT_TYPES
            # ARCH40-S1:catalog-counts-38. ARCH-40 adds review.cleared, so the
            # catalog is 14 triggers over 15 events on an ARCH-40 tree. ARCH-38's
            # own counts stay acceptable: this gate asserts what ARCH-38 added
            # is present, not that nothing was added after it.
            counts = (len(triggers.TRIGGERS), len(triggers.CATALOG_EVENT_TYPES))
            assert counts in ((13, 14), (14, 15)), counts
            spec = triggers.TRIGGERS_BY_KEY["batch.completed"]
            assert spec.has_document is False, (
                "a batch is not one document; has_document=True would offer "
                "document conditions with nothing to read"
            )
            assert spec.category == "Documents", spec.category
            source = _read(root / "app/services/ingestion/batch_service.py")
            assert "emit_trigger(" in source and "event_type=BATCH_COMPLETED_EVENT" in source
            assert "db.commit()" not in source.split("def finalize_if_done")[1].split("def ")[0], (
                "finalize_if_done commits; emit_trigger must write the event and "
                "its job in the CALLER's transaction"
            )

        rec.check("B2 batch.completed is catalogued, documentless, and emitted", trigger_catalogued)

    if want("B3"):
        def workers_registered() -> None:
            from app.workers import profiles
            from app.workers.handlers import ALL_PHASE_JOB_TYPES, _HANDLERS
            from app.workers.scheduler import DEFAULT_SCHEDULE

            for job in ARCH38_JOB_TYPES:
                assert job in _HANDLERS, f"{job} has no handler"
                assert job in ALL_PHASE_JOB_TYPES, f"{job} is undeclared"
                assert profiles.LIGHT.may_claim(job), (
                    f"{job} is on no profile; assert_imports_match_profile() "
                    "raises at every worker's startup, so this stops the fleet booting"
                )
            uncovered = profiles.uncovered_job_types(set(_HANDLERS))
            assert not uncovered, f"job types no profile claims: {uncovered}"
            assert any(s.job_type == "ingestion.sweep_sessions" for s in DEFAULT_SCHEDULE), (
                "nothing reclaims abandoned multipart parts"
            )

        rec.check("B3 every ARCH-38 job has a handler, a profile and (where due) a schedule", workers_registered)

    if want("B4"):
        def routes_mounted_in_order() -> None:
            text = _read(root / "app/api/v1/router.py")
            ingestion_at = text.index("ingestion.work_item_router")
            work_items_at = text.index("(work_items.router,")
            assert ingestion_at < work_items_at, (
                "ingestion.work_item_router is mounted after work_items.router. "
                "work_items carries GET /{work_item_id}; mounted second, "
                "GET /work-items/tags is captured by it and 422s on a bad UUID."
            )

        rec.check("B4 literal work-item routes are mounted before the catch-all", routes_mounted_in_order)

    if want("B5"):
        def bulk_contract() -> None:
            from app.schemas.ingestion import BulkActionRequest

            payload = BulkActionRequest(
                action="delete",
                ids=[uuid.uuid4()],
                idempotency_key="abcdefgh",
            )
            assert payload.action == "delete"
            for bad_action in ("nuke", "archive"):
                try:
                    BulkActionRequest(
                        action=bad_action, ids=[uuid.uuid4()], idempotency_key="abcdefgh"
                    )
                    raise AssertionError(f"{bad_action} was accepted")
                except Exception:
                    pass
            assert set(bulk_service.BULK_ACTIONS) == {"delete", "reprocess", "export", "tag"}

        rec.check("B5 the bulk contract is exactly delete/reprocess/export/tag", bulk_contract)

    # -- M: migrations --------------------------------------------------------
    if want("M1"):
        def migration_chain() -> None:
            step0 = _read(root / "alembic/versions/arch38_step0_batch_vocabulary.py")
            step1 = _read(root / "alembic/versions/arch38_step1_batches.py")
            assert f'revision = "{STEP0}"' in step0
            assert f'down_revision = "{PREVIOUS_HEAD}"' in step0
            assert f'revision = "{HEAD}"' in step1
            assert f'down_revision = "{STEP0}"' in step1
            assert "autocommit_block()" in step0, (
                "new enum values must be added outside the transaction that uses them"
            )
            for name in EXPECTED_CHECKS:
                assert name in step1, f"{name} is not added by the migration"
            # A CALL, not a mention. The migration's own docstring explains why
            # this helper is avoided, and a substring test would fail on the
            # explanation rather than on the defect.
            assert "op.create_check_constraint(" not in step1, (
                "op.create_check_constraint emits ck_<table>_ck_<table>_… through "
                "the naming convention; use raw ALTER TABLE so gates find the name"
            )
            assert 'ALTER TABLE {table} ADD CONSTRAINT {name}' in step1, (
                "CHECKs are no longer added by raw ALTER TABLE under an exact name"
            )
            for fk in EXPECTED_COMPOSITE_FKS:
                assert fk in step1, f"{fk} is missing; cross-workspace rows become possible"
            assert "uq_work_items_id_workspace_id" in step1, (
                "the composite FKs need a UNIQUE on work_items (id, workspace_id)"
            )

        rec.check("M1 the migration chain, enum block, CHECK names and composite FKs", migration_chain)

    if want("M2"):
        def vocabularies_agree() -> None:
            module = _load_migration(root / "alembic/versions/arch38_step1_batches.py")
            model = importlib.import_module("app.models.ingestion")
            assert tuple(module.BATCH_STATUSES) == model.BATCH_STATUSES
            assert tuple(module.ITEM_STATUSES) == model.ITEM_STATUSES
            assert tuple(module.SESSION_STATUSES) == model.SESSION_STATUSES
            assert tuple(module.BATCH_SOURCES) == model.BATCH_SOURCES
            from app.models.audit_log import AuditResourceType

            step0 = _load_migration(root / "alembic/versions/arch38_step0_batch_vocabulary.py")
            for value in step0.NEW_RESOURCE_TYPES:
                assert hasattr(AuditResourceType, value), (
                    f"{value} is in the migration and not in the Python enum"
                )

        rec.check("M2 migration vocabularies equal the model and audit enums", vocabularies_agree)


def _load_migration(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(f"arch38_mig_{path.stem}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ===========================================================================
# Database gates
# ===========================================================================


def gates_db(rec: Recorder) -> None:
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.exc import DataError, IntegrityError
    from sqlalchemy.orm import Session

    url = os.environ.get("DATABASE_URL")
    if not url:
        rec.failed.append(("DB", "DATABASE_URL is not set"))
        print("  [FAIL] DB: DATABASE_URL is not set")
        return

    from app.models.ingestion import IngestionBatch, IngestionBatchItem
    from app.models.work_item import WorkItem
    from app.services.ingestion import (
        batch_service,
        bulk_service,
        retention_service,
        upload_session_service,
    )

    engine = create_engine(url)
    conn = engine.connect()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint")

    ORG, WS_A, WS_B, USER = (uuid.uuid4() for _ in range(4))

    def expect_violation(fn, fragment: str, message: str) -> None:
        sp = db.begin_nested()
        try:
            fn()
            db.flush()
        except (IntegrityError, DataError) as exc:
            sp.rollback()
            detail = str(exc)
            assert fragment in detail or "value too long" in detail, detail[:220]
            return
        sp.rollback()
        raise AssertionError(message)

    def work_item(ws, name="doc.pdf", age_days=400):
        wid = uuid.uuid4()
        db.execute(
            text(
                "INSERT INTO work_items (id, original_filename, stored_filename, "
                "file_type, file_size, status, workspace_id, created_by_user_id, "
                "created_at, updated_at) VALUES (:id, :n, :s, 'application/pdf', "
                "100, 'COMPLETED', :ws, :u, now() - make_interval(days => :age), now())"
            ),
            {"id": wid, "n": name, "s": f"{wid.hex}-{name}", "ws": ws,
             "u": USER, "age": age_days},
        )
        return wid

    try:
        db.execute(
            text("INSERT INTO organizations (id,name,slug,status,created_at,updated_at) "
                 "VALUES (:i,'ARCH38 Probe',:s,'ACTIVE',now(),now())"),
            {"i": ORG, "s": f"arch38-{ORG.hex[:8]}"},
        )
        db.execute(
            text("INSERT INTO users (id,email,hashed_password,is_active,is_superuser,"
                 "timezone,locale,created_at,updated_at) VALUES "
                 "(:i,:e,'x',true,false,'UTC','en',now(),now())"),
            {"i": USER, "e": f"arch38-{USER.hex[:8]}@example.invalid"},
        )
        for ws_id, tag in ((WS_A, "a"), (WS_B, "b")):
            db.execute(
                text("INSERT INTO workspaces (id,workspace_name,slug,organization_id,"
                     "status,timezone,language,currency,date_format,created_at,updated_at) "
                     "VALUES (:i,:n,:s,:o,'ACTIVE','UTC','en','USD','YYYY-MM-DD',now(),now())"),
                {"i": ws_id, "n": f"ARCH38 {tag}", "s": f"arch38-{tag}-{ws_id.hex[:8]}", "o": ORG},
            )
        db.flush()

        def scoped(name: str, fn: Callable[[], None]) -> None:
            sp = db.begin_nested()
            try:
                rec.check(name, fn)
            finally:
                if sp.is_active:
                    sp.rollback()

        # -- D1/D2: schema --------------------------------------------------
        def head_and_schema() -> None:
            head = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            # ARCH40-S1:head-widened-38. ARCH-40 advances the head; this gate
            # asserts ARCH-38's schema is applied, not that it is the newest.
            assert head in (HEAD, "arch40_step2_settings_backfill", "arch40_step2a_review_view_paths", "arch40_step3_contract_ai_settings", "hm1_tier_price_per_key"), f"alembic head is {head}; run `alembic upgrade head`"  # HM-S1:head-widened
            present = set(
                db.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                ).scalars()
            )
            missing = [t for t in EXPECTED_TABLES if t not in present]
            assert not missing, f"tables missing: {missing}"

        scoped(f"D1 head is {HEAD} and every ARCH-38 table exists", head_and_schema)

        def checks_by_exact_name() -> None:
            names = set(
                db.execute(
                    text("SELECT conname FROM pg_constraint WHERE contype='c'")
                ).scalars()
            )
            missing = [c for c in EXPECTED_CHECKS if c not in names]
            assert not missing, (
                f"CHECKs missing under their exact names: {missing}. "
                "op.create_check_constraint would have produced ck_<table>_ck_<table>_…"
            )
            fks = set(
                db.execute(
                    text("SELECT conname FROM pg_constraint WHERE contype='f'")
                ).scalars()
            )
            missing_fk = [f for f in EXPECTED_COMPOSITE_FKS if f not in fks]
            assert not missing_fk, f"composite FKs missing: {missing_fk}"

        scoped("D2 every CHECK and composite FK is present under its exact name", checks_by_exact_name)

        def presets_seeded() -> None:
            count, industries = db.execute(
                text("SELECT count(*), count(DISTINCT industry) FROM "
                     "document_schema_presets WHERE organization_id IS NULL")
            ).one()
            assert count >= 11, f"only {count} platform presets were seeded"
            assert industries == 5, f"{industries} industries seeded, expected 5"

        scoped("D3 eleven platform presets across five industries are seeded", presets_seeded)

        # -- D4: cross-workspace refusal, both halves ------------------------
        def cross_workspace() -> None:
            batch = batch_service.create_batch(
                db, workspace_id=WS_A, user_id=USER,
                files=[{"client_key": "k1", "filename": "a.pdf"}],
            )
            db.flush()
            expect_violation(
                lambda: db.execute(
                    text("INSERT INTO ingestion_batch_items (id,batch_id,workspace_id,"
                         "client_key,filename,size_bytes,status) VALUES "
                         "(:i,:b,:w,'evil','x.pdf',1,'PENDING')"),
                    {"i": uuid.uuid4(), "b": batch.id, "w": WS_B},
                ),
                "fk_ingestion_batch_items_batch_workspace",
                "the database accepted a cross-workspace batch item",
            )
            item = db.execute(
                select(IngestionBatchItem).where(IngestionBatchItem.batch_id == batch.id)
            ).scalar_one()
            try:
                batch_service.assert_item_in_workspace(db, workspace_id=WS_B, item_id=item.id)
                raise AssertionError("the service returned another workspace's item")
            except batch_service.BatchError as exc:
                assert exc.code == "ITEM_NOT_FOUND", exc.code
            wid = work_item(WS_A)
            db.flush()
            expect_violation(
                lambda: db.execute(
                    text("INSERT INTO work_item_tags (work_item_id,workspace_id,tag,created_at) "
                         "VALUES (:w,:ws,'urgent',now())"),
                    {"w": wid, "ws": WS_B},
                ),
                "fk_work_item_tags_work_item_workspace",
                "a tag was attached across workspaces",
            )

        scoped("D4 a cross-workspace batch item and tag are refused by DB and service", cross_workspace)

        def tag_check_live() -> None:
            wid = work_item(WS_A)
            db.flush()
            for bad in ("-leading", "UPPER", "has space", "x" * 49):
                expect_violation(
                    (lambda b=bad: db.execute(
                        text("INSERT INTO work_item_tags (work_item_id,workspace_id,tag,created_at) "
                             "VALUES (:w,:ws,:t,now())"),
                        {"w": wid, "ws": WS_A, "t": b},
                    )),
                    "ck_work_item_tags_tag_format",
                    f"the tag CHECK accepted {bad!r}",
                )

        scoped("D5 the live tag CHECK refuses four malformed tags", tag_check_live)

        def count_and_code_checks() -> None:
            batch = batch_service.create_batch(
                db, workspace_id=WS_A, user_id=USER,
                files=[{"client_key": "k1", "filename": "a.pdf"}],
            )
            db.flush()
            expect_violation(
                lambda: db.execute(
                    text("UPDATE ingestion_batches SET completed_items=5 WHERE id=:i"),
                    {"i": batch.id},
                ),
                "ck_ingestion_batches_counts_bounded",
                "counts were allowed to exceed total_items",
            )
            expect_violation(
                lambda: db.execute(
                    text("UPDATE ingestion_batch_items SET status='FAILED', "
                         "error_code=NULL WHERE batch_id=:b"),
                    {"b": batch.id},
                ),
                "ck_ingestion_batch_items_failed_has_code",
                "a FAILED item with no error code was accepted",
            )

        scoped("D6 counts stay bounded and a FAILED item must carry a code", count_and_code_checks)

        # -- D7: one failing file ------------------------------------------
        def one_failure_isolated() -> None:
            batch = batch_service.create_batch(
                db, workspace_id=WS_A, user_id=USER,
                files=[{"client_key": f"k{i}", "filename": f"f{i}.pdf"} for i in range(5)],
            )
            db.flush()
            items = batch_service.list_items(db, workspace_id=WS_A, batch_id=batch.id)
            seen: list[str] = []

            def handler(item):
                seen.append(item.client_key)
                if item.client_key == "k2":
                    raise RuntimeError("simulated per-file failure")
                return work_item(WS_A, item.filename)

            outcome = batch_service.process_items(
                db, batch=batch, items=items, organization_id=ORG, handler=handler
            )
            assert len(seen) == 5, f"the loop stopped after {len(seen)} files: {seen}"
            assert outcome == {"succeeded": 4, "failed": 1}, outcome
            fresh = db.execute(
                select(IngestionBatch).where(IngestionBatch.id == batch.id)
            ).scalar_one()
            db.refresh(fresh)
            assert fresh.status == "COMPLETED_WITH_ERRORS", fresh.status
            assert (fresh.completed_items, fresh.failed_items) == (4, 1)
            failed = batch_service.list_items(
                db, workspace_id=WS_A, batch_id=batch.id, failed_only=True
            )
            assert len(failed) == 1 and failed[0].error_code == "RuntimeError"

        scoped("D7 one failing file leaves the batch COMPLETED_WITH_ERRORS", one_failure_isolated)

        def trigger_and_job() -> None:
            batch = batch_service.create_batch(
                db, workspace_id=WS_A, user_id=USER,
                files=[{"client_key": "k0", "filename": "a.pdf"}],
            )
            db.flush()
            items = batch_service.list_items(db, workspace_id=WS_A, batch_id=batch.id)
            batch_service.process_items(
                db, batch=batch, items=items, organization_id=ORG,
                handler=lambda item: work_item(WS_A, item.filename),
            )
            row = db.execute(
                text("SELECT id, visibility, payload FROM outbox_events WHERE "
                     "event_type='trigger.batch.completed' AND resource_id=:r"),
                {"r": batch.id},
            ).first()
            assert row is not None, "no trigger.batch.completed event was written"
            assert row[1] == "INTERNAL", row[1]
            assert row[2]["total_items"] == 1, row[2]
            jobs = db.execute(
                text("SELECT count(*) FROM jobs WHERE job_type='automation.execute' "
                     "AND payload->>'outbox_event_id'=:e"),
                {"e": str(row[0])},
            ).scalar_one()
            assert jobs == 1, (
                f"the event has {jobs} automation jobs. Nothing relays INTERNAL "
                "events, so an event without its job is an event no rule sees."
            )

        scoped("D8 trigger.batch.completed is INTERNAL and carries its automation job", trigger_and_job)

        # -- D9: retention --------------------------------------------------
        def hold_blocks_delete() -> None:
            held = work_item(WS_A, "held.pdf")
            free = work_item(WS_A, "free.pdf")
            db.flush()
            retention_service.place_hold(
                db, organization_id=ORG, workspace_id=None, work_item_id=held,
                reason="Litigation hold", reference="ACME-2026", user_id=USER,
            )
            db.flush()
            results = bulk_service.bulk_delete(
                db, organization_id=ORG, workspace_id=WS_A, ids=[held, free]
            )
            by_id = {r.work_item_id: r for r in results}
            assert by_id[str(held)].code == "RETENTION_HOLD", by_id[str(held)]
            assert by_id[str(free)].outcome == "ok", by_id[str(free)]
            db.flush()
            assert db.get(WorkItem, held) is not None, "a held document was deleted"

        scoped("D9 bulk delete honours a retention hold and deletes the rest", hold_blocks_delete)

        def policy_floor() -> None:
            db.execute(
                text("INSERT INTO retention_policies (id,organization_id,"
                     "work_item_retention_days,created_at,updated_at) "
                     "VALUES (:i,:o,90,now(),now())"),
                {"i": uuid.uuid4(), "o": ORG},
            )
            young = work_item(WS_A, "young.pdf", age_days=2)
            old = work_item(WS_A, "old.pdf", age_days=400)
            db.flush()
            results = bulk_service.bulk_delete(
                db, organization_id=ORG, workspace_id=WS_A, ids=[young, old]
            )
            by_id = {r.work_item_id: r for r in results}
            assert by_id[str(young)].code == "RETENTION_POLICY", by_id[str(young)]
            assert by_id[str(old)].outcome == "ok", by_id[str(old)]

        scoped("D10 the ARCH-20 retention floor blocks a document that is too young", policy_floor)

        def bulk_never_crosses() -> None:
            mine = work_item(WS_A, "mine.pdf")
            theirs = work_item(WS_B, "theirs.pdf")
            db.flush()
            results = bulk_service.bulk_delete(
                db, organization_id=ORG, workspace_id=WS_A, ids=[mine, theirs]
            )
            by_id = {r.work_item_id: r for r in results}
            assert by_id[str(theirs)].code == "NOT_FOUND", by_id[str(theirs)]
            db.flush()
            assert db.get(WorkItem, theirs) is not None, (
                "bulk delete reached another workspace's document"
            )

        scoped("D11 bulk delete never reaches another workspace", bulk_never_crosses)

        # -- D12: upload sessions -------------------------------------------
        def resume_and_sha() -> None:
            from app.core.storage import get_storage_driver, reset_storage_driver

            reset_storage_driver()
            a, b = b"A" * (5 * 1024 * 1024), b"B" * 512
            digest = hashlib.sha256(a + b).hexdigest()
            session = upload_session_service.create(
                db, organization_id=ORG, workspace_id=WS_A, user_id=USER,
                filename="big.pdf", mime_type="application/pdf",
                total_size=len(a) + len(b), expected_sha256=digest,
            )
            db.flush()
            upload_session_service.receive_part(
                db, workspace_id=WS_A, session_id=session.id, part_number=1, data=a
            )
            db.flush()
            # The disconnect: the browser keeps nothing; the server's row is
            # the only record of what has landed.
            resumed = upload_session_service.resume(
                db, workspace_id=WS_A, session_id=session.id
            )
            assert resumed.parts_received == [1], resumed.parts_received
            upload_session_service.receive_part(
                db, workspace_id=WS_A, session_id=session.id, part_number=2, data=b
            )
            db.flush()
            done, assembled = upload_session_service.complete(
                db, workspace_id=WS_A, session_id=session.id
            )
            assert done.status == "COMPLETED", done.status
            assert hashlib.sha256(assembled).hexdigest() == digest

            bad = upload_session_service.create(
                db, organization_id=ORG, workspace_id=WS_A, user_id=USER,
                filename="bad.pdf", mime_type="application/pdf",
                total_size=10, expected_sha256="0" * 64,
            )
            db.flush()
            upload_session_service.receive_part(
                db, workspace_id=WS_A, session_id=bad.id, part_number=1, data=b"wrong"
            )
            db.flush()
            try:
                upload_session_service.complete(db, workspace_id=WS_A, session_id=bad.id)
                raise AssertionError("complete accepted a sha256 mismatch")
            except upload_session_service.UploadSessionError as exc:
                assert exc.code == "SHA256_MISMATCH", exc.code
            db.flush()
            reloaded = upload_session_service.resume(
                db, workspace_id=WS_A, session_id=bad.id
            )
            assert reloaded.status == "ABORTED", reloaded.status
            assert not get_storage_driver().exists(reloaded.object_key), (
                "the mismatched object was left in storage"
            )

        scoped("D12 a session resumes after a disconnect; complete refuses a sha mismatch", resume_and_sha)

        def session_isolation() -> None:
            session = upload_session_service.create(
                db, organization_id=ORG, workspace_id=WS_A, user_id=USER,
                filename="x.pdf", mime_type="application/pdf",
                total_size=None, expected_sha256=None,
            )
            db.flush()
            try:
                upload_session_service.resume(db, workspace_id=WS_B, session_id=session.id)
                raise AssertionError("another workspace read the session")
            except upload_session_service.UploadSessionError as exc:
                assert exc.code == "SESSION_NOT_FOUND", exc.code
            for _ in range(3):
                upload_session_service.receive_part(
                    db, workspace_id=WS_A, session_id=session.id,
                    part_number=1, data=b"z" * 32,
                )
            db.flush()
            again = upload_session_service.resume(
                db, workspace_id=WS_A, session_id=session.id
            )
            assert again.parts_received == [1], again.parts_received

        scoped("D13 a session is invisible cross-workspace and parts are idempotent", session_isolation)

        def tags_and_export() -> None:
            a = work_item(WS_A, "t1.pdf")
            b = work_item(WS_A, "t2.pdf")
            db.execute(
                text("UPDATE work_items SET extracted_entities = :e WHERE id = :i"),
                {"e": '{"vendor": "ACME", "total": 120}', "i": a},
            )
            db.flush()
            results = bulk_service.bulk_tag(
                db, workspace_id=WS_A, ids=[a, b], tags=["Q3 Review", "urgent"], user_id=USER
            )
            assert all(r.outcome == "ok" for r in results), results
            db.flush()
            stored = bulk_service.tags_for(db, workspace_id=WS_A, work_item_ids=[a, b])
            assert stored[str(a)] == ["q3-review", "urgent"], stored
            mime, body = bulk_service.build_export(
                db, workspace_id=WS_A, ids=[a], fmt="csv"
            )
            assert mime == "text/csv" and "field.vendor" in body and "ACME" in body

        scoped("D14 bulk tag normalises and export flattens extracted fields", tags_and_export)

    finally:
        db.close()
        outer.rollback()
        conn.close()


# ===========================================================================
# Mutation gates
# ===========================================================================

#: (file, find, replace, gate that must die, must_kill)
#:
#: `must_kill=True` is a mutation of a real control: the named gate has to fail.
#: `must_kill=False` is a benign edit that must survive, which is what proves
#: the harness is not simply failing on any change at all.
MUTATIONS: tuple[tuple[str, str, str, str, bool], ...] = (
    # M1 — the compression-ratio limit switched off.
    (
        "app/services/ingestion/archive.py",
        "MAX_COMPRESSION_RATIO: Final[float] = 100.0",
        "MAX_COMPRESSION_RATIO: Final[float] = 1e12",
        "Z1",
        True,
    ),
    # M2 — `..` allowed.
    #
    # BOTH traversal refusals have to go. `safe_member_name` checks the raw
    # segments and then re-checks after posixpath.normpath, and disabling only
    # the first leaves the second catching every shape -- which is the defence
    # in depth doing its job, and therefore not a mutation of "the" control.
    # Removing one of two redundant checks proves nothing about the gate.
    (
        "app/services/ingestion/archive.py",
        '''    segments = candidate.split("/")
    if any(segment == ".." for segment in segments):
        raise ArchiveRejected(
            "ARCHIVE_UNSAFE_PATH",
            "An entry name traverses outside the archive.",
            entry=raw,
        )

    normalised = posixpath.normpath(candidate)
    if normalised.startswith("/") or normalised == ".." or normalised.startswith("../"):''',
        '''    segments = candidate.split("/")
    if False and any(segment == ".." for segment in segments):
        raise ArchiveRejected(
            "ARCHIVE_UNSAFE_PATH",
            "An entry name traverses outside the archive.",
            entry=raw,
        )

    normalised = posixpath.normpath(candidate)
    if False and (normalised.startswith("/") or normalised == ".."):''',
        "Z2",
        True,
    ),
    # M2b — BENIGN: only the FIRST of the two traversal checks removed.
    # The normalised check must still refuse every shape, so nothing dies.
    # This is the pair to M2: together they show the redundancy is real and
    # that the gate tests traversal rather than one particular line.
    (
        "app/services/ingestion/archive.py",
        '''    if any(segment == ".." for segment in segments):''',
        '''    if False and any(segment == ".." for segment in segments):''',
        "Z2",
        False,
    ),
    # M3 — nested-archive detection removed.
    (
        "app/services/ingestion/archive.py",
        """        if is_archive_name(name):
            raise ArchiveRejected(""",
        """        if False and is_archive_name(name):
            raise ArchiveRejected(""",
        "Z3",
        True,
    ),
    # M4 — the loop-level try restored: one failure aborts the batch.
    (
        "app/services/ingestion/batch_service.py",
        """    for item in items:
        try:
            work_item_id = handler(item)""",
        """    try:
      for item in items:
        if True:
            work_item_id = handler(item)""",
        "B1",
        True,
    ),
    # M5 — the tag CHECK dropped from the migration.
    (
        "alembic/versions/arch38_step1_batches.py",
        '''    _check(
        "work_item_tags",
        "ck_work_item_tags_tag_format",
        f"tag ~ '{TAG_PATTERN}'",
    )''',
        "    pass  # tag CHECK removed",
        "T2",
        True,
    ),
    # M6 — has_document flipped on a trigger that has no document.
    (
        "app/services/automation/triggers.py",
        """        # refuses document actions for a trigger with has_document=False, so
        # this single flag is what keeps the builder honest.
        has_document=False,""",
        """        # refuses document actions for a trigger with has_document=False, so
        # this single flag is what keeps the builder honest.
        has_document=True,""",
        "B2",
        True,
    ),
    # M7 — the ingestion routers mounted after the catch-all.
    (
        "app/api/v1/router.py",
        '''    (ingestion.work_item_router, "/work-items",        "Work Items"),
    (work_items.router,        "/work-items",         "Work Items"),''',
        '''    (work_items.router,        "/work-items",         "Work Items"),
    (ingestion.work_item_router, "/work-items",        "Work Items"),''',
        "B4",
        True,
    ),
    # M8 — a job type registered with no profile: the fleet stops booting.
    (
        "app/workers/profiles.py",
        '''            "batch.expand_archive",
            "work_items.bulk",''',
        '''            "work_items.bulk",''',
        "B3",
        True,
    ),
    # M9 — a preset naming a redaction profile that does not exist.
    (
        "app/services/ingestion/preset_service.py",
        """    if profile not in PROFILE_KEYS:""",
        """    if False and profile not in PROFILE_KEYS:""",
        "P2",
        True,
    ),
    # M10 — BENIGN: a comment reworded. Nothing may die.
    (
        "app/services/ingestion/archive.py",
        "#: Ceiling on files in one archive.",
        "#: Upper bound on the number of files in one archive.",
        "Z4",
        False,
    ),
    # M11 — BENIGN: a limit made STRICTER. Nothing may die.
    (
        "app/services/ingestion/archive.py",
        "MAX_ENTRIES: Final[int] = 2_000",
        "MAX_ENTRIES: Final[int] = 1_999",
        "Z4",
        False,
    ),
)


def gates_mutation(rec: Recorder) -> None:
    workdir = Path(tempfile.mkdtemp(prefix="arch38-mutate-"))
    mirror = workdir / "backend"
    print(f"  (mirroring backend into {mirror})")
    shutil.copytree(
        BACKEND, mirror,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".git", "node_modules", "arch07_evidence",
            "arch08_evidence", "evidence", "stripe.exe",
        ),
    )
    (workdir / "frontend").mkdir(exist_ok=True)
    shutil.copytree(
        FRONTEND / "src", workdir / "frontend" / "src",
        ignore=shutil.ignore_patterns("node_modules"),
    )

    try:
        for index, (rel, find, replace, gate, must_kill) in enumerate(MUTATIONS, start=1):
            target = mirror / rel
            original = target.read_text(encoding="utf-8")
            if find not in original:
                rec.failed.append(
                    (f"M{index}", f"mutation anchor not found in {rel}")
                )
                print(f"  [FAIL] M{index} ({gate}): mutation anchor not found in {rel}")
                continue
            target.write_text(original.replace(find, replace, 1), encoding="utf-8")
            try:
                out = subprocess.run(
                    [sys.executable, str(HERE / "verify_arch38.py"), "--only", gate],
                    cwd=mirror, capture_output=True, text=True, timeout=600,
                    env={**os.environ, "ARCH38_MUTATION_ROOT": str(mirror)},
                )
                died = out.returncode != 0
            finally:
                target.write_text(original, encoding="utf-8")

            label = f"M{index} ({rel.split('/')[-1]}) -> {gate}"
            if must_kill and died:
                rec.passed += 1
                print(f"  [PASS] {label}: killed, as it must be")
            elif must_kill and not died:
                rec.failed.append((label, f"{gate} survived a real mutation"))
                print(f"  [FAIL] {label}: {gate} SURVIVED a real mutation")
            elif not must_kill and not died:
                rec.passed += 1
                print(f"  [PASS] {label}: benign edit survived")
            else:
                rec.failed.append((label, f"{gate} died on a benign edit"))
                print(f"  [FAIL] {label}: {gate} died on a BENIGN edit (harness is brittle)")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ===========================================================================
# Build gates
# ===========================================================================

BUILD_FILES: tuple[str, ...] = FRONTEND_NEW_FILES + tuple(FRONTEND_SENTINELS)


def gates_build(rec: Recorder) -> None:
    def run(name: str, args: list[str], must_contain: Optional[str] = None) -> None:
        out = subprocess.run(
            args, cwd=FRONTEND, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=(sys.platform == "win32"), timeout=1800
        )
        noise = ("TS5101", "ignoreDeprecations", "npm notice", "aka.ms")
        stderr = "\n".join(
            line for line in (out.stdout + out.stderr).splitlines()
            if line.strip() and not any(n in line for n in noise)
        )
        assert out.returncode == 0, f"{name} failed:\n{stderr[-2000:]}"
        if must_contain:
            assert must_contain in out.stdout, stderr[-2000:]

    rec.check(
        "BU1 tsc --noEmit is clean",
        lambda: run("tsc", ["npx", "tsc", "--noEmit", "-p", "tsconfig.json"]),
    )
    rec.check(
        "BU2 ESLint is clean on every ARCH-38 frontend file",
        lambda: run("eslint", ["npx", "eslint", *BUILD_FILES]),
    )
    rec.check(
        "BU3 vite build succeeds",
        lambda: run("vite build", ["npx", "vite", "build"]),
    )


# ===========================================================================
# Regression
# ===========================================================================


def gates_regression(rec: Recorder, *, with_db: bool) -> None:
    script = BACKEND / "verify_arch37.py"
    if not script.exists():
        return

    def run() -> None:
        args = [sys.executable, str(script)]
        if with_db:
            args.append("--db")
        out = subprocess.run(
            args, cwd=BACKEND, capture_output=True, text=True, timeout=2400
        )
        assert out.returncode == 0, out.stdout[-2500:] + out.stderr[-1500:]

    rec.check(
        "regression: verify_arch37.py"
        + (" --db" if with_db else "")
        + " (chains 39 -> 36 -> 35 -> 34 -> 33 -> 32 -> 31)",
        run,
    )


# ===========================================================================
# Main
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify ARCH-38.")
    parser.add_argument("--db", action="store_true", help="run live database gates")
    parser.add_argument("--mutate", action="store_true", help="run mutation kills")
    parser.add_argument("--build", action="store_true", help="run tsc, ESLint, vite build")
    parser.add_argument("--only", help="run a single offline gate (used by --mutate)")
    args = parser.parse_args()

    root = Path(os.environ.get("ARCH38_MUTATION_ROOT", str(BACKEND)))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    only = {args.only} if args.only else None

    print("ARCH-38 — Batch Ingestion & Universal Document Intelligence")
    print(f"backend: {root}")
    print()

    rec = Recorder()

    print("--- offline gates ---")
    gates_offline(rec, root=root, only=only)

    if args.only:
        print()
        print(f"  {rec.passed} passed, {len(rec.failed)} failed")
        return 1 if rec.failed else 0

    if args.db:
        print("\n--- database gates ---")
        gates_db(rec)

    if args.mutate:
        print("\n--- mutation gates ---")
        gates_mutation(rec)

    if args.build:
        print("\n--- build gates ---")
        gates_build(rec)

    print("\n--- regression chain ---")
    gates_regression(rec, with_db=args.db)

    print()
    print("=" * 72)
    print(f"  {rec.passed} passed, {len(rec.failed)} failed")
    if rec.failed:
        print("\nFailures:")
        for name, why in rec.failed:
            print(f"  - {name}: {why.splitlines()[0] if why else ''}")
        print("\nARCH-38: GATES FAILED")
        return 1
    print("\nARCH-38: ALL GATES PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
