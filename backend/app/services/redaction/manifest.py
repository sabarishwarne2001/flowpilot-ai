"""ARCH-32 — the input digest and the manifest. Pure: values in, JSON out.

THE MANIFEST TRAVELS WITH THE DOCUMENT
======================================

That single sentence decides everything else in this module. A manifest that
goes to opposing counsel with the sanitized PDF cannot contain a single
character of what was redacted — not the matched text, not a prefix of it, not
a "first four digits" convenience field, and not a detector label specific
enough to reconstruct it. What it CAN carry is arithmetic: how many regions,
of which detector classes, at which precision, over which pages, hashed how.

`verify_arch32.py` asserts this by building a job whose detections are known
strings and searching the serialised manifest for every one of them. That gate
is the reason `build_manifest` takes COUNTS rather than the detections
themselves: a function that never receives the plaintext cannot leak it, and
that is a cheaper guarantee than a review of what it chooses to print.

THE INPUT DIGEST
================

Same pattern as `procurement_matching/digest.py`, same reason. SHA-256 over
canonical JSON — sorted keys, no whitespace, no floats — of everything that
can change the OUTPUT BYTES:

  * the source's own SHA-256
  * the engine version
  * the profile key
  * the render DPI and colour mode
  * whether a text layer is restored
  * every ENABLED region, as (page, rounded box, detector, precision)

`ENGINE_VERSION` is in there deliberately. Without it, a job re-run after the
burn padding changes produces different bytes under the same digest, and
"deterministic output hash across repeated runs" quietly becomes "deterministic
until someone edits `vocabulary.py`".

Boxes are rounded to four decimal places before hashing, matching
`numeric(10,4)` on `redaction_regions`. Hashing the unrounded Python float
would make the digest depend on whether a value arrived from the database
(Decimal) or from the request body (float) — the same job, two digests, and a
determinism gate that fails for a reason nobody can find.

DISABLED REGIONS ARE NOT IN THE DIGEST
======================================

A region the reviewer switched off does not affect the output, so it must not
affect the digest. Including it would mean toggling a box off and on again
produced a job that is byte-identical but digest-different, which defeats the
only purpose the digest has.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.services.redaction.vocabulary import (
    CHECKSUM_DETECTORS,
    ENGINE_VERSION,
    GEOMETRY_PRECISIONS,
    PRODUCER_STRING,
)

__all__ = [
    "MANIFEST_VERSION",
    "RegionFingerprint",
    "canonical_json",
    "sha256_hex",
    "input_digest",
    "build_manifest",
]

#: Bumped when the manifest's SHAPE changes, independently of ENGINE_VERSION.
#: A consumer that parsed v1 must be able to tell that it is looking at v2
#: without diffing key sets.
MANIFEST_VERSION: str = "arch32.manifest.v1"

_BOX_PLACES = Decimal("0.0001")


@dataclass(frozen=True)
class RegionFingerprint:
    """Everything about a region that affects the output. No text, ever."""

    page_number: int
    x0: Decimal
    y0: Decimal
    x1: Decimal
    y1: Decimal
    detector: str
    geometry_precision: str

    def as_tuple(self) -> list[Any]:
        return [
            int(self.page_number),
            str(self.x0.quantize(_BOX_PLACES)),
            str(self.y0.quantize(_BOX_PLACES)),
            str(self.x1.quantize(_BOX_PLACES)),
            str(self.y1.quantize(_BOX_PLACES)),
            self.detector,
            self.geometry_precision,
        ]


def canonical_json(payload: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8. The only serialisation hashed here."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def input_digest(
    *,
    source_sha256: str,
    profile_key: str,
    render_dpi: int,
    grayscale: bool,
    restore_text_layer: bool,
    regions: Iterable[RegionFingerprint],
) -> str:
    """SHA-256 over everything that can change the output bytes.

    Regions are sorted before hashing. Two jobs whose regions were inserted in
    a different order are the same job, and a digest that disagreed would make
    the determinism gate depend on a database's row ordering.
    """
    fingerprints = sorted(
        (region.as_tuple() for region in regions),
        key=lambda item: (item[0], item[1], item[2], item[5]),
    )
    payload = {
        "engine_version": ENGINE_VERSION,
        "source_sha256": source_sha256,
        "profile_key": profile_key,
        "render_dpi": int(render_dpi),
        "color_mode": "grayscale" if grayscale else "rgb",
        "restore_text_layer": bool(restore_text_layer),
        "regions": fingerprints,
    }
    return sha256_hex(canonical_json(payload))


def build_manifest(
    *,
    job_id: str,
    organization_id: str,
    workspace_id: str,
    work_item_id: str,
    source_sha256: str,
    output_sha256: str,
    profile_key: str,
    render_dpi: int,
    grayscale: bool,
    restore_text_layer: bool,
    text_layer_applied: bool,
    page_count: int,
    output_bytes: int,
    regions: Sequence[RegionFingerprint],
    disabled_region_count: int,
    leak_check: Mapping[str, Any],
    approved_by: Optional[str],
    approved_at: Optional[str],
    created_at: Optional[str],
    completed_at: Optional[str],
    digest: Optional[str] = None,
) -> dict[str, Any]:
    """The JSON object written beside the sanitized PDF.

    Every field here is a count, a hash, an identifier or a timestamp. There
    is no field that can hold document content, which is a property of the
    signature rather than of this function's discipline: nothing carrying
    plaintext is passed in.
    """
    by_detector = Counter(region.detector for region in regions)
    by_precision = Counter(region.geometry_precision for region in regions)
    by_page = Counter(region.page_number for region in regions)

    checksum_backed = sum(
        count
        for detector, count in by_detector.items()
        if detector in CHECKSUM_DETECTORS
    )

    return {
        "manifest_version": MANIFEST_VERSION,
        "engine_version": ENGINE_VERSION,
        "producer": PRODUCER_STRING,
        "job": {
            "id": job_id,
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "work_item_id": work_item_id,
            "created_at": created_at,
            "completed_at": completed_at,
        },
        "source": {
            "sha256": source_sha256,
        },
        "output": {
            "sha256": output_sha256,
            "pages": int(page_count),
            "bytes": int(output_bytes),
            "page_content": "image",
            "text_layer": "ocr" if text_layer_applied else "none",
            # Stated on the artifact because the operator was told it in the
            # apply dialog and a regulator reading only this file was not.
            # See rasterize.py's header for why these are false.
            "annotations_rendered": False,
            "form_fields_rendered": False,
        },
        "settings": {
            "profile": profile_key,
            "render_dpi": int(render_dpi),
            "color_mode": "grayscale" if grayscale else "rgb",
            "restore_text_layer": bool(restore_text_layer),
        },
        "regions": {
            "applied": len(regions),
            "disabled_by_reviewer": int(disabled_region_count),
            "checksum_validated": checksum_backed,
            "by_detector": dict(sorted(by_detector.items())),
            "by_precision": {
                precision: by_precision.get(precision, 0)
                for precision in GEOMETRY_PRECISIONS
            },
            "by_page": {str(page): count for page, count in sorted(by_page.items())},
        },
        "leak_check": dict(leak_check),
        "approval": {
            "approved_by_user_id": approved_by,
            "approved_at": approved_at,
        },
        "input_digest": digest,
    }