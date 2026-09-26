"""ARCH47-S1:render — canonical object + mapping version + target -> the exact bytes that will be posted. Pure.

Rendering happens ONCE, when a posting is planned; the bytes and their sha256
are frozen in the ledger, so every retry sends the same thing and a person can
see (and download) exactly what went out.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional

from app.services.erp import canonical as C
from app.services.erp import mapping as M
from app.services.erp import presets as PR
from app.services.erp import vocabulary as v
from app.services.erp.formats import jsonapi, tabular, tally, ubl, x12


class RenderError(ValueError):
    def __init__(self, code: str, message: str, problems: Optional[list[str]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.problems = problems or [message]


@dataclass
class Rendered:
    data: bytes
    media_type: str
    filename: str
    sha256: str
    record: M.Record
    body: Optional[dict] = None           # JSON targets: the request body
    control: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TargetView:
    """What rendering needs of a target (no ORM, so the gates can call it directly)."""

    format: str
    preset: str
    config: Mapping[str, Any]


def render(target: TargetView, obj: C.PostingObject, spec: Mapping[str, Any], *,
           lookups: Mapping[str, Mapping[str, str]], extra: Mapping[str, Any], posting_id: str,
           remote_id: str, at: datetime, control: Optional[x12.ControlNumbers] = None) -> Rendered:
    kind = obj.kind
    if kind not in PR.supported_objects(target.format, target.preset):
        raise RenderError("UNSUPPORTED", f"{v.FORMAT_LABELS[target.format]} "
                                         f"{'(' + v.PRESET_LABELS[target.preset] + ') ' if target.format == v.FORMAT_JSON else ''}"
                                         f"does not take a {v.OBJECT_LABELS[kind].lower()}")
    try:
        record = M.evaluate(spec, obj, lookups=lookups, extra=dict(extra))
    except M.MappingError as exc:
        raise RenderError("MAPPING", "the mapping could not produce every required field", exc.problems) from exc
    body = None
    control_json: dict = {}
    try:
        if target.format == v.FORMAT_CSV:
            data = tabular.to_csv(record, target.config)
        elif target.format == v.FORMAT_XLSX:
            data = tabular.to_xlsx(record, target.config)
        elif target.format == v.FORMAT_UBL:
            data = ubl.render(record, kind, target.config)
        elif target.format == v.FORMAT_X12:
            if control is None:
                raise RenderError("NO_CONTROL", "an X12 interchange needs control numbers")
            data = x12.render(record, kind, control=control, at=at, config=target.config)
            control_json = control.as_json()
        elif target.format == v.FORMAT_TALLY:
            data = tally.render(record, kind, remote_id=remote_id, config=target.config)
        else:
            body, data = jsonapi.build(target.preset, kind, record, target.config)
    except (ubl.UblError, x12.X12Error, tally.TallyError, jsonapi.ApiError, ValueError) as exc:
        if isinstance(exc, RenderError):
            raise
        raise RenderError("FORMAT", str(exc)) from exc
    if len(data) > v.MAX_RENDERED_BYTES:
        raise RenderError("TOO_LARGE", f"{len(data)} bytes; a posting is at most {v.MAX_RENDERED_BYTES}")
    return Rendered(data=data, media_type=PR.MEDIA_TYPES[target.format],
                    filename=PR.filename(target.format, kind, obj.document_number, posting_id),
                    sha256=hashlib.sha256(data).hexdigest(), record=record, body=body, control=control_json)


def validate_rendered(target: TargetView, object_kind: str, data: bytes) -> list[str]:
    """The published-schema (or encoded-specification) check for rendered bytes: the golden-file gate uses it."""
    from app.services.erp.formats import xsd

    if target.format == v.FORMAT_CSV:
        return tabular.validate_csv(data, delimiter=((target.config.get("csv") or {}).get("delimiter", ",")))
    if target.format == v.FORMAT_XLSX:
        return xsd.validate_xlsx(data)
    if target.format == v.FORMAT_UBL:
        return ubl.validate(data, object_kind)
    if target.format == v.FORMAT_X12:
        return x12.validate(data)
    if target.format == v.FORMAT_TALLY:
        return tally.validate(data)
    return jsonapi.validate_body(target.preset, object_kind, jsonapi.loads(data), target.config)


__all__ = ["RenderError", "Rendered", "TargetView", "render", "validate_rendered"]
