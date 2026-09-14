"""ARCH-32 — prove the OUTPUT is clean. Pure: bytes and secrets in, a report out.

WHAT THIS CHECKS AND WHY EACH CHECK IS SEPARATE
===============================================

Three independent checks, because they fail for different reasons and a job
that passes two of them is not a job that can ship.

  1. **Text extraction.** Render-independent: whatever a PDF reader's
     copy-paste would produce. Catches an OCR text layer that transcribed a
     box the burn missed, and catches an assembler that accidentally carried a
     content stream across.

  2. **Object-tree scan.** Walks every object in the output looking for the
     seven keys in `FORBIDDEN_OUTPUT_KEYS`. Each one is a place a PDF can
     carry text that check 1 will never return — an annotation's `/Contents`,
     a form field's `/V`, an XMP packet, an attached file, an `/OpenAction`
     JavaScript string, an optional content group's hidden layer. A clean
     extraction with an `/EmbeddedFiles` name tree is the exact failure this
     phase exists to prevent, and it is invisible to check 1 by construction.

  3. **Decompressed stream scan.** Inflates every stream and looks for the
     secrets as bytes. Catches the case where text survives in a form no
     extractor decodes — a stream pikepdf can read and pdfium declines to
     interpret, or a font's ToUnicode map.

Any hit on any of the three FAILS the job. There is no "warn" level, because
the only honest thing to tell a legal team about a partial redaction is that
it did not work.

THE SECRETS NEVER TOUCH THE DATABASE AND NEVER TOUCH A LOG
==========================================================

`check_output` takes the plaintext detections the apply job is holding in
memory, uses them, and the caller drops them. Nothing here writes them
anywhere: the report carries COUNTS and the offending page number, never the
value or a substring of it. `LeakReport.summary()` is what gets stored, and
`verify_arch32.py` asserts that a report from a deliberately leaking document
contains none of the leaked text.

NORMALISATION
=============

Comparison is done on a folded form — casefolded, with separators and
whitespace removed — because `4111 1111 1111 1111` and `4111-1111-1111-1111`
and `4111111111111111` are the same secret and an extractor may return any of
them. Folding widens what counts as a hit, which is the correct direction for
a check whose false positive costs a re-run and whose false negative costs a
customer.
"""

from __future__ import annotations

import io
import re
import zlib
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from app.services.redaction.vocabulary import FORBIDDEN_OUTPUT_KEYS

__all__ = [
    "LeakReport",
    "LeakCheckError",
    "fold",
    "extract_text_per_page",
    "scan_object_tree",
    "scan_streams",
    "check_output",
]

#: Everything a printed identifier might be broken up with. Removed on both
#: sides before comparison.
_SEPARATORS = re.compile(r"[\s\-\u00a0\u2010-\u2015_.,/\\()\[\]]+")

#: A secret shorter than this is not searched for. Two- and three-character
#: tokens ("Li", "Ng") match somewhere on almost every page, and a leak check
#: that always fails is a leak check somebody switches off.
MIN_SECRET_LENGTH: int = 4


class LeakCheckError(RuntimeError):
    """The output could not be inspected. Treated as a failed check."""


def fold(text: str) -> str:
    """Casefold and strip every separator. Used on both sides of every compare."""
    return _SEPARATORS.sub("", text).casefold()


@dataclass
class LeakReport:
    """The verdict. Carries counts and locations, never content."""

    passed: bool
    pages_checked: int
    secrets_checked: int
    #: 1-based page numbers where extracted text matched a secret.
    text_hits: tuple[int, ...] = ()
    #: Forbidden keys found anywhere in the object tree, sorted and de-duped.
    forbidden_keys: tuple[str, ...] = ()
    #: Object generation numbers are useless to a reader; the count is not.
    stream_hits: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> dict[str, Any]:
        """The shape stored on the job row and embedded in the manifest."""
        return {
            "passed": self.passed,
            "pages_checked": self.pages_checked,
            "secrets_checked": self.secrets_checked,
            "text_hit_pages": list(self.text_hits),
            "forbidden_keys": list(self.forbidden_keys),
            "stream_hits": self.stream_hits,
            "notes": list(self.notes),
        }

    def sentence(self) -> str:
        """One line for the studio. §3.6 requires a sentence, not a JSON blob."""
        if self.passed:
            return (
                f"No redacted text found in the output. "
                f"{self.pages_checked} page"
                f"{'' if self.pages_checked == 1 else 's'} checked against "
                f"{self.secrets_checked} detection"
                f"{'' if self.secrets_checked == 1 else 's'}."
            )
        reasons: list[str] = []
        if self.text_hits:
            pages = ", ".join(str(p) for p in self.text_hits)
            reasons.append(f"text was still extractable on page {pages}")
        if self.forbidden_keys:
            reasons.append(
                "the output carried " + ", ".join(self.forbidden_keys)
            )
        if self.stream_hits:
            reasons.append(
                f"{self.stream_hits} stream"
                f"{'' if self.stream_hits == 1 else 's'} still contained "
                "redacted content"
            )
        return "The leak check failed: " + "; ".join(reasons) + "."


def extract_text_per_page(pdf_bytes: bytes) -> list[str]:
    """Text a reader's copy-paste would produce, one string per page."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(io.BytesIO(pdf_bytes))
    try:
        pages: list[str] = []
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                pages.append(textpage.get_text_range() or "")
            finally:
                textpage.close()
        return pages
    except Exception as exc:  # noqa: BLE001
        raise LeakCheckError(f"could not extract text: {type(exc).__name__}") from exc
    finally:
        document.close()


def scan_object_tree(pdf_bytes: bytes) -> tuple[str, ...]:
    """Forbidden keys present anywhere in the output, sorted and de-duplicated.

    Walks objects rather than only the catalog. An `/AcroForm` reachable only
    from a page's parent, or an `/EmbeddedFiles` hanging off a names tree two
    levels down, is exactly as dangerous as one on the root and exactly as
    invisible to a catalog-only check.
    """
    import pikepdf

    found: set[str] = set()
    seen: set[int] = set()

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 64:
            return
        try:
            objgen = getattr(obj, "objgen", None)
            if objgen and objgen != (0, 0):
                if objgen[0] in seen:
                    return
                seen.add(objgen[0])
        except Exception:  # noqa: BLE001
            pass

        if isinstance(obj, pikepdf.Dictionary) or (
            hasattr(obj, "keys") and not isinstance(obj, (str, bytes))
        ):
            try:
                keys = list(obj.keys())
            except Exception:  # noqa: BLE001
                keys = []
            for key in keys:
                name = key if isinstance(key, str) else str(key)
                if name in FORBIDDEN_OUTPUT_KEYS:
                    found.add(name)
                try:
                    walk(obj[key], depth + 1)
                except Exception:  # noqa: BLE001
                    continue
        elif isinstance(obj, pikepdf.Array) or (
            isinstance(obj, (list, tuple)) and not isinstance(obj, (str, bytes))
        ):
            for item in obj:
                walk(item, depth + 1)

    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            walk(pdf.Root)
            walk(pdf.trailer)
            for page in pdf.pages:
                walk(page.obj if hasattr(page, "obj") else page)
            for obj in pdf.objects:
                walk(obj)
    except Exception as exc:  # noqa: BLE001
        raise LeakCheckError(
            f"could not scan the object tree: {type(exc).__name__}"
        ) from exc

    return tuple(sorted(found))


def scan_streams(pdf_bytes: bytes, folded_secrets: Iterable[str]) -> int:
    """How many streams still contain one of the secrets once inflated."""
    import pikepdf

    secrets = [s for s in folded_secrets if len(s) >= MIN_SECRET_LENGTH]
    if not secrets:
        return 0

    hits = 0
    try:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            for obj in pdf.objects:
                if not isinstance(obj, pikepdf.Stream):
                    continue
                try:
                    raw = obj.read_bytes()
                except Exception:  # noqa: BLE001
                    try:
                        raw = zlib.decompress(obj.read_raw_bytes())
                    except Exception:  # noqa: BLE001
                        continue
                # Image data is the bulk of the output and cannot contain a
                # readable string by construction, but decoding it as latin-1
                # and folding is still cheaper than deciding which streams to
                # skip and being wrong about one of them.
                try:
                    text = raw.decode("latin-1", errors="ignore")
                except Exception:  # noqa: BLE001
                    continue
                folded = fold(text)
                if any(secret in folded for secret in secrets):
                    hits += 1
    except Exception as exc:  # noqa: BLE001
        raise LeakCheckError(
            f"could not scan streams: {type(exc).__name__}"
        ) from exc
    return hits


def check_output(
    pdf_bytes: bytes,
    secrets: Iterable[str],
    *,
    scan_stream_content: bool = True,
) -> LeakReport:
    """Run all three checks. `secrets` is in-memory plaintext; it is not stored.

    Raising is never the answer here: an inspection that blows up is a job
    whose cleanliness is unknown, and unknown must resolve to FAILED rather
    than to an exception the worker's generic handler turns into a retry.
    """
    notes: list[str] = []

    folded_secrets = sorted(
        {f for f in (fold(s) for s in secrets) if len(f) >= MIN_SECRET_LENGTH}
    )
    dropped = len([s for s in secrets]) - len(folded_secrets)
    if dropped > 0:
        notes.append(
            f"{dropped} detection(s) were too short or duplicated to search for"
        )

    try:
        pages = extract_text_per_page(pdf_bytes)
    except LeakCheckError as exc:
        return LeakReport(
            passed=False,
            pages_checked=0,
            secrets_checked=len(folded_secrets),
            notes=tuple(notes + [str(exc)]),
        )

    text_hits: list[int] = []
    for index, page_text in enumerate(pages, start=1):
        folded_page = fold(page_text)
        if not folded_page:
            continue
        if any(secret in folded_page for secret in folded_secrets):
            text_hits.append(index)

    try:
        forbidden = scan_object_tree(pdf_bytes)
    except LeakCheckError as exc:
        return LeakReport(
            passed=False,
            pages_checked=len(pages),
            secrets_checked=len(folded_secrets),
            text_hits=tuple(text_hits),
            notes=tuple(notes + [str(exc)]),
        )

    stream_hits = 0
    if scan_stream_content:
        try:
            stream_hits = scan_streams(pdf_bytes, folded_secrets)
        except LeakCheckError as exc:
            return LeakReport(
                passed=False,
                pages_checked=len(pages),
                secrets_checked=len(folded_secrets),
                text_hits=tuple(text_hits),
                forbidden_keys=forbidden,
                notes=tuple(notes + [str(exc)]),
            )

    passed = not text_hits and not forbidden and stream_hits == 0
    return LeakReport(
        passed=passed,
        pages_checked=len(pages),
        secrets_checked=len(folded_secrets),
        text_hits=tuple(text_hits),
        forbidden_keys=forbidden,
        stream_hits=stream_hits,
        notes=tuple(notes),
    )