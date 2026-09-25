"""ARCH45-S1:fingerprint — the cache key of a comparison, and what invalidates it.

    set_hash      sha256 over the SORTED work item ids: which documents,
                  whatever order they were given in.
    content_hash  per document, sha256 over everything the engine reads:
                  the extracted text, every stored page (text and block boxes),
                  the extracted values, each ARCH-44 table's id and revision
                  (a re-extraction creates new table ids; a cell correction or
                  a column role bumps the revision), and each live ARCH-42
                  mention with its canonical root and decision (a merge or an
                  unmerge moves the root).
    fingerprint   sha256 over the engine version, the encoder, the options,
                  the rule digests and the content hashes in id order.

A request whose fingerprint equals a COMPLETED run's is served from that run
(cached). Reprocessing a document, re-extracting or correcting its tables,
correcting its fields or resolving its entities differently changes its
content hash, so the next request computes afresh, and invalidate() marks the
old run STALE.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence


def _sha(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
                          .encode("utf-8")).hexdigest()


def set_hash(work_item_ids: Sequence[Any]) -> str:
    return _sha(sorted(str(x) for x in work_item_ids))


def _pages(meta: Any) -> list:
    pages = (meta or {}).get("pages") if isinstance(meta, dict) else None
    out = []
    for p in pages or []:
        blocks = []
        for b in p.get("blocks") or []:
            box = b.get("box") or {}
            blocks.append([b.get("text") or "", *[round(float(box.get(k, 0) or 0), 1) for k in ("x0", "y0", "x1", "y1")]])
        out.append([p.get("page_number"), p.get("text") or "", p.get("width"), p.get("height"), blocks])
    return out


def content_hash(work_item: Any, tables: Sequence[Any], mention_keys: Sequence[tuple]) -> str:
    """`tables`: ExtractedTable rows (id, revision); `mention_keys`: (mention id, root id, decision)."""
    return _sha({
        "text": hashlib.sha256((work_item.extracted_text or "").encode("utf-8")).hexdigest(),
        "pages": _sha(_pages(work_item.extraction_metadata)),
        "fields": _sha(work_item.extracted_entities if isinstance(work_item.extracted_entities, (dict, list)) else {}),
        "tables": sorted([str(t.id), int(t.revision or 1), t.status == "REJECTED"] for t in tables),
        "mentions": sorted(list(k) for k in mention_keys),
    })


def fingerprint(*, engine_version: str, encoder: str, options: dict, rule_digests: Sequence[str],
                contents: dict[str, str]) -> str:
    return _sha({"engine": engine_version, "encoder": encoder, "options": options,
                 "rules": sorted(rule_digests), "documents": sorted(contents.items())})


__all__ = ["content_hash", "fingerprint", "set_hash"]
