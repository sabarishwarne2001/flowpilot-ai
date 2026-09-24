"""ARCH43-S1:templates — case templates: draft, publish (immutable), retire.

A published template is immutable (the database trigger refuses any change
to its content, and deleting it); changing one means publishing version n+1,
which retires version n in the same transaction. Cases keep pointing at the
version they were assembled under, so a rule change never silently re-grades
yesterday's cases.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.cases import CaseTemplate
from app.services.cases import dsl
from app.services.cases import vocabulary as v

_KEY = re.compile(r"^[a-z0-9][a-z0-9_\-]{1,63}$")
_DOC = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class TemplateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_required(required: Any) -> list[dict[str, Any]]:
    if not isinstance(required, list) or not required or len(required) > v.MAX_REQUIRED:
        raise TemplateError("INVALID_REQUIRED", f"required_documents must list 1-{v.MAX_REQUIRED} document types")
    out, seen = [], set()
    for item in required:
        doc_type = str((item or {}).get("doc_type") or "") if isinstance(item, dict) else ""
        if not _DOC.match(doc_type) or doc_type in seen:
            raise TemplateError("INVALID_REQUIRED", f"{doc_type!r} is not a unique document type key")
        seen.add(doc_type)
        min_count = int(item.get("min_count", 1))
        if not 1 <= min_count <= 50:
            raise TemplateError("INVALID_REQUIRED", f"{doc_type}: min_count must be 1-50")
        out.append({"doc_type": doc_type, "label": str(item.get("label") or doc_type.replace("_", " ").title())[:120],
                    "min_count": min_count})
    return out


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    key = str(payload.get("key") or "").strip().lower()
    if not _KEY.match(key):
        raise TemplateError("INVALID_KEY", "key must be 2-64 lowercase letters, digits, - or _")
    assembly = str(payload.get("assembly_key") or v.ASSEMBLY_MANUAL).upper()
    if assembly not in v.ASSEMBLY_KEYS:
        raise TemplateError("INVALID_ASSEMBLY", f"assembly_key must be one of {', '.join(v.ASSEMBLY_KEYS)}")
    entity_kind = payload.get("entity_kind")
    if assembly == v.ASSEMBLY_ENTITY:
        entity_kind = str(entity_kind or "").upper()
        if entity_kind not in v.ENTITY_KINDS:
            raise TemplateError("INVALID_ENTITY_KIND", f"entity_kind must be one of {', '.join(v.ENTITY_KINDS)}")
    else:
        entity_kind = None
    try:
        rules = dsl.validate_rules(payload.get("rules") or [])
    except dsl.RuleError as exc:
        raise TemplateError("INVALID_RULE", str(exc)) from exc
    if len(rules) > v.MAX_RULES:
        raise TemplateError("INVALID_RULE", f"at most {v.MAX_RULES} rules")
    ttl = int(payload.get("request_ttl_hours") or 72)
    if not 1 <= ttl <= 2160:
        raise TemplateError("INVALID_TTL", "request_ttl_hours must be 1-2160")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise TemplateError("INVALID_NAME", "name is required")
    return {"key": key, "name": name[:160], "description": str(payload.get("description") or "")[:4000],
            "assembly_key": assembly, "entity_kind": entity_kind,
            "entity_role": (str(payload["entity_role"]).strip().lower()[:48] or None) if payload.get("entity_role") else None,
            "required_documents": validate_required(payload.get("required_documents")), "rules": rules,
            "request_ttl_hours": ttl}


def create(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, payload: dict[str, Any],
           actor_user_id: Optional[uuid.UUID]) -> CaseTemplate:
    clean = validate(payload)
    version = (db.execute(select(func.max(CaseTemplate.version)).where(
        CaseTemplate.workspace_id == workspace_id, CaseTemplate.key == clean["key"])).scalar() or 0) + 1
    draft = db.execute(select(CaseTemplate).where(CaseTemplate.workspace_id == workspace_id, CaseTemplate.key == clean["key"],
                                                  CaseTemplate.status == v.TEMPLATE_DRAFT)).scalar_one_or_none()
    if draft is not None:
        raise TemplateError("DRAFT_EXISTS", f"{clean['key']} already has a draft (v{draft.version}); edit that")
    template = CaseTemplate(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, version=version,
                            status=v.TEMPLATE_DRAFT, created_by_user_id=actor_user_id, **clean)
    db.add(template)
    db.flush()
    return template


def update_draft(db: Session, *, template: CaseTemplate, payload: dict[str, Any]) -> CaseTemplate:
    if template.status != v.TEMPLATE_DRAFT:
        raise TemplateError("IMMUTABLE", "a published template cannot change; create a new version")
    clean = validate({**payload, "key": template.key})
    for attr, value in clean.items():
        setattr(template, attr, value)
    db.flush()
    return template


def publish(db: Session, *, template: CaseTemplate) -> CaseTemplate:
    if template.status != v.TEMPLATE_DRAFT:
        raise TemplateError("NOT_DRAFT", "only a draft can be published")
    now = datetime.now(timezone.utc)
    for old in db.execute(select(CaseTemplate).where(CaseTemplate.workspace_id == template.workspace_id,
                                                     CaseTemplate.key == template.key,
                                                     CaseTemplate.status == v.TEMPLATE_PUBLISHED)).scalars():
        old.status, old.retired_at = v.TEMPLATE_RETIRED, now
    db.flush()
    template.status, template.published_at = v.TEMPLATE_PUBLISHED, now
    db.flush()
    return template


def retire(db: Session, *, template: CaseTemplate) -> CaseTemplate:
    if template.status != v.TEMPLATE_PUBLISHED:
        raise TemplateError("NOT_PUBLISHED", "only a published template can be retired")
    template.status, template.retired_at = v.TEMPLATE_RETIRED, datetime.now(timezone.utc)
    db.flush()
    return template


def document_types(template: CaseTemplate) -> set[str]:
    return {r["doc_type"] for r in template.required_documents or []} | dsl.doc_types_of(template.rules or [])


__all__ = ["TemplateError", "create", "document_types", "publish", "retire", "update_draft", "validate"]
