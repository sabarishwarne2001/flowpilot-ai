"""ARCH42-S1:presets — which ARCH-38 preset fits a document, and the seam that
makes an ENABLED preset change what extraction asks for.

THE SEAM (a correction to ARCH-38)
==================================

ARCH-38 let a workspace apply and enable a preset ("review the field list...
before enabling") but nothing sent that field list to the extraction prompt,
so enabling a preset changed nothing a document produced. ARCH-42's `x-entity`
annotations live on those fields, so they would have been inert.

`prompt_context` closes the seam narrowly: only presets ENABLED in the
workspace are considered; one is chosen by its classifier hints appearing in
the document text (at least two, or all of them when it has fewer); and its
fields are appended to the entity-extraction prompt as extra keys to return.
A workspace with no enabled preset gets no block and exactly the prompt it had
before. Not capability-gated: enabling a preset is an ARCH-38 feature on every
tier. Never raises: a preset fault costs the extra fields, not the document.

SELECTING A PRESET FOR RESOLUTION
=================================

`select_for_document` picks the preset whose REQUIRED fields the document's
extracted fields all contain, preferring a preset enabled in the workspace,
then the larger field overlap. Platform presets count even when not enabled:
an annotation is mapping metadata, and a résumé the generic prompt extracted
(candidate_name, email) is still a résumé.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.services.entities import annotations as ann

logger = logging.getLogger("app.services.entities.presets")

MIN_HINTS = 2
MAX_PROMPT_FIELDS = 40


def _visible(db: Session, organization_id: uuid.UUID) -> list[Any]:
    from app.models.ingestion import DocumentSchemaPreset

    return list(db.execute(select(DocumentSchemaPreset).where(or_(
        DocumentSchemaPreset.organization_id.is_(None),
        DocumentSchemaPreset.organization_id == organization_id,
    )).order_by(DocumentSchemaPreset.document_type, DocumentSchemaPreset.version.desc())).scalars())


def _enabled_ids(db: Session, workspace_id: uuid.UUID) -> set[uuid.UUID]:
    from app.models.ingestion import WorkspaceSchemaPreset

    return set(db.execute(select(WorkspaceSchemaPreset.preset_id).where(
        WorkspaceSchemaPreset.workspace_id == workspace_id, WorkspaceSchemaPreset.enabled.is_(True))).scalars())


def select_for_document(
    db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, extracted: Mapping[str, Any]
) -> Optional[tuple[str, dict, Optional[dict]]]:
    """(document_type, field annotations, detect) of the best-fitting preset, or None."""
    keys = {ann.field_key(k) for k, value in (extracted or {}).items() if value not in (None, "", [], {})}
    if not keys:
        return None
    enabled = _enabled_ids(db, workspace_id)
    best: Optional[tuple[tuple, Any]] = None
    for preset in _visible(db, organization_id):
        schema = preset.schema or {}
        properties = set((schema.get("properties") or {}).keys())
        required = set(schema.get("required") or [])
        if not required or not required <= keys:
            continue
        fields, detect = ann.annotations_from_schema(schema)
        if not fields:
            continue
        score = (preset.id in enabled, len(properties & keys), -len(properties))
        if best is None or score > best[0]:
            best = (score, (preset.document_type, fields, detect))
    return best[1] if best else None


def _hint_count(hints: list[Any], text: str) -> int:
    lowered = (text or "").casefold()
    return sum(1 for h in hints if isinstance(h, str) and h.strip() and h.casefold() in lowered)


def prompt_context(db: Session, *, workspace_id: uuid.UUID, text: str) -> Optional[str]:
    """The extra-keys block for an enabled preset matching the text, or None."""
    try:
        with db.begin_nested():  # a failed read must not poison the enrichment transaction
            return _prompt_context(db, workspace_id=workspace_id, text=text)
    except Exception:  # noqa: BLE001
        logger.exception("entities.preset_prompt_failed", extra={"workspace_id": str(workspace_id)})
        return None


def _prompt_context(db: Session, *, workspace_id: uuid.UUID, text: str) -> Optional[str]:
    if True:
        from app.models.ingestion import DocumentSchemaPreset

        enabled = _enabled_ids(db, workspace_id)
        if not enabled:
            return None
        best: Optional[tuple[int, Any]] = None
        for preset in db.execute(select(DocumentSchemaPreset).where(DocumentSchemaPreset.id.in_(enabled))).scalars():
            hints = list(preset.classifier_hints or [])
            needed = min(MIN_HINTS, len(hints)) or 1
            count = _hint_count(hints, text)
            if hints and count >= needed and (best is None or count > best[0]):
                best = (count, preset)
        if best is None:
            return None
        preset = best[1]
        properties = (preset.schema or {}).get("properties") or {}
        lines = [
            f"This workspace also extracts {preset.label} documents with the fields below.",
            "Include each of these keys in the JSON (null when absent):",
        ]
        for name, definition in list(properties.items())[:MAX_PROMPT_FIELDS]:
            kind = definition.get("type", "string") if isinstance(definition, dict) else "string"
            if kind == "array":
                kind = f"array of {(definition.get('items') or {}).get('type', 'string')}"
            description = (definition.get("description") or definition.get("title") or "") if isinstance(definition, dict) else ""
            lines.append(f"- {name} ({kind}){': ' + description if description else ''}")
        return "\n".join(lines)
