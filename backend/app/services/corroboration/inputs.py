"""ARCH45-S1:inputs — one document's stored material as the engine's DocInput. Pure.

The database loader (loader.py) and the offline gates (synthetic documents)
both come through here, so the gates exercise the exact assembly production
uses: stored page geometry -> PageText; ARCH-44 tables -> table regions (their
lines are the table layer's, not clauses) and line items; else line items from
extracted_entities; ARCH-42 mentions -> Mention and the surface-name map that
lets the fields layer see two spellings of one party as one party.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from app.services.corroboration import entities as E
from app.services.corroboration import fields as F
from app.services.corroboration import lines as L
from app.services.corroboration import normalize as n
from app.services.corroboration import segment as S
from app.services.corroboration.engine import DocInput


def infer_date_order(pages: Sequence[S.PageText], text: str = "") -> str:
    from app.services.tables import values as vals

    samples = []
    for page in pages:
        for line in page.lines:
            for tok in line.text.split():
                if vals.date_parts(tok) is not None:
                    samples.append(tok)
    if not samples and text:
        samples = [t for t in text.split() if vals.date_parts(t) is not None]
    return vals.infer_date_order(samples)


def table_regions(tables: Sequence[Any]) -> dict[int, list[S.Box]]:
    """Per page, the boxes of upright tables (their rows belong to the table layer)."""
    out: dict[int, list[S.Box]] = {}
    for t in tables:
        if getattr(t, "rotation", 0) != 0:
            continue
        for box in getattr(t, "bboxes", None) or []:
            try:
                out.setdefault(int(box["page"]), []).append(
                    (float(box["x0"]), float(box["y0"]), float(box["x1"]), float(box["y1"])))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def doc_input(*, doc_id: str, label: str, pages_meta: Optional[Sequence[dict]], text: str,
              fields: Optional[Mapping[str, Any]], tables: Sequence[Any] = (), mentions: Sequence[E.Mention] = (),
              workspace_currency: Optional[str] = None) -> DocInput:
    pages = S.pages_from_metadata(list(pages_meta or []), fallback_text=text or "")
    if not pages and text:
        pages = S.pages_from_text(text)
    items = L.from_tables(tables)
    source = "TABLE" if items else "NONE"
    if not items:
        items = L.from_entities(dict(fields or {}), workspace_currency=workspace_currency)
        source = "EXTRACTED" if items else "NONE"
    names: dict[str, str] = {}
    for m in mentions:
        names[F.name_key(m.surface)] = m.entity_id
        names.setdefault(F.name_key(m.display_name), m.entity_id)
    return DocInput(id=doc_id, label=label, pages=pages, table_regions=table_regions(tables),
                    fields=dict(fields or {}), line_items=items, line_source=source, mentions=list(mentions),
                    entity_names=names, date_order=infer_date_order(pages, text or ""),
                    workspace_currency=workspace_currency)


def synthetic_input(doc: Any, *, extract_tables: bool = True) -> DocInput:
    """A synthetic.SynDoc through ARCH-44's real extractor and this assembly."""
    tables: list[Any] = []
    if extract_tables:
        from app.services.tables import engine as table_engine
        from app.services.tables import reader

        tables = table_engine.extract(reader.pages_for(pdf_bytes=doc.pdf, metadata={"pages": doc.pages}))
    mentions = [E.Mention(m["role"], m["kind"], m["entity"], m["surface"], m.get("display") or m["surface"])
                for m in doc.mentions]
    text = "\n".join(p.get("text") or "" for p in doc.pages)
    return doc_input(doc_id=doc.id, label=doc.label, pages_meta=doc.pages, text=text, fields=doc.fields,
                     tables=tables, mentions=mentions)


__all__ = ["doc_input", "infer_date_order", "synthetic_input", "table_regions"]
