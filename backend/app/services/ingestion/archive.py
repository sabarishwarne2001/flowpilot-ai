"""ARCH-38 — server-side archive expansion, with limits.

A zip is untrusted input from an authenticated tenant, which is the most
dangerous shape of input there is: the caller is allowed to be here, so nothing
upstream is suspicious of them.

This module is pure. It takes bytes and returns entries or raises, and imports
no session, no model and no service, so the gates can exercise every limit
without a database. `expand_archive` in the worker is the only caller that
touches storage.

THE FIVE LIMITS, AND WHY EACH ONE EXISTS SEPARATELY
===================================================

* **Total expanded bytes.** A 40 KB archive can hold 4 GB. The entry count and
  the ratio both miss the single enormous entry.
* **Entry count.** Ten thousand one-byte files pass a ratio check and a size
  check, and still cost ten thousand rows, ten thousand jobs and ten thousand
  OCR attempts.
* **Compression ratio.** The classic bomb: small archive, large content, few
  entries. Checked per entry *and* in aggregate, because forty entries at 99:1
  each individually look ordinary against a global budget.
* **Path safety.** `../` and absolute paths escape the extraction root. Nothing
  here writes to a filesystem, but an entry name also becomes a filename in the
  UI and an object key suffix, and a name that traverses is never legitimate.
* **Nested archives.** Depth 1 means: this archive, and no archive inside it.
  Recursive expansion is how a bounded expander becomes unbounded.

Every limit is evaluated *before* any entry is read to completion, using the
directory's declared sizes, and then again against what was actually read --
a zip directory can lie.
"""

from __future__ import annotations

import io
import posixpath
import zipfile
from dataclasses import dataclass
from typing import Final, Iterator, Optional

#: Aggregate ceiling on what one archive may expand to.
MAX_TOTAL_EXPANDED_BYTES: Final[int] = 512 * 1024 * 1024

#: Ceiling on files in one archive.
MAX_ENTRIES: Final[int] = 2_000

#: uncompressed / compressed. Text and scanned PDFs sit far below this; a
#: zip bomb sits orders of magnitude above it.
MAX_COMPRESSION_RATIO: Final[float] = 100.0

#: 1 = this archive only. An archive inside it is refused, not expanded.
MAX_ARCHIVE_DEPTH: Final[int] = 1

#: A single entry may not exceed this, whatever the aggregate budget allows.
MAX_ENTRY_BYTES: Final[int] = 128 * 1024 * 1024

#: Entries smaller than this are exempt from the per-entry ratio test. A 12-byte
#: file that compresses to 11 bytes has a meaningless ratio, and an empty file
#: has an undefined one.
RATIO_FLOOR_BYTES: Final[int] = 4096

#: What a member may be. Mirrors the upload allowlist: an archive is a delivery
#: mechanism, not a way to widen what the product accepts.
ALLOWED_MEMBER_MIMES: Final[frozenset[str]] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/webp",
        "image/bmp",
    }
)

_SUFFIX_TO_MIME: Final[dict[str, str]] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

#: Suffixes that make an entry a nested archive. Checked by name and by magic
#: bytes, because a bomb renames itself.
_ARCHIVE_SUFFIXES: Final[frozenset[str]] = frozenset(
    {".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".tar"}
)

_ARCHIVE_MAGIC: Final[tuple[bytes, ...]] = (
    b"PK\x03\x04",  # zip
    b"PK\x05\x06",  # empty zip
    b"\x1f\x8b",  # gzip
    b"BZh",  # bzip2
    b"\xfd7zXZ",  # xz
    b"7z\xbc\xaf\x27\x1c",  # 7z
    b"Rar!",  # rar
)


class ArchiveRejected(Exception):
    """An archive, or one of its entries, violated a limit.

    `code` is what is stored in `ingestion_batch_items.error_code` and shown in
    the failures list, so it is a stable vocabulary rather than prose.
    """

    def __init__(self, code: str, message: str, *, entry: Optional[str] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.entry = entry

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.entry:
            return f"{self.code}: {self.message} ({self.entry})"
        return f"{self.code}: {self.message}"


REJECTION_CODES: Final[tuple[str, ...]] = (
    "ARCHIVE_NOT_A_ZIP",
    "ARCHIVE_TOO_MANY_ENTRIES",
    "ARCHIVE_TOO_LARGE",
    "ARCHIVE_ENTRY_TOO_LARGE",
    "ARCHIVE_COMPRESSION_RATIO",
    "ARCHIVE_UNSAFE_PATH",
    "ARCHIVE_NESTED",
    "ARCHIVE_DEPTH_EXCEEDED",
    "ARCHIVE_MEMBER_NOT_ALLOWED",
    "ARCHIVE_EMPTY",
    "ARCHIVE_DIRECTORY_LIES",
)


@dataclass(frozen=True)
class ArchiveEntry:
    """One member that passed every limit."""

    name: str
    data: bytes
    mime_type: str

    @property
    def size(self) -> int:
        return len(self.data)


def is_archive_name(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES)


def looks_like_archive(head: bytes) -> bool:
    return any(head.startswith(magic) for magic in _ARCHIVE_MAGIC)


def safe_member_name(raw: str) -> str:
    """Return the entry's name, or raise if it could escape its root.

    Refused, in order: a backslash separator (a Windows-authored archive that
    would resolve differently on POSIX), a drive letter, an absolute path, and
    any `..` segment. The normalised result is re-checked, so `a/../../b` is
    caught after normalisation as well as before.
    """
    if not raw or raw.strip() == "":
        raise ArchiveRejected("ARCHIVE_UNSAFE_PATH", "An entry has no name.", entry=raw)
    if "\x00" in raw:
        raise ArchiveRejected(
            "ARCHIVE_UNSAFE_PATH", "An entry name contains NUL.", entry=raw
        )

    candidate = raw.replace("\\", "/")

    if candidate.startswith("/"):
        raise ArchiveRejected(
            "ARCHIVE_UNSAFE_PATH",
            "An entry uses an absolute path.",
            entry=raw,
        )
    if len(candidate) >= 2 and candidate[1] == ":":
        raise ArchiveRejected(
            "ARCHIVE_UNSAFE_PATH",
            "An entry uses a drive-letter path.",
            entry=raw,
        )

    segments = candidate.split("/")
    if any(segment == ".." for segment in segments):
        raise ArchiveRejected(
            "ARCHIVE_UNSAFE_PATH",
            "An entry name traverses outside the archive.",
            entry=raw,
        )

    normalised = posixpath.normpath(candidate)
    if normalised.startswith("/") or normalised == ".." or normalised.startswith("../"):
        raise ArchiveRejected(
            "ARCHIVE_UNSAFE_PATH",
            "An entry name traverses outside the archive after normalisation.",
            entry=raw,
        )
    return normalised


def member_mime(name: str) -> str:
    """The MIME for an allowed member, or raise."""
    lowered = name.lower()
    for suffix, mime in _SUFFIX_TO_MIME.items():
        if lowered.endswith(suffix):
            return mime
    raise ArchiveRejected(
        "ARCHIVE_MEMBER_NOT_ALLOWED",
        "This file type is not accepted inside an archive.",
        entry=name,
    )


def _ratio(uncompressed: int, compressed: int) -> float:
    if compressed <= 0:
        # A zero-length compressed stream that declares content is the purest
        # form of the bomb; treat it as unbounded rather than dividing by zero.
        return float("inf") if uncompressed > 0 else 0.0
    return uncompressed / compressed


def inspect(
    data: bytes, *, depth: int = 1
) -> list[zipfile.ZipInfo]:
    """Validate the archive's directory without decompressing anything.

    Separated from `expand` so the cheap refusals happen before a single byte
    is inflated: that is the whole defence against a bomb, and doing it after
    extraction would mean the bomb had already run.
    """
    if depth > MAX_ARCHIVE_DEPTH:
        raise ArchiveRejected(
            "ARCHIVE_DEPTH_EXCEEDED",
            f"Archives nested more than {MAX_ARCHIVE_DEPTH} deep are refused.",
        )

    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise ArchiveRejected(
            "ARCHIVE_NOT_A_ZIP", "The archive is not a readable zip file."
        )

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]

    if not infos:
        raise ArchiveRejected("ARCHIVE_EMPTY", "The archive contains no files.")

    if len(infos) > MAX_ENTRIES:
        raise ArchiveRejected(
            "ARCHIVE_TOO_MANY_ENTRIES",
            f"The archive holds {len(infos)} files; the limit is {MAX_ENTRIES}.",
        )

    declared_total = 0
    compressed_total = 0
    for info in infos:
        name = safe_member_name(info.filename)

        if is_archive_name(name):
            raise ArchiveRejected(
                "ARCHIVE_NESTED",
                "The archive contains another archive.",
                entry=name,
            )

        if info.file_size > MAX_ENTRY_BYTES:
            raise ArchiveRejected(
                "ARCHIVE_ENTRY_TOO_LARGE",
                f"{name} expands to {info.file_size} bytes; the per-file limit "
                f"is {MAX_ENTRY_BYTES}.",
                entry=name,
            )

        if (
            info.file_size >= RATIO_FLOOR_BYTES
            and _ratio(info.file_size, info.compress_size) > MAX_COMPRESSION_RATIO
        ):
            raise ArchiveRejected(
                "ARCHIVE_COMPRESSION_RATIO",
                f"{name} expands {_ratio(info.file_size, info.compress_size):.0f}x, "
                f"above the {MAX_COMPRESSION_RATIO:.0f}x limit.",
                entry=name,
            )

        declared_total += info.file_size
        compressed_total += info.compress_size

        if declared_total > MAX_TOTAL_EXPANDED_BYTES:
            raise ArchiveRejected(
                "ARCHIVE_TOO_LARGE",
                f"The archive expands to more than {MAX_TOTAL_EXPANDED_BYTES} "
                "bytes.",
            )

    # Forty entries at 99:1 each pass the per-entry test and are still a bomb
    # in aggregate.
    if (
        declared_total >= RATIO_FLOOR_BYTES
        and _ratio(declared_total, compressed_total) > MAX_COMPRESSION_RATIO
    ):
        raise ArchiveRejected(
            "ARCHIVE_COMPRESSION_RATIO",
            f"The archive expands "
            f"{_ratio(declared_total, compressed_total):.0f}x in aggregate, "
            f"above the {MAX_COMPRESSION_RATIO:.0f}x limit.",
        )

    return infos


def expand(data: bytes, *, depth: int = 1) -> Iterator[ArchiveEntry]:
    """Yield the archive's members, enforcing every limit.

    `inspect` has already refused the archive on its declared sizes. This
    re-checks against what was actually read, because a zip directory can
    declare 1 KB and deliver 1 GB, and `ZipFile.read` honours the stream, not
    the directory.
    """
    infos = inspect(data, depth=depth)

    actual_total = 0
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in infos:
            name = safe_member_name(info.filename)
            mime = member_mime(name)

            with archive.open(info, "r") as member:
                # Read one byte past the entry ceiling: if it arrives, the
                # directory lied and the read stops there rather than
                # continuing to fill memory.
                payload = member.read(MAX_ENTRY_BYTES + 1)

            if len(payload) > MAX_ENTRY_BYTES:
                raise ArchiveRejected(
                    "ARCHIVE_DIRECTORY_LIES",
                    f"{name} delivered more bytes than its directory entry "
                    "declared.",
                    entry=name,
                )

            if len(payload) != info.file_size:
                raise ArchiveRejected(
                    "ARCHIVE_DIRECTORY_LIES",
                    f"{name} declared {info.file_size} bytes and delivered "
                    f"{len(payload)}.",
                    entry=name,
                )

            if looks_like_archive(payload[:8]):
                raise ArchiveRejected(
                    "ARCHIVE_NESTED",
                    "An entry is an archive under another extension.",
                    entry=name,
                )

            actual_total += len(payload)
            if actual_total > MAX_TOTAL_EXPANDED_BYTES:
                raise ArchiveRejected(
                    "ARCHIVE_TOO_LARGE",
                    "The archive delivered more than "
                    f"{MAX_TOTAL_EXPANDED_BYTES} bytes.",
                )

            yield ArchiveEntry(
                name=posixpath.basename(name) or name,
                data=payload,
                mime_type=mime,
            )


__all__ = [
    "ALLOWED_MEMBER_MIMES",
    "ArchiveEntry",
    "ArchiveRejected",
    "MAX_ARCHIVE_DEPTH",
    "MAX_COMPRESSION_RATIO",
    "MAX_ENTRIES",
    "MAX_ENTRY_BYTES",
    "MAX_TOTAL_EXPANDED_BYTES",
    "REJECTION_CODES",
    "expand",
    "inspect",
    "is_archive_name",
    "looks_like_archive",
    "member_mime",
    "safe_member_name",
]
