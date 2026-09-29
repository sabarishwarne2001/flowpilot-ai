"""ARCH-50 — an organization's egress lockdown: the switch, the allow rules, the refusals, a dry-run test.

ARCH50-S1:egress-policy

The DECISION lives in `app.core.egress` (every outbound client consults it); this module only reads and writes
the organization's policy. Every write is audited (ORGANIZATION / UPDATED with an `operation`) and drops this
process's cached copy at once; other workers pick the change up within `egress.POLICY_TTL_SECONDS`.

A LOCKDOWN OUTLIVES A DOWNGRADE. Editing the policy needs `capability.egress_lockdown`; ENFORCING it does not.
An organization that switched lockdown on and then left Enterprise keeps its restriction -- lifting a data
control because a payment lapsed would open egress without anyone deciding to. The operator can clear it
(`scripts/egress_admin.py clear --organization <id>`), audited.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlsplit

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import egress
from app.core.exceptions import FlowPilotError

MAX_RULES_PER_ORGANIZATION = 200
MAX_NOTE_CHARS = 200
REFUSAL_RETENTION_DAYS = 90


_BY_CODE: dict[tuple[str, int], type] = {}


class EgressPolicyError(FlowPilotError):
    """The domain exception handler maps by CLASS, so each (code, status) gets a subclass carrying them (a 422
    INVALID_PATTERN renders as 422 INVALID_PATTERN, not 409 EGRESS_POLICY_CONFLICT)."""

    status_code = 409
    code = "EGRESS_POLICY_CONFLICT"

    def __new__(cls, message: str = "", code: str = "EGRESS_POLICY_CONFLICT", status_code: int = 409,
                **details: Any) -> Any:
        key = (str(code), int(status_code))
        sub = _BY_CODE.get(key)
        if sub is None:
            sub = type(f"EgressPolicyError_{key[0]}_{key[1]}", (EgressPolicyError,),
                       {"status_code": key[1], "code": key[0]})
            _BY_CODE[key] = sub
        return super().__new__(sub, message)

    def __init__(self, message: str, code: str, status_code: int = 409, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = {"code": code, **details}


def _now() -> datetime:
    return egress.now()


def _audit(db: Session, *, organization_id: uuid.UUID, actor_id: Optional[uuid.UUID], operation: str,
           **details: Any) -> None:
    from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
    from app.services import audit_service

    audit_service.record(db, organization_id=organization_id, actor_id=actor_id,
                         resource_type=AuditResourceType.ORGANIZATION, resource_id=organization_id,
                         action=AuditAction.UPDATED, outcome=AuditOutcome.ALLOWED,
                         details={"operation": operation, **{k: (str(v) if v is not None else None)
                                                             for k, v in details.items()}})


def _rule_out(row: Any) -> dict[str, Any]:
    return {"id": row.id, "channel": row.channel, "host_pattern": row.host_pattern, "port": row.port,
            "note": row.note, "created_at": row.created_at, "created_by_user_id": row.created_by_user_id}


def get_policy(db: Session, *, organization_id: uuid.UUID) -> dict[str, Any]:
    row = db.execute(text("SELECT lockdown_enabled, updated_at, updated_by_user_id FROM egress_policies "
                          "WHERE organization_id = :o"), {"o": organization_id}).first()
    rules = db.execute(text("SELECT id, channel, host_pattern, port, note, created_at, created_by_user_id "
                            "FROM egress_allow_rules WHERE organization_id = :o ORDER BY created_at, id"),
                       {"o": organization_id}).all()
    return {
        "organization_id": organization_id,
        "lockdown_enabled": bool(row[0]) if row else False,
        "updated_at": row[1] if row else None,
        "updated_by_user_id": row[2] if row else None,
        "rules": [_rule_out(r) for r in rules],
        "max_rules": MAX_RULES_PER_ORGANIZATION,
        "deployment_mode": egress.current_mode(),
        "governed_channels": [c for c in egress.CHANNELS if c in egress.TENANT_CHANNELS],
    }


def set_lockdown(db: Session, *, organization_id: uuid.UUID, enabled: bool,
                 actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    db.execute(text(
        "INSERT INTO egress_policies (organization_id, lockdown_enabled, updated_by_user_id, created_at, updated_at) "
        "VALUES (:o, :e, :u, now(), now()) ON CONFLICT (organization_id) DO UPDATE SET "
        "lockdown_enabled = EXCLUDED.lockdown_enabled, updated_by_user_id = EXCLUDED.updated_by_user_id, "
        "updated_at = GREATEST(now(), egress_policies.created_at)"),
        {"o": organization_id, "e": bool(enabled), "u": actor_id})
    _audit(db, organization_id=organization_id, actor_id=actor_id, operation="egress.lockdown",
           lockdown_enabled=bool(enabled))
    db.flush()
    egress.clear_cache(organization_id)
    return get_policy(db, organization_id=organization_id)


def operator_clear(db: Session, *, organization_id: uuid.UUID, reason: str) -> dict[str, Any]:
    """The operator lifts an organization's lockdown (scripts/egress_admin.py clear) -- the one path that needs no
    capability, for an organization that left Enterprise with the lockdown on. The rules stay, so switching the
    lockdown back on restores the same allowlist. Audited with the operator's reason; the caller commits."""
    why = " ".join(str(reason or "").split())[:MAX_NOTE_CHARS]
    if not why:
        raise EgressPolicyError("say why the lockdown is being lifted", "REASON_REQUIRED", 422)
    exists = db.execute(text("SELECT 1 FROM organizations WHERE id = :o"), {"o": organization_id}).first()
    if exists is None:
        raise EgressPolicyError("no such organization", "ORGANIZATION_NOT_FOUND", 404)
    db.execute(text("UPDATE egress_policies SET lockdown_enabled = false, updated_by_user_id = NULL, "
                    "updated_at = GREATEST(now(), created_at) WHERE organization_id = :o"), {"o": organization_id})
    _audit(db, organization_id=organization_id, actor_id=None, operation="egress.lockdown_cleared_by_operator",
           lockdown_enabled=False, reason=why)
    db.flush()
    egress.clear_cache(organization_id)
    return get_policy(db, organization_id=organization_id)


def add_rule(db: Session, *, organization_id: uuid.UUID, channel: Optional[str], host_pattern: str,
             port: Optional[int], note: Optional[str], actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    if channel is not None and channel not in egress.TENANT_CHANNELS:
        raise EgressPolicyError(f"{channel!r} is not a channel an organization governs", "CHANNEL_NOT_GOVERNED",
                                422, channel=channel)
    try:
        pattern = egress.parse_pattern(host_pattern, port)
    except egress.PatternError as exc:
        raise EgressPolicyError(str(exc), "INVALID_PATTERN", 422) from exc
    stored = pattern.value if pattern.kind != "SUFFIX" else f"*.{pattern.value}"
    note_text = (note or "").strip()[:MAX_NOTE_CHARS] or None
    # A lock on the organization's policy row serialises concurrent adds against the cap.
    db.execute(text("INSERT INTO egress_policies (organization_id) VALUES (:o) ON CONFLICT DO NOTHING"),
               {"o": organization_id})
    db.execute(text("SELECT 1 FROM egress_policies WHERE organization_id = :o FOR UPDATE"), {"o": organization_id})
    count = db.execute(text("SELECT count(*) FROM egress_allow_rules WHERE organization_id = :o"),
                       {"o": organization_id}).scalar_one()
    if count >= MAX_RULES_PER_ORGANIZATION:
        raise EgressPolicyError(f"an organization holds at most {MAX_RULES_PER_ORGANIZATION} rules", "TOO_MANY_RULES")
    exists = db.execute(text(
        "SELECT id FROM egress_allow_rules WHERE organization_id = :o AND COALESCE(channel, '') = :c "
        "AND host_pattern = :h AND COALESCE(port, 0) = :p"),
        {"o": organization_id, "c": channel or "", "h": stored, "p": int(pattern.port or 0)}).first()
    if exists:
        raise EgressPolicyError("this rule already exists", "RULE_EXISTS", rule_id=str(exists[0]))
    rule_id = uuid.uuid4()
    db.execute(text(
        "INSERT INTO egress_allow_rules (id, organization_id, channel, host_pattern, port, note, created_by_user_id) "
        "VALUES (:id, :o, :c, :h, :p, :n, :u)"),
        {"id": rule_id, "o": organization_id, "c": channel, "h": stored, "p": pattern.port, "n": note_text,
         "u": actor_id})
    _audit(db, organization_id=organization_id, actor_id=actor_id, operation="egress.rule_added",
           rule_id=rule_id, channel=channel, host_pattern=stored, port=pattern.port)
    db.flush()
    egress.clear_cache(organization_id)
    row = db.execute(text("SELECT id, channel, host_pattern, port, note, created_at, created_by_user_id "
                          "FROM egress_allow_rules WHERE id = :id"), {"id": rule_id}).one()
    return _rule_out(row)


def delete_rule(db: Session, *, organization_id: uuid.UUID, rule_id: uuid.UUID,
                actor_id: Optional[uuid.UUID]) -> None:
    row = db.execute(text("DELETE FROM egress_allow_rules WHERE id = :id AND organization_id = :o "
                          "RETURNING channel, host_pattern, port"), {"id": rule_id, "o": organization_id}).first()
    if row is None:
        raise EgressPolicyError("no such rule in this organization", "RULE_NOT_FOUND", 404)
    _audit(db, organization_id=organization_id, actor_id=actor_id, operation="egress.rule_removed",
           rule_id=rule_id, channel=row[0], host_pattern=row[1], port=row[2])
    db.flush()
    egress.clear_cache(organization_id)


def list_refusals(db: Session, *, organization_id: Optional[uuid.UUID], days: int = 7,
                  limit: int = 200) -> list[dict[str, Any]]:
    since = _now() - timedelta(days=max(1, min(int(days), REFUSAL_RETENTION_DAYS)))
    where = "organization_id = :o" if organization_id is not None else "TRUE"
    rows = db.execute(text(
        f"SELECT id, organization_id, channel, host, port, reason, mode, bucket_start, first_at, last_at, count "
        f"FROM egress_refusals WHERE {where} AND last_at >= :since ORDER BY last_at DESC, id LIMIT :lim"),
        {"o": organization_id, "since": since, "lim": max(1, min(int(limit), 1000))}).mappings().all()
    return [dict(r) for r in rows]


def parse_destination(destination: str) -> tuple[str, Optional[int]]:
    raw = str(destination or "").strip()
    if "://" in raw:
        parts = urlsplit(raw)
        port = parts.port or {"https": 443, "http": 80, "sftp": 22, "smtp": 25, "smtps": 465}.get(parts.scheme)
        return egress.normalize_host(parts.hostname or ""), port
    if raw.startswith("["):
        host, _, rest = raw[1:].partition("]")
        return egress.normalize_host(host), int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else None
    if raw.count(":") == 1:
        host, _, port = raw.partition(":")
        return egress.normalize_host(host), int(port) if port.isdigit() else None
    return egress.normalize_host(raw), None


def test_destination(db: Session, *, organization_id: uuid.UUID, channel: str,
                     destination: str) -> dict[str, Any]:
    """What WOULD happen, read inside the caller's transaction and never recorded as a refusal."""
    if channel not in egress.CHANNELS:
        raise EgressPolicyError(f"unknown channel {channel!r}", "UNKNOWN_CHANNEL", 422)
    host, port = parse_destination(destination)
    return egress.decide(channel, host, port, organization_id=organization_id, db=db).as_dict()


def prune_refusals(db: Session, *, older_than_days: int = REFUSAL_RETENTION_DAYS) -> int:
    cutoff = _now() - timedelta(days=older_than_days)
    return int(db.execute(text("DELETE FROM egress_refusals WHERE last_at < :c"), {"c": cutoff}).rowcount or 0)


__all__ = ["EgressPolicyError", "MAX_RULES_PER_ORGANIZATION", "add_rule", "delete_rule", "get_policy",
           "list_refusals", "operator_clear", "parse_destination", "prune_refusals", "set_lockdown",
           "test_destination"]
