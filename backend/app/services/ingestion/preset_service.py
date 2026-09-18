"""ARCH-38 — document schema presets: the vertical packs.

THE SCHEMA-OF-SCHEMAS
=====================

A preset's `schema` column is a JSON Schema describing the fields to extract.
`validate_preset_schema` is the schema that describes *those* schemas, hand
written rather than pulled from `jsonschema`: the dependency is not in
requirements.txt, adding it would be a new runtime dependency for one
validation, and a bounded subset is all a preset may use anyway.

What a preset schema may contain:
  - `type` exactly "object"
  - `properties`: a mapping of field name -> {type, title, description, items}
  - each field `type` is one of string / number / integer / boolean / array
  - an array field carries `items` with a scalar `type`
  - `required`: a list of names that all appear in `properties`

Anything else is refused. This is deliberately narrower than JSON Schema: a
preset drives an extraction prompt and a form, and `$ref`, `oneOf` or
`patternProperties` have no meaning in either.

WORDING
=======

`hipaa_safe_harbor` implements Safe Harbor identifier removal. It does not make
a deployment HIPAA compliant -- that also needs a business associate agreement
and administrative safeguards. `SAFE_HARBOR_NOTICE` is the sentence the console
renders wherever that profile appears, and `verify_arch38.py` gate P4 fails the
build if any product copy in the frontend claims "HIPAA compliant".
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ingestion import (
    DocumentSchemaPreset,
    WorkspaceSchemaPreset,
)
from app.services.redaction.vocabulary import PROFILE_KEYS

SCALAR_TYPES: frozenset[str] = frozenset(
    {"string", "number", "integer", "boolean"}
)
FIELD_TYPES: frozenset[str] = SCALAR_TYPES | {"array"}

FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

MAX_FIELDS = 60

SAFE_HARBOR_NOTICE = (
    "Removes the HIPAA Safe Harbor identifiers. This is identifier removal, "
    "not a compliance certification: a compliant deployment also needs a "
    "business associate agreement and administrative safeguards."
)

INDUSTRIES: tuple[str, ...] = ("HR", "HEALTHCARE", "LEGAL", "LOGISTICS", "KYC")


class PresetError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_preset_schema(schema: Any) -> dict[str, Any]:
    """Validate a preset's field list against the schema-of-schemas."""
    if not isinstance(schema, dict):
        raise PresetError("SCHEMA_NOT_OBJECT", "A preset schema must be an object.")
    if schema.get("type") != "object":
        raise PresetError(
            "SCHEMA_TYPE", "A preset schema must declare type 'object'."
        )

    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise PresetError(
            "SCHEMA_NO_PROPERTIES", "A preset schema must define at least one field."
        )
    if len(properties) > MAX_FIELDS:
        raise PresetError(
            "SCHEMA_TOO_MANY_FIELDS",
            f"A preset may define at most {MAX_FIELDS} fields.",
        )

    for name, definition in properties.items():
        if not isinstance(name, str) or not FIELD_NAME.match(name):
            raise PresetError(
                "SCHEMA_FIELD_NAME",
                f"{name!r} is not a valid field name "
                "(lowercase, digits and underscores).",
            )
        if not isinstance(definition, dict):
            raise PresetError(
                "SCHEMA_FIELD_SHAPE", f"Field {name!r} must be an object."
            )
        field_type = definition.get("type")
        if field_type not in FIELD_TYPES:
            raise PresetError(
                "SCHEMA_FIELD_TYPE",
                f"Field {name!r} has type {field_type!r}; allowed types are "
                f"{sorted(FIELD_TYPES)}.",
            )
        if field_type == "array":
            items = definition.get("items")
            if not isinstance(items, dict) or items.get("type") not in SCALAR_TYPES:
                raise PresetError(
                    "SCHEMA_ARRAY_ITEMS",
                    f"Array field {name!r} needs items with a scalar type.",
                )
        for forbidden in ("$ref", "oneOf", "anyOf", "allOf", "patternProperties"):
            if forbidden in definition:
                raise PresetError(
                    "SCHEMA_UNSUPPORTED",
                    f"Field {name!r} uses {forbidden}, which a preset may not.",
                )

    required = schema.get("required", [])
    if not isinstance(required, list):
        raise PresetError("SCHEMA_REQUIRED", "'required' must be a list.")
    missing = [name for name in required if name not in properties]
    if missing:
        raise PresetError(
            "SCHEMA_REQUIRED_UNKNOWN",
            f"'required' names fields that are not defined: {missing}.",
        )
    return schema


def validate_assertions(assertions: Any) -> list[dict[str, Any]]:
    if not isinstance(assertions, list):
        raise PresetError("ASSERTIONS_NOT_LIST", "'assertions' must be a list.")
    validated: list[dict[str, Any]] = []
    for entry in assertions:
        if not isinstance(entry, dict):
            raise PresetError(
                "ASSERTION_SHAPE", "Each assertion must be an object."
            )
        sentence = entry.get("sentence")
        if not isinstance(sentence, str) or not sentence.strip():
            raise PresetError(
                "ASSERTION_SENTENCE", "Each assertion needs a sentence."
            )
        severity = entry.get("severity", "MEDIUM")
        if severity not in {"LOW", "MEDIUM", "HIGH"}:
            raise PresetError(
                "ASSERTION_SEVERITY",
                f"{severity!r} is not a severity (LOW, MEDIUM, HIGH).",
            )
        validated.append({"sentence": sentence.strip(), "severity": severity})
    return validated


def validate_redaction_profile(profile: Optional[str]) -> Optional[str]:
    """A preset may only name a profile `app/services/redaction/vocabulary.py`
    actually defines. A preset naming a profile that does not exist would fail
    at redaction time, long after anyone connected the two."""
    if profile in (None, ""):
        return None
    if profile not in PROFILE_KEYS:
        raise PresetError(
            "UNKNOWN_REDACTION_PROFILE",
            f"{profile!r} is not a redaction profile. Known profiles: "
            f"{list(PROFILE_KEYS)}.",
        )
    return profile


def validate_preset(preset: DocumentSchemaPreset) -> DocumentSchemaPreset:
    validate_preset_schema(preset.schema)
    validate_assertions(preset.assertions)
    validate_redaction_profile(preset.redaction_profile)
    if preset.industry not in INDUSTRIES:
        raise PresetError(
            "UNKNOWN_INDUSTRY",
            f"{preset.industry!r} is not one of {list(INDUSTRIES)}.",
        )
    return preset


def visible_presets(
    db: Session, *, organization_id: uuid.UUID
) -> list[DocumentSchemaPreset]:
    """Platform presets plus this organization's own. Never another tenant's."""
    return list(
        db.execute(
            select(DocumentSchemaPreset)
            .where(
                (DocumentSchemaPreset.organization_id.is_(None))
                | (DocumentSchemaPreset.organization_id == organization_id)
            )
            .order_by(
                DocumentSchemaPreset.industry,
                DocumentSchemaPreset.document_type,
                DocumentSchemaPreset.version,
            )
        ).scalars()
    )


def get_preset(
    db: Session, *, organization_id: uuid.UUID, preset_id: uuid.UUID
) -> DocumentSchemaPreset:
    row = db.execute(
        select(DocumentSchemaPreset).where(
            DocumentSchemaPreset.id == preset_id,
            (DocumentSchemaPreset.organization_id.is_(None))
            | (DocumentSchemaPreset.organization_id == organization_id),
        )
    ).scalar_one_or_none()
    if row is None:
        raise PresetError("PRESET_NOT_FOUND", "Preset not found.")
    return row


def applied_presets(
    db: Session, *, workspace_id: uuid.UUID
) -> list[WorkspaceSchemaPreset]:
    return list(
        db.execute(
            select(WorkspaceSchemaPreset).where(
                WorkspaceSchemaPreset.workspace_id == workspace_id
            )
        ).scalars()
    )


def apply_to_workspace(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    preset_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
) -> WorkspaceSchemaPreset:
    """Apply a preset. It is NOT enabled: that is a second, deliberate act."""
    preset = get_preset(db, organization_id=organization_id, preset_id=preset_id)
    validate_preset(preset)

    existing = db.execute(
        select(WorkspaceSchemaPreset).where(
            WorkspaceSchemaPreset.workspace_id == workspace_id,
            WorkspaceSchemaPreset.preset_id == preset.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    row = WorkspaceSchemaPreset(
        workspace_id=workspace_id,
        preset_id=preset.id,
        enabled=False,
        applied_by_user_id=user_id,
    )
    db.add(row)
    db.flush([row])
    return row


def set_enabled(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    preset_id: uuid.UUID,
    enabled: bool,
) -> WorkspaceSchemaPreset:
    from datetime import datetime, timezone

    row = db.execute(
        select(WorkspaceSchemaPreset).where(
            WorkspaceSchemaPreset.workspace_id == workspace_id,
            WorkspaceSchemaPreset.preset_id == preset_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise PresetError(
            "PRESET_NOT_APPLIED", "Apply the preset to this workspace first."
        )
    row.enabled = enabled
    # The CHECK refuses enabled=true with no enabled_at, so the timestamp is
    # part of the state rather than an afterthought.
    row.enabled_at = datetime.now(timezone.utc) if enabled else None
    db.flush([row])
    return row


def remove_from_workspace(
    db: Session, *, workspace_id: uuid.UUID, preset_id: uuid.UUID
) -> bool:
    row = db.execute(
        select(WorkspaceSchemaPreset).where(
            WorkspaceSchemaPreset.workspace_id == workspace_id,
            WorkspaceSchemaPreset.preset_id == preset_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


__all__ = [
    "FIELD_TYPES",
    "INDUSTRIES",
    "MAX_FIELDS",
    "PresetError",
    "SAFE_HARBOR_NOTICE",
    "SCALAR_TYPES",
    "applied_presets",
    "apply_to_workspace",
    "get_preset",
    "remove_from_workspace",
    "set_enabled",
    "validate_assertions",
    "validate_preset",
    "validate_preset_schema",
    "validate_redaction_profile",
    "visible_presets",
]
