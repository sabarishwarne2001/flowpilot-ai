"""ARCH-10 Steps 4-6 — unit tests."""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
import uuid
from typing import Any

import pytest

from app.core.storage.base import (
    InvalidStorageKeyError,
    StoredObject,
    sanitize_key,
)
from app.core.storage.keys import (
    StorageNamespace,
    TenantKeyError,
    assert_key_belongs_to,
    parse_key,
    tenant_key,
    tenant_prefix,
)
from app.core.storage.local import LocalStorageDriver
from app.core.storage.s3 import (
    MinIOStorageDriver,
    R2StorageDriver,
    S3CompatibleStorageDriver,
    S3StorageDriver,
)
from app.services.file_validation_service import (
    FileValidationError,
    RejectionReason,
    sniff_mime,
    spool_stream,
    validate_spooled,
)
from app.services.ocr.base import BoundingBox, OCRBlock, OCRPage, OCRResult
from app.services.ocr.paddle import PaddleOCRProvider


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}

    def upload_fileobj(self, fileobj, bucket, key, ExtraArgs=None, Config=None):
        self.calls.append(
            ("upload_fileobj", {"Key": key, "ExtraArgs": ExtraArgs or {}})
        )
        self.objects[key] = fileobj.read()

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "HeadObject",
            )
        return {"ContentLength": len(self.objects[Key])}

    def generate_presigned_url(self, op: str, Params: dict, ExpiresIn: int) -> str:
        return f"https://example.invalid/{Params['Key']}?op={op}&e={ExpiresIn}"


@pytest.fixture()
def local_driver(tmp_path: pathlib.Path) -> LocalStorageDriver:
    return LocalStorageDriver(root=tmp_path / "storage")


def make_pdf(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_jpeg_with_exif() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    image = Image.new("RGB", (32, 32), (10, 90, 160))
    exif = image.getexif()
    exif[271] = "TestCamera"
    exif[305] = "TestSoftware"
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def test_tenant_key_puts_the_organization_first():
    org = uuid.uuid4()
    key = tenant_key(
        organization_id=org,
        namespace=StorageNamespace.DOCUMENTS,
        file_id=uuid.uuid4(),
        suffix="pdf",
    )
    assert key.startswith(f"{org}/documents/")
    assert key.endswith(".pdf")
    assert sanitize_key(key) == key


def test_tenant_prefix_narrows_by_namespace():
    org = uuid.uuid4()
    assert tenant_prefix(organization_id=org) == f"{org}/"
    assert (
        tenant_prefix(organization_id=org, namespace=StorageNamespace.QUARANTINE)
        == f"{org}/quarantine/"
    )


def test_cross_tenant_key_is_refused():
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    key = tenant_key(
        organization_id=org_a,
        namespace=StorageNamespace.DOCUMENTS,
        file_id=uuid.uuid4(),
    )
    assert_key_belongs_to(key, org_a)
    with pytest.raises(TenantKeyError):
        assert_key_belongs_to(key, org_b)


@pytest.mark.parametrize(
    "bad",
    ["../etc/passwd", "documents/x", "/absolute/key", "not-a-uuid/documents/x"],
)
def test_malformed_keys_are_refused(bad):
    with pytest.raises((TenantKeyError, InvalidStorageKeyError)):
        parse_key(bad)


def test_non_uuid_organization_is_refused():
    with pytest.raises(TenantKeyError):
        tenant_key(
            organization_id="'; DROP TABLE users; --",
            namespace=StorageNamespace.DOCUMENTS,
            file_id=uuid.uuid4(),
        )


def test_put_stream_round_trip_and_checksum(local_driver):
    payload = os.urandom(2 * 1024 * 1024)
    key = "org/documents/file.bin".replace("org", str(uuid.uuid4()))

    stored = local_driver.put_stream(key, io.BytesIO(payload), "application/pdf")
    assert isinstance(stored, StoredObject)
    assert stored.size == len(payload)
    assert stored.checksum_sha256 == hashlib.sha256(payload).hexdigest()
    assert local_driver.get(key) == payload
    assert local_driver.checksum(key) == stored.checksum_sha256


def test_put_stream_trusts_a_supplied_checksum(local_driver):
    payload = b"x" * 1024
    key = f"{uuid.uuid4()}/documents/f.bin"
    stored = local_driver.put_stream(
        key,
        io.BytesIO(payload),
        "application/octet-stream",
        checksum_sha256="deadbeef",
    )
    assert stored.checksum_sha256 == "deadbeef"


def test_download_to_and_iter_chunks(local_driver):
    payload = os.urandom(300_000)
    key = f"{uuid.uuid4()}/documents/f.bin"
    local_driver.put_stream(key, io.BytesIO(payload), "application/octet-stream")

    sink = io.BytesIO()
    assert local_driver.download_to(key, sink) == len(payload)
    assert sink.getvalue() == payload
    assert b"".join(local_driver.iter_chunks(key)) == payload


def test_usage_bytes_counts_only_the_tenant_prefix(local_driver):
    org_a, org_b = uuid.uuid4(), uuid.uuid4()
    local_driver.put(f"{org_a}/documents/a.bin", b"a" * 100, "application/octet-stream")
    local_driver.put(f"{org_b}/documents/b.bin", b"b" * 250, "application/octet-stream")

    total_a, count_a = local_driver.usage_bytes(f"{org_a}/")
    assert (total_a, count_a) == (100, 1)


def test_local_driver_declares_no_presigning(local_driver):
    assert local_driver.supports_presigned is False
    assert local_driver.presigned_get_url("a/documents/b") is None


@pytest.mark.parametrize(
    ("cls", "kwargs", "expect_sse", "expect_backend"),
    [
        (S3StorageDriver, {}, True, "aws"),
        (
            R2StorageDriver,
            {"endpoint_url": "https://acct.r2.cloudflarestorage.com"},
            False,
            "r2",
        ),
        (MinIOStorageDriver, {"endpoint_url": "http://localhost:9000"}, False, "minio"),
    ],
)
def test_sse_header_is_flavour_dependent(cls, kwargs, expect_sse, expect_backend):
    client = FakeS3Client()
    driver = cls(bucket="b", client=client, **kwargs)
    driver.put("t/documents/x", b"data", "application/pdf")

    _, call = client.calls[0]
    assert ("ServerSideEncryption" in call) is expect_sse
    assert driver.backend_name == expect_backend


def test_r2_requires_an_endpoint():
    from app.core.storage.base import StorageError

    with pytest.raises(StorageError):
        R2StorageDriver(bucket="b", client=FakeS3Client())


def test_s3_put_stream_reports_multipart_above_the_threshold():
    client = FakeS3Client()
    driver = S3CompatibleStorageDriver(
        bucket="b", client=client, multipart_threshold=1024
    )
    stored = driver.put_stream(
        "t/documents/big.bin", io.BytesIO(b"z" * 4096), "application/octet-stream"
    )
    assert stored.multipart is True
    assert stored.size == 4096

    small = driver.put_stream(
        "t/documents/small.bin", io.BytesIO(b"z" * 16), "application/octet-stream"
    )
    assert small.multipart is False


def test_s3_prefix_is_applied_and_stripped():
    client = FakeS3Client()
    driver = S3CompatibleStorageDriver(bucket="b", client=client, prefix="env/prod")
    driver.put("org/documents/f.bin", b"x", "application/octet-stream")
    assert "env/prod/org/documents/f.bin" in client.objects


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"%PDF-1.7\n...", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF89a", "image/gif"),
        (b"II*\x00", "image/tiff"),
        (b"RIFF____WEBP", "image/webp"),
        (b"PK\x03\x04", "application/zip"),
        (b"hello world", None),
        (b"", None),
    ],
)
def test_sniff_mime(head, expected):
    assert sniff_mime(head) == expected


def test_pdf_with_leading_junk_is_still_a_pdf():
    assert sniff_mime(b"\x00" * 40 + b"%PDF-1.4") == "application/pdf"


def test_declared_type_disagreeing_with_bytes_is_refused():
    payload = b"PK\x03\x04" + b"\x00" * 256
    with pytest.raises(FileValidationError) as exc:
        validate_spooled(
            io.BytesIO(payload),
            len(payload),
            declared_mime="application/pdf",
            original_filename="invoice.pdf",
            allowed_mimes=["application/pdf"],
            max_pages=100,
        )
    assert exc.value.reason is RejectionReason.MIME_MISMATCH
    assert exc.value.should_quarantine


def test_unrecognised_bytes_are_refused():
    payload = b"just some text, honestly" * 20
    with pytest.raises(FileValidationError) as exc:
        validate_spooled(
            io.BytesIO(payload),
            len(payload),
            declared_mime="application/pdf",
            original_filename="x.pdf",
            allowed_mimes=["application/pdf"],
            max_pages=100,
        )
    assert exc.value.reason is RejectionReason.UNKNOWN_SIGNATURE


def test_disallowed_but_valid_type_is_refused_without_quarantine():
    payload = make_jpeg_with_exif()
    with pytest.raises(FileValidationError) as exc:
        validate_spooled(
            io.BytesIO(payload),
            len(payload),
            declared_mime="image/jpeg",
            original_filename="photo.jpg",
            allowed_mimes=["application/pdf"],
            max_pages=100,
        )
    assert exc.value.reason is RejectionReason.MIME_NOT_ALLOWED
    assert not exc.value.should_quarantine


def test_empty_upload_is_refused():
    with pytest.raises(FileValidationError) as exc:
        spool_stream(iter([]), max_bytes=1024)
    assert exc.value.reason is RejectionReason.EMPTY


def test_size_ceiling_stops_the_read_early():
    consumed = {"chunks": 0}

    def chunks():
        for _ in range(200):
            consumed["chunks"] += 1
            yield b"\x00" * 1024

    with pytest.raises(FileValidationError) as exc:
        spool_stream(chunks(), max_bytes=4096)
    assert exc.value.reason is RejectionReason.TOO_LARGE
    assert consumed["chunks"] <= 6


def test_exif_is_removed_and_hashed_after_the_scrub():
    original = make_jpeg_with_exif()
    assert b"TestCamera" in original

    with validate_spooled(
        io.BytesIO(original),
        len(original),
        declared_mime="image/jpeg",
        original_filename="photo.jpg",
        allowed_mimes=["image/jpeg"],
        max_pages=1,
    ) as validated:
        validated.handle.seek(0)
        cleaned = validated.handle.read()

        assert validated.scrubbed
        assert b"TestCamera" not in cleaned
        assert b"TestSoftware" not in cleaned
        assert validated.checksum_sha256 == hashlib.sha256(cleaned).hexdigest()
        assert validated.size == len(cleaned)
        assert validated.page_count == 1


def test_pdf_page_count_is_extracted():
    payload = make_pdf(9)
    with validate_spooled(
        io.BytesIO(payload),
        len(payload),
        declared_mime="application/pdf",
        original_filename="doc.pdf",
        allowed_mimes=["application/pdf"],
        max_pages=100,
    ) as validated:
        assert validated.page_count == 9


def test_page_bomb_is_refused():
    payload = make_pdf(15)
    with pytest.raises(FileValidationError) as exc:
        validate_spooled(
            io.BytesIO(payload),
            len(payload),
            declared_mime="application/pdf",
            original_filename="bomb.pdf",
            allowed_mimes=["application/pdf"],
            max_pages=10,
        )
    assert exc.value.reason is RejectionReason.TOO_MANY_PAGES
    assert exc.value.detail["page_count"] == 15


def test_octet_stream_declaration_defers_to_the_bytes():
    payload = make_pdf(2)
    with validate_spooled(
        io.BytesIO(payload),
        len(payload),
        declared_mime="application/octet-stream",
        original_filename="doc.pdf",
        allowed_mimes=["application/pdf"],
        max_pages=10,
    ) as validated:
        assert validated.mime_type == "application/pdf"


def test_only_ocr_applied_pages_are_billable():
    result = OCRResult(
        pages=[
            OCRPage(page_number=1, text="digital", ocr_applied=False),
            OCRPage(page_number=2, text="digital", ocr_applied=False),
            OCRPage(page_number=3, text="scan", ocr_applied=True),
        ],
        provider="paddleocr",
    )
    assert result.page_count == 3
    assert result.billable_pages == 1


def test_bounding_box_from_a_rotated_quadrilateral():
    box = BoundingBox.from_polygon([[10, 20], [90, 22], [92, 48], [12, 46]])
    assert (box.x0, box.y0, box.x1, box.y1) == (10.0, 20.0, 92.0, 48.0)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            [[[[[10, 20], [90, 22], [92, 48], [12, 46]], ("INVOICE", 0.98)]]],
            id="paddleocr-2.x",
        ),
        pytest.param(
            [
                {
                    "rec_texts": ["INVOICE"],
                    "rec_scores": [0.98],
                    "rec_polys": [[[10, 20], [90, 22], [92, 48], [12, 46]]],
                }
            ],
            id="paddleocr-3.x",
        ),
    ],
)
def test_blocks_normalise_from_both_api_shapes(raw):
    blocks = PaddleOCRProvider._blocks_from_raw(raw)
    assert len(blocks) == 1
    block = blocks[0]
    assert block.text == "INVOICE"
    assert block.confidence == pytest.approx(0.98)
    assert block.box is not None
    assert block.box.as_dict() == {"x0": 10.0, "y0": 20.0, "x1": 92.0, "y1": 48.0}


def test_blank_blocks_are_dropped():
    raw = [{"rec_texts": ["", "   ", "REAL"], "rec_scores": [0.1, 0.2, 0.9]}]
    blocks = PaddleOCRProvider._blocks_from_raw(raw)
    assert [b.text for b in blocks] == ["REAL"]


def test_provider_declares_supported_types():
    provider = PaddleOCRProvider()
    assert provider.supports("application/pdf")
    assert provider.supports("image/png")
    assert not provider.supports("text/csv")
    assert provider.cost_micros_per_page == 0
    assert provider.estimated_cost_micros(500) == 0


def test_register_all_is_idempotent():
    from app.services import job_service
    from app.workers.handlers import ARCH10_JOB_TYPES, register_all

    register_all()
    assert ARCH10_JOB_TYPES <= set(job_service.JOB_HANDLERS)
    assert register_all() == []


def test_registration_does_not_import_paddleocr():
    import sys
    from app.workers.handlers import register_all

    register_all()
    leaked = [
        name
        for name in ("paddleocr", "paddle", "torch", "chromadb")
        if name in sys.modules
    ]
    assert not leaked, f"heavy modules pulled in by registration: {leaked}"