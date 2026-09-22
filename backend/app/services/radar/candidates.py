"""ARCH-34 — the boundary. ORM rows in, pure `layers.Candidate` values out.

THIS MODULE IS THE LINE
=======================

`assertions/retrieve.to_chunks()` is the equivalent in ARCH-33, and it exists
for the same reason: "everything downstream is gateable offline" has to be
checkable rather than aspirational, and it is checkable exactly when there is
one named function where the `Session` stops.

Above this line: `DocumentFingerprint`, `DocumentChunk`, `WorkItem`,
`ProcurementCase`. Below it: tuples of floats and frozensets of strings.
`verify_arch34.py` asserts through the AST that no module under
`app/services/radar/` other than this one, `sweep.py`, `findings.py` and
`suppressions.py` imports `sqlalchemy` or `app.models`.

WHY SHINGLES ARE RECOMPUTED AND NOT STORED
==========================================

`document_fingerprints` stores the SIGNATURE, not the shingle set. The
signature is 128 integers; the shingle set for a sixteen-line invoice is a few
hundred strings, and for a sixty-page contract a few thousand. Storing it
would multiply the table's size by two orders of magnitude to serve one
purpose: L2's evidence sample.

So shingles are recomputed from the work item's text, and ONLY for the pair
that already cleared the threshold. The threshold test itself runs on the
stored signatures alone, which is what makes the sweep an index-and-integers
pass rather than a text pass.

`load_pair_shingles()` is the function that does it, and it is deliberately
separate from `to_candidate()`: a sweep that recomputed shingles for every
candidate it considered would have thrown away the entire point of MinHash.

WHY THE PRICE SERIES COMES FROM `procurement_case_lines`
========================================================

§5.3 names `procurement_line_items`. There is no such table. The tree stores
extracted lines on `procurement_case_lines`, whose parent `procurement_cases`
carries the `vendor_key`, so the series query joins the two.

The consequence is worth stating rather than burying: a workspace that ingests
documents but never runs three-way matching has no line rows, and therefore no
price series and no surge findings. That is a product boundary, not a bug, and
`series_for()` returns an empty tuple rather than pretending otherwise.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document_chunk import DocumentChunk
from app.models.procurement import ProcurementCase, ProcurementCaseLine
from app.models.radar import DocumentFingerprint
from app.models.uploaded_file import UploadedFile
from app.models.work_item import WorkItem
from app.services.radar import fingerprint as fp
from app.services.radar import price_surge
from app.services.radar import vocabulary as vocab
from app.services.radar.layers import Candidate

logger = logging.getLogger("app.services.radar.candidates")

__all__ = [
    "to_candidate",
    "load_candidates",
    "load_pair_shingles",
    "load_chunks",
    "shingle_text_for",
    "build_fingerprint",
    "series_for",
    "MAX_CANDIDATES",
    "MAX_CHUNKS_FOR_EVIDENCE",
]

#: How many counterparts one sweep will compare a subject against. A ceiling
#: rather than a full scan: a workspace with 50,000 documents would otherwise
#: make a single `anomaly.scan_document` job do 50,000 comparisons and hold a
#: LIGHT worker for the whole interval, starving every other job type.
#:
#: The candidates are chosen by the L0/L1/L3 INDEXES, so the ceiling bites
#: only on the tail, and the tail is by construction the least similar.
MAX_CANDIDATES: int = 400

#: Chunks loaded per document for L3's evidence. The closest-pair search is
#: O(n·m), so twenty by twenty is four hundred dot products and sixty by sixty
#: is three thousand six hundred. Twenty is enough to find a representative
#: passage and is bounded.
MAX_CHUNKS_FOR_EVIDENCE: int = 20


# ===========================================================================
# Fingerprint construction
# ===========================================================================


def shingle_text_for(work_item: WorkItem) -> tuple[str, int]:
    """The text L2 shingles, and how many line items it came from.

    Line items first, page text as the fallback, and the difference is not
    cosmetic. Page text carries the header, the footer, the terms block and
    the bank details — identical on every invoice a supplier ever issues — so
    two unrelated invoices from one vendor share hundreds of shingles before a
    single line is compared. The Jaccard floor across a vendor's whole history
    sits around 0.5 instead of near zero, and the 0.85 threshold stops meaning
    what it says.

    The fallback still exists because a delivery note with no extractable
    lines should have an identity rather than no fingerprint at all; the
    finding records `line_count = 0` and the console says the comparison was
    made on page text.
    """
    entities = work_item.extracted_entities or {}
    raw_lines = entities.get("line_items") or entities.get("lines") or []

    rows: list[tuple[Optional[str], Any, Optional[int]]] = []
    if isinstance(raw_lines, list):
        for line in raw_lines:
            if not isinstance(line, dict):
                continue
            description = line.get("description") or line.get("name")
            quantity = line.get("quantity")
            price = line.get("unit_price_micros")
            if price is None and line.get("unit_price") is not None:
                # Extraction wrote a display price rather than micros. Not
                # converted here: `app/core/normalize.py` is the only thing in
                # the tree allowed to read money, and it was not asked. The
                # line still shingles on description and quantity.
                price = None
            rows.append(
                (
                    description,
                    _as_decimal(quantity),
                    None if price is None else int(price),
                )
            )

    if rows:
        return fp.shingle_source_from_lines(rows), len(rows)

    return (work_item.extracted_text or work_item.summary or ""), 0


def _as_decimal(value: Any) -> Any:
    from decimal import Decimal, InvalidOperation

    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def build_fingerprint(
    db: Session, *, work_item: WorkItem
) -> Optional[fp.DocumentFingerprint]:
    """Compute one work item's fingerprint from what is already stored.

    Returns None when the document has no chunks. That is not a failure — it
    is a document that has not finished the ARCH-11 pipeline — and writing a
    fingerprint with a zero embedding would put it into L3's comparison set
    with a cosine of 0 against everything, which fires nothing but costs a row
    and a scan.
    """
    chunk_rows = db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.work_item_id == work_item.id)
        .order_by(DocumentChunk.chunk_index)
    ).scalars().all()
    if not chunk_rows:
        return None

    checksum = ""
    if work_item.uploaded_file_id is not None:
        checksum = db.execute(
            select(UploadedFile.checksum_sha256).where(
                UploadedFile.id == work_item.uploaded_file_id
            )
        ).scalar_one_or_none() or ""
    if not checksum:
        # HARDENING-T1:D19. A work item with no uploaded file row (created
        # before ARCH-10 intake, or by an importer) produced an empty hash,
        # which `ck_df_sha256_lowercase_hex` refuses — so the scan crashed and
        # retried until DEAD. Hash the extracted text instead: identical
        # content still collides, which is what the exact layer is for.
        import hashlib

        checksum = hashlib.sha256(
            (work_item.extracted_text or "").encode("utf-8")
        ).hexdigest()

    entities = work_item.extracted_entities or {}
    shingle_text, line_count = shingle_text_for(work_item)

    # HARDENING-T1:D19. Vendor and document number come from the
    # normalised `document_roles` row when there is one. Raw entities
    # rarely carry keys literally named `vendor_key` / `document_number`,
    # so the vendor+number layer read None for real extractions.
    from app.models.document_role import DocumentRole

    role_row = db.execute(
        select(DocumentRole.vendor_key, DocumentRole.document_number).where(
            DocumentRole.work_item_id == work_item.id
        )
    ).one_or_none()
    vendor_key = (role_row.vendor_key if role_row else None) or _text(
        entities.get("vendor_key")
    )
    document_number = (role_row.document_number if role_row else None) or _text(
        entities.get("document_number")
    )

    return fp.build(
        work_item_id=str(work_item.id),
        content_sha256_hex=checksum,
        vendor_key=vendor_key,
        document_number=document_number,
        shingle_text=shingle_text,
        chunks=tuple(
            fp.ChunkVector(
                chunk_id=str(row.id),
                chunk_index=row.chunk_index,
                text=row.content,
                vector=tuple(float(value) for value in row.embedding),
                page_number=row.page_number,
            )
            for row in chunk_rows
        ),
        line_count=line_count,
        page_count=work_item.page_count,
        document_date=_as_date(entities.get("document_date")),
        total_micros=_as_int(entities.get("total_micros")),
        currency=_text(entities.get("currency")),
        embedding_model=chunk_rows[0].embedding_model,
    )


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    body = str(value).strip()
    return body or None


def _as_int(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        # An unparseable date is ABSENT, not today. `l2_minhash` treats
        # absence as "not measured"; substituting a guess would make the
        # 45-day guard test a number nobody wrote down.
        return None


# ===========================================================================
# Candidate loading
# ===========================================================================


def to_candidate(
    row: DocumentFingerprint,
    *,
    shingles: Optional[frozenset[str]] = None,
    chunks: Sequence[fp.ChunkVector] = (),
) -> Candidate:
    """THE BOUNDARY. One ORM row becomes one pure value."""
    return Candidate(
        fingerprint=fp.DocumentFingerprint(
            work_item_id=str(row.work_item_id),
            content_sha256=row.content_sha256,
            vendor_key=row.vendor_key,
            document_number=row.document_number,
            minhash=tuple(int(value) for value in row.minhash),
            embedding=tuple(float(value) for value in row.embedding),
            shingle_count=row.shingle_count,
            line_count=row.line_count,
            page_count=row.page_count,
            document_date=row.document_date,
            total_micros=row.total_micros,
            currency=row.currency,
            embedding_model=row.embedding_model,
            engine_version=row.engine_version,
        ),
        shingles=shingles or frozenset(),
        chunks=tuple(chunks),
    )


def load_candidates(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    subject: DocumentFingerprint,
    limit: int = MAX_CANDIDATES,
) -> tuple[Candidate, ...]:
    """Counterparts worth comparing the subject against, tenant-scoped.

    ARCH-02: `workspace_id` is in the WHERE clause, not applied afterwards. A
    duplicate finding that named a document from another tenant's workspace
    would be a data leak wearing a finding's clothes.

    Ordering is by the cheap discriminators first — same hash, then same
    vendor — so the `limit` truncates the least similar tail rather than an
    arbitrary slice.
    """
    stmt = (
        select(DocumentFingerprint)
        .where(
            DocumentFingerprint.workspace_id == workspace_id,
            DocumentFingerprint.work_item_id != subject.work_item_id,
        )
        .order_by(
            (DocumentFingerprint.content_sha256 == subject.content_sha256).desc(),
            (DocumentFingerprint.vendor_key == subject.vendor_key).desc(),
            DocumentFingerprint.computed_at.desc(),
        )
        .limit(limit)
    )
    return tuple(to_candidate(row) for row in db.execute(stmt).scalars())


def load_chunks(
    db: Session, *, work_item_id: uuid.UUID, limit: int = MAX_CHUNKS_FOR_EVIDENCE
) -> tuple[fp.ChunkVector, ...]:
    """Chunks for L3 and drift evidence. Bounded; see MAX_CHUNKS_FOR_EVIDENCE."""
    rows = db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.work_item_id == work_item_id)
        .order_by(DocumentChunk.chunk_index)
        .limit(limit)
    ).scalars().all()
    return tuple(
        fp.ChunkVector(
            chunk_id=str(row.id),
            chunk_index=row.chunk_index,
            text=row.content,
            vector=tuple(float(value) for value in row.embedding),
            page_number=row.page_number,
        )
        for row in rows
    )


def load_pair_shingles(
    db: Session, *, left_id: uuid.UUID, right_id: uuid.UUID
) -> tuple[frozenset[str], frozenset[str]]:
    """Recompute both documents' shingles, for evidence only.

    Called AFTER a pair has cleared the L2 threshold on stored signatures.
    Calling it before would mean reading and shingling every candidate's full
    text, which is exactly the work MinHash exists to avoid.
    """
    items = db.execute(
        select(WorkItem).where(WorkItem.id.in_([left_id, right_id]))
    ).scalars().all()
    by_id = {item.id: item for item in items}

    def _shingles(item_id: uuid.UUID) -> frozenset[str]:
        item = by_id.get(item_id)
        if item is None:
            return frozenset()
        text, _ = shingle_text_for(item)
        return fp.shingles(text)

    return _shingles(left_id), _shingles(right_id)


# ===========================================================================
# Price series
# ===========================================================================


def series_for(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    vendor_key: str,
    sku: str,
    currency: str,
    limit: int = 200,
) -> tuple[price_surge.PriceObservation, ...]:
    """Every observed unit price for one `(vendor_key, sku)`, oldest first.

    Joined `procurement_case_lines -> procurement_cases` because the vendor
    lives on the case and the price lives on the line. See the module
    docstring for why §5.3's `procurement_line_items` does not exist.

    Lines with no `invoice_unit_price_micros` are excluded rather than
    defaulted to zero. A line the extractor could not price is missing data,
    and a zero in the series would drag the median toward it and manufacture a
    surge on the next real price.
    """
    stmt = (
        select(
            # The work item is on the CASE, not the line: a case line carries
            # `invoice_line_index`, an offset into the invoice, not a
            # document id. The price is observed ON the invoice the case
            # matched, which is what `ProcurementCase.invoice_work_item_id`
            # names.
            ProcurementCase.invoice_work_item_id,
            ProcurementCaseLine.invoice_unit_price_micros,
            ProcurementCaseLine.description,
            ProcurementCaseLine.line_number,
            ProcurementCase.created_at,
        )
        .join(ProcurementCase, ProcurementCaseLine.case_id == ProcurementCase.id)
        .where(
            ProcurementCaseLine.workspace_id == workspace_id,
            ProcurementCase.vendor_key == vendor_key,
            ProcurementCaseLine.sku == sku,
            ProcurementCaseLine.invoice_unit_price_micros.isnot(None),
            ProcurementCase.invoice_work_item_id.isnot(None),
        )
        .order_by(ProcurementCase.created_at)
        .limit(limit)
    )

    observations: list[price_surge.PriceObservation] = []
    for row in db.execute(stmt):
        work_item_id = row[0]
        price = row[1]
        if work_item_id is None or price is None:
            continue
        observations.append(
            price_surge.PriceObservation(
                work_item_id=str(work_item_id),
                observed_on=row[4].date(),
                unit_price_micros=int(price),
                # Currency is not stored per line, so the caller passes the
                # workspace's resolved currency and every observation in one
                # series carries the same one. A per-line guess would let two
                # currencies into one series, which integer micros make
                # invisible: 50,000 USD and 50,000 INR are the same integer.
                currency=currency,
                vendor_key=vendor_key,
                sku=sku,
                description=row[2],
                line_number=row[3],
            )
        )
    return tuple(observations)
