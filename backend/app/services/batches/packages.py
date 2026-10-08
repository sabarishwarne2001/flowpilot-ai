"""Export packages: a batch's documents and data as one zip a compliance team can prove later.

LAYOUT (every path under one folder named after the package)

    README.txt                    what the package is, how to verify it, the manifest's SHA-256
    manifest.json                 package, workspace, batch, every document, every file with its SHA-256
    SHA256SUMS                    `sha256sum -c` format: every file above and below, except itself
    data/extractions.json         one record per document: fields, summary, confidence, lane, healing
    data/extractions.csv          the same, one row per document, one column per field (formula-safe)
    data/documents/NNN-name.json  one document's record on its own
    files/NNN-name.ext            the original file, when the package includes originals

WHY THREE LAYERS OF CHECKSUM

`SHA256SUMS` lets anyone check the files with standard tools and no FlowPilot account. It is not
enough on its own: someone who edits a file can regenerate it. So the manifest's own SHA-256 is
recorded in FlowPilot when the package is built (and printed in README.txt): verification in the
app compares the package's manifest with that record, which an editor of the zip cannot change.
And each original file is checked against the SHA-256 recorded when it was uploaded, so the
package proves the file is the one that was received, not just the one that was exported.

The zip is deterministic: entries in a fixed order with the package's creation time as their
timestamp, so the same documents produce the same files.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, BinaryIO, Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.batches import (
    PACKAGE_BUILDING,
    PACKAGE_EXPIRED,
    PACKAGE_FAILED,
    PACKAGE_QUEUED,
    PACKAGE_READY,
    ExportPackage,
    ProcessingBatch,
    ProcessingBatchItem,
)
from app.models.uploaded_file import UploadedFile
from app.models.work_item import WorkItem
from app.services.batches import canonical, confidence as conf, healing
from app.services.batches import vocabulary as v

_CHUNK = 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


class PackageError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def safe_name(name: str, limit: int = 100) -> str:
    stem, dot, ext = (name or "document").rpartition(".")
    if not dot:
        stem, ext = name or "document", ""
    stem = _SAFE.sub("_", stem).strip(" ._") or "document"
    ext = _SAFE.sub("", ext)[:10]
    stem = stem[: max(1, limit - len(ext) - 1)]
    return f"{stem}.{ext}" if ext else stem


def folder_name(package: ExportPackage) -> str:
    stamp = (package.created_at or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return f"{safe_name(package.name, 60).replace(' ', '-')}-{stamp}-{str(package.id)[:8]}"


# ------------------------------------------------------------------ requesting

def request_package(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    organization_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    name: str,
    batch: Optional[ProcessingBatch] = None,
    work_item_ids: Sequence[uuid.UUID] = (),
    include_originals: bool = True,
) -> ExportPackage:
    """Record a package request and queue its build. The documents are fixed now."""
    from app.services import job_service

    ids: list[uuid.UUID] = []
    if batch is not None:
        ids.extend(
            db.execute(
                select(ProcessingBatchItem.work_item_id)
                .where(ProcessingBatchItem.batch_id == batch.id)
                .order_by(ProcessingBatchItem.added_at)
            ).scalars()
        )
    ids.extend(work_item_ids)
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise PackageError("NO_DOCUMENTS", "Choose at least one document to export.")
    if len(ids) > v.MAX_PACKAGE_DOCUMENTS:
        raise PackageError("TOO_MANY_DOCUMENTS", f"A package covers at most {v.MAX_PACKAGE_DOCUMENTS} documents.")
    known = set(
        db.execute(select(WorkItem.id).where(WorkItem.workspace_id == workspace_id, WorkItem.id.in_(ids))).scalars()
    )
    if len(known) != len(ids):
        raise PackageError("UNKNOWN_DOCUMENTS", "Some of the documents are not in this workspace.", 404)
    label = " ".join((name or "").split()) or (f"{batch.name} export" if batch else "Document export")
    package = ExportPackage(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        organization_id=organization_id,
        batch_id=batch.id if batch else None,
        name=label[:160],
        status=PACKAGE_QUEUED,
        include_originals=include_originals,
        work_item_ids=[str(i) for i in ids],
        document_count=len(ids),
        created_by_user_id=user_id,
    )
    db.add(package)
    db.flush()
    job_service.enqueue(
        db,
        job_type=v.JOB_BUILD_PACKAGE,
        payload={"package_id": str(package.id)},
        organization_id=organization_id,
        idempotency_key=f"{v.JOB_BUILD_PACKAGE}:{package.id}",
        max_attempts=3,
    )
    return package


# ------------------------------------------------------------------ building

@dataclass
class _Entry:
    path: str
    sha256: str
    size: int


class _Writer:
    """Writes entries into a deterministic zip and hashes each one as it goes."""

    def __init__(self, handle: BinaryIO, root: str, when: datetime) -> None:
        self.zip = zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True)
        self.root = root
        self.when = when.astimezone(timezone.utc).timetuple()[:6]
        self.entries: list[_Entry] = []

    def _info(self, path: str) -> zipfile.ZipInfo:
        info = zipfile.ZipInfo(f"{self.root}/{path}", date_time=self.when)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        return info

    def write_bytes(self, path: str, data: bytes) -> _Entry:
        self.zip.writestr(self._info(path), data)
        entry = _Entry(path, hashlib.sha256(data).hexdigest(), len(data))
        self.entries.append(entry)
        return entry

    def write_stream(self, path: str, chunks: Iterable[bytes]) -> _Entry:
        digest = hashlib.sha256()
        size = 0
        with self.zip.open(self._info(path), "w", force_zip64=True) as out:
            for chunk in chunks:
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
        entry = _Entry(path, digest.hexdigest(), size)
        self.entries.append(entry)
        return entry

    def close(self) -> None:
        self.zip.close()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False, default=str) + "\n").encode("utf-8")


def _csv_bytes(records: list[dict[str, Any]]) -> bytes:
    from app.services.tables.export import safe_text

    base = ["work_item_id", "filename", "document_type", "status", "lane", "confidence", "source_sha256"]
    field_names: list[str] = []
    for record in records:
        for key in record["fields"]:
            if key not in field_names:
                field_names.append(key)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(base + [f"field.{name}" for name in field_names])
    for record in records:
        row: list[Any] = [
            record["work_item_id"], record["filename"], record["document_type"] or "", record["status"],
            record["lane"] or "", "" if record["confidence"] is None else record["confidence"],
            record["source_sha256"] or "",
        ]
        for name in field_names:
            value = record["fields"].get(name)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            row.append("" if value is None else value)
        writer.writerow([safe_text(cell) if isinstance(cell, str) else cell for cell in row])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def _readme(package: ExportPackage, manifest_sha256: str, documents: int, files: int, workspace: str) -> bytes:
    lines = [
        "FLOWPILOT AI EXPORT PACKAGE",
        "===========================",
        "",
        f"Package:        {package.name}",
        f"Package ID:     {package.id}",
        f"Workspace:      {workspace}",
        f"Created (UTC):  {(package.created_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()}",
        f"Documents:      {documents}",
        f"Files:          {files} (listed in manifest.json with their SHA-256)",
        "",
        "INTEGRITY",
        "---------",
        f"manifest.json SHA-256: {manifest_sha256}",
        "",
        "FlowPilot recorded this manifest SHA-256 when it built the package. If the package is",
        "changed and SHA256SUMS regenerated to match, the manifest no longer has this digest.",
        "",
        "To check every file with standard tools, from inside this folder:",
        "  Linux:    sha256sum -c SHA256SUMS",
        "  macOS:    shasum -a 256 -c SHA256SUMS",
        "  Windows:  Get-FileHash -Algorithm SHA256 <file>   (compare with SHA256SUMS)",
        "",
        "To check it against FlowPilot's record: Batch operations -> Verify a package, and upload",
        "this zip unchanged.",
        "",
        "Each original file under files/ is also checked against the SHA-256 FlowPilot recorded when",
        "it was uploaded (manifest.json, documents[].source_sha256 and source_verified).",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _records(db: Session, package: ExportPackage, items: list[WorkItem]) -> list[dict[str, Any]]:
    lanes: dict[uuid.UUID, tuple[Optional[str], list[Any]]] = {}
    if package.batch_id:
        for membership in db.execute(
            select(ProcessingBatchItem).where(ProcessingBatchItem.batch_id == package.batch_id)
        ).scalars():
            lanes[membership.work_item_id] = (membership.lane, list(membership.lane_reasons or []))
    scores = conf.for_documents(db, [item.id for item in items])
    schemas: dict[str, Any] = {}
    records = []
    for item in items:
        entities = item.extracted_entities if isinstance(item.extracted_entities, dict) else {}
        document_type = canonical.document_type_of(entities)
        schema = canonical.schema_for(
            db, workspace_id=package.workspace_id, organization_id=package.organization_id,
            document_type=document_type, _cache=schemas,
        )
        plan = healing.plan(entities, schema)
        score = scores.get(item.id)
        lane, reasons = lanes.get(item.id, (None, []))
        records.append({
            "work_item_id": str(item.id),
            "filename": item.original_filename,
            "document_type": document_type,
            "status": item.status,
            "uploaded_at": item.created_at.isoformat() if item.created_at else None,
            "page_count": item.page_count,
            "fields": {k: val for k, val in entities.items() if k not in healing.RESERVED_KEYS},
            "summary": item.summary,
            "confidence": score.confidence if score else None,
            "verification_status": score.verification_status if score else None,
            "field_confidence": [
                {"field": f.field, "confidence": f.confidence, "agreed": f.agreed} for f in (score.fields if score else ())
            ],
            "lane": lane,
            "lane_reasons": reasons,
            "schema": {"key": plan.schema_key, "state": plan.state, "missing_required": plan.missing_required},
            "source_sha256": None,
            "source_verified": None,
        })
    return records


def build(db: Session, *, package: ExportPackage) -> ExportPackage:
    """Build the archive and store it. Sets READY or FAILED; never raises for a data problem."""
    from app.core.storage import StorageNamespace, get_storage_driver, tenant_key
    from app.models.workspace import Workspace

    if package.status not in (PACKAGE_QUEUED, PACKAGE_BUILDING):
        return package
    package.status = PACKAGE_BUILDING
    db.flush()

    ids = [uuid.UUID(str(i)) for i in package.work_item_ids]
    rows = {
        item.id: item
        for item in db.execute(
            select(WorkItem).where(WorkItem.workspace_id == package.workspace_id, WorkItem.id.in_(ids))
        ).scalars()
    }
    items = [rows[i] for i in ids if i in rows]
    missing = [str(i) for i in ids if i not in rows]
    if not items:
        return fail(package, "NO_DOCUMENTS", "Every document in the package was deleted before it was built.")
    files = {
        f.id: f
        for f in db.execute(
            select(UploadedFile).where(UploadedFile.id.in_([i.uploaded_file_id for i in items if i.uploaded_file_id]))
        ).scalars()
    }
    if package.include_originals:
        total = sum(int(files[i.uploaded_file_id].file_size or 0) for i in items if i.uploaded_file_id in files)
        if total > v.MAX_PACKAGE_BYTES:
            return fail(
                package, "TOO_LARGE",
                f"The original files total {total // (1024 * 1024)} MB; a package holds at most "
                f"{v.MAX_PACKAGE_BYTES // (1024 * 1024)} MB. Export without originals, or split the batch.",
            )
    workspace = db.get(Workspace, package.workspace_id)
    batch = db.get(ProcessingBatch, package.batch_id) if package.batch_id else None
    records = _records(db, package, items)
    driver = get_storage_driver()
    root = folder_name(package)
    when = package.created_at or datetime.now(timezone.utc)

    with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as handle:
        writer = _Writer(handle, root, when)
        documents_manifest = []
        try:
            for index, (item, record) in enumerate(zip(items, records), start=1):
                prefix = f"{index:04d}-{safe_name(item.original_filename)}"
                stem = prefix.rsplit(".", 1)[0]
                file_path = None
                if package.include_originals and item.stored_filename:
                    uploaded = files.get(item.uploaded_file_id) if item.uploaded_file_id else None
                    entry = writer.write_stream(f"files/{prefix}", driver.iter_chunks(item.stored_filename, chunk_size=_CHUNK))
                    file_path = entry.path
                    record["source_sha256"] = uploaded.checksum_sha256 if uploaded else None
                    record["source_verified"] = (
                        uploaded.checksum_sha256.lower() == entry.sha256 if uploaded and uploaded.checksum_sha256 else None
                    )
                data_entry = writer.write_bytes(f"data/documents/{stem}.json", _json_bytes(record))
                documents_manifest.append({
                    "work_item_id": record["work_item_id"],
                    "original_filename": item.original_filename,
                    "data_path": data_entry.path,
                    "file_path": file_path,
                    "source_sha256": record["source_sha256"],
                    "source_verified": record["source_verified"],
                })
            writer.write_bytes("data/extractions.json", _json_bytes(records))
            writer.write_bytes("data/extractions.csv", _csv_bytes(records))
            manifest = {
                "format": v.PACKAGE_FORMAT,
                "package_id": str(package.id),
                "name": package.name,
                "created_at": when.astimezone(timezone.utc).isoformat(),
                "organization_id": str(package.organization_id),
                "workspace": {"id": str(package.workspace_id), "name": workspace.workspace_name if workspace else None},
                "batch": {"id": str(batch.id), "name": batch.name} if batch else None,
                "include_originals": package.include_originals,
                "algorithm": "SHA-256",
                "document_count": len(items),
                "documents_missing_at_build": missing,
                "documents": documents_manifest,
                "files": [{"path": e.path, "sha256": e.sha256, "bytes": e.size} for e in writer.entries],
            }
            manifest_bytes = _json_bytes(manifest)
            manifest_entry = writer.write_bytes("manifest.json", manifest_bytes)
            writer.write_bytes(
                "README.txt",
                _readme(package, manifest_entry.sha256, len(items), len(manifest["files"]),
                        workspace.workspace_name if workspace else str(package.workspace_id)),
            )
            sums = "".join(f"{e.sha256}  {e.path}\n" for e in writer.entries).encode("utf-8")
            writer.write_bytes("SHA256SUMS", sums)
        finally:
            writer.close()

        size = handle.tell()
        handle.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
        handle.seek(0)
        key = tenant_key(
            organization_id=package.organization_id,
            namespace=StorageNamespace.EXPORTS,
            file_id=package.id,
            suffix="zip",
        )
        stored = driver.put_stream(key, handle, "application/zip", content_length=size, checksum_sha256=digest.hexdigest())

    package.status = PACKAGE_READY
    package.storage_key = stored.key
    package.size_bytes = size
    package.package_sha256 = digest.hexdigest()
    package.manifest_sha256 = manifest_entry.sha256
    package.manifest = manifest["files"]
    package.file_count = len(manifest["files"])
    package.document_count = len(items)
    package.completed_at = datetime.now(timezone.utc)
    package.expires_at = package.completed_at + timedelta(days=v.PACKAGE_TTL_DAYS)
    db.flush()
    return package


def fail(package: ExportPackage, code: str, detail: str) -> ExportPackage:
    package.status = PACKAGE_FAILED
    package.error_code = code
    package.error_detail = detail[:2000]
    package.completed_at = datetime.now(timezone.utc)
    return package


def expire(db: Session, *, now: Optional[datetime] = None) -> int:
    """Delete the archives of packages past their expiry and mark them EXPIRED."""
    from app.core.storage import StorageError, get_storage_driver

    moment = now or datetime.now(timezone.utc)
    driver = get_storage_driver()
    count = 0
    for package in db.execute(
        select(ExportPackage).where(ExportPackage.status == PACKAGE_READY, ExportPackage.expires_at <= moment)
    ).scalars():
        try:
            if package.storage_key:
                driver.delete(package.storage_key)
        except StorageError:
            continue
        package.status = PACKAGE_EXPIRED
        package.storage_key = None  # the digests stay: they still prove what was issued
        count += 1
    db.flush()
    return count


# ------------------------------------------------------------------ verifying

@dataclass(frozen=True)
class FileCheck:
    path: str
    expected: Optional[str]
    actual: Optional[str]
    status: str  # OK | MODIFIED | MISSING | UNEXPECTED


def _parse_sums(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]?(.+)", line.strip())
        if match:
            out[match.group(2).strip()] = match.group(1).lower()
    return out


def verify(db: Session, *, workspace_id: uuid.UUID, handle: BinaryIO) -> dict[str, Any]:
    """Check an uploaded package against its own checksums and against FlowPilot's record of it."""
    handle.seek(0)
    archive_digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(_CHUNK), b""):
        archive_digest.update(chunk)
    handle.seek(0)
    invalid = {
        "verdict": "INVALID", "package_id": None, "package_name": None, "issued_at": None,
        "files_checked": 0, "files_ok": 0, "problems": [], "manifest_sha256": None,
        "recorded_manifest_sha256": None, "archive_sha256": archive_digest.hexdigest(),
        "archive_matches_record": None, "manifest_matches_record": None,
    }
    try:
        archive = zipfile.ZipFile(handle)
    except zipfile.BadZipFile:
        return {**invalid, "message": "This file is not a zip archive."}
    with archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) > v.VERIFY_MAX_ENTRIES or sum(i.file_size for i in infos) > v.VERIFY_MAX_UNCOMPRESSED_BYTES:
            return {**invalid, "message": "This archive is too large to verify here."}
        names = {i.filename for i in infos}
        manifest_names = sorted(n for n in names if n == "manifest.json" or n.endswith("/manifest.json"))
        if not manifest_names:
            return {**invalid, "message": "This is not a FlowPilot export package: it has no manifest.json."}
        manifest_name = min(manifest_names, key=len)
        root = manifest_name[: -len("manifest.json")]
        sums_name = f"{root}SHA256SUMS"
        if sums_name not in names:
            return {**invalid, "message": "This is not a FlowPilot export package: it has no SHA256SUMS."}
        try:
            manifest_bytes = archive.read(manifest_name)
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            sums = _parse_sums(archive.read(sums_name).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, KeyError):
            return {**invalid, "message": "The package's manifest or SHA256SUMS cannot be read."}
        if manifest.get("format") != v.PACKAGE_FORMAT:
            return {**invalid, "message": "This is not a FlowPilot export package (unknown format)."}

        checks: list[FileCheck] = []
        relative = {n[len(root):]: n for n in names if n.startswith(root) and n != sums_name}
        for path, expected in sorted(sums.items()):
            name = relative.get(path)
            if name is None:
                checks.append(FileCheck(path, expected, None, "MISSING"))
                continue
            digest = hashlib.sha256()
            with archive.open(name) as stream:
                for chunk in iter(lambda: stream.read(_CHUNK), b""):
                    digest.update(chunk)
            actual = digest.hexdigest()
            checks.append(FileCheck(path, expected, actual, "OK" if actual == expected else "MODIFIED"))
        for path in sorted(set(relative) - set(sums)):
            checks.append(FileCheck(path, None, None, "UNEXPECTED"))
        listed = {f.get("path"): f.get("sha256") for f in manifest.get("files") or [] if isinstance(f, dict)}
        for path, expected in listed.items():
            if sums.get(path) != expected:
                checks.append(FileCheck(str(path), expected, sums.get(path), "MODIFIED"))

    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    package = None
    try:
        package_id = uuid.UUID(str(manifest.get("package_id")))
        package = db.execute(
            select(ExportPackage).where(ExportPackage.id == package_id, ExportPackage.workspace_id == workspace_id)
        ).scalar_one_or_none()
    except ValueError:
        package_id = None
    problems = [c for c in checks if c.status != "OK"]
    if problems:
        verdict = "TAMPERED"
    elif package is None:
        verdict = "UNRECOGNISED"
    elif package.manifest_sha256 != manifest_sha256:
        verdict = "TAMPERED"
    else:
        verdict = "VERIFIED"
    messages = {
        "VERIFIED": "Every file matches, and the manifest is the one FlowPilot issued.",
        "TAMPERED": "This package was changed after FlowPilot issued it.",
        "UNRECOGNISED": "The files match their checksums, but this workspace never issued this package.",
    }
    return {
        "verdict": verdict,
        "message": messages[verdict] if not (verdict == "TAMPERED" and not problems) else
        "The files match a regenerated SHA256SUMS, but the manifest is not the one FlowPilot issued.",
        "package_id": str(package_id) if package_id else None,
        "package_name": manifest.get("name"),
        "issued_at": manifest.get("created_at"),
        "files_checked": len(checks),
        "files_ok": sum(1 for c in checks if c.status == "OK"),
        "problems": [c.__dict__ for c in problems[:200]],
        "manifest_sha256": manifest_sha256,
        "recorded_manifest_sha256": package.manifest_sha256 if package else None,
        "archive_sha256": archive_digest.hexdigest(),
        "archive_matches_record": (package.package_sha256 == archive_digest.hexdigest()) if package else None,
        "manifest_matches_record": (package.manifest_sha256 == manifest_sha256) if package else None,
    }


__all__ = [
    "PackageError",
    "build",
    "expire",
    "fail",
    "folder_name",
    "request_package",
    "safe_name",
    "verify",
]
