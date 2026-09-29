"""ARCH47-S1:service — targets, mapping versions, lookup tables, and THE LEDGER.

EXACTLY ONCE
============

One posting per (target, object kind, source kind, source id): the UNIQUE key
of erp_postings. Everything that can ask for a posting -- a person's "Post", a
target's auto-post sweep, the erp.post Flow Builder action, a retry, a second
worker -- goes through `plan()`, which INSERTs ... ON CONFLICT DO NOTHING on that
key and otherwise returns the posting that already exists. The rendered bytes
are frozen when it is planned; every attempt sends those bytes.

Delivery (`deliver()`, the erp.deliver_posting job) is three steps:

  1. CLAIM    SELECT ... FOR UPDATE SKIP LOCKED; only a PENDING / due RETRYING
              posting, or a SENDING one whose lease EXPIRED, is taken; it becomes
              SENDING with a fresh lease token, attempts + 1, and the claim is
              COMMITTED before any network I/O (a second worker skips the locked
              row, then sees SENDING with a live lease and leaves it alone).
  2. TALK     no database transaction is held. A claim that reclaimed an expired
              lease, or a send whose outcome is uncertain, PROBES the target (the
              object by our document number; the SFTP file by name and sha256)
              before anything is sent again. A target that cannot be probed makes
              the posting UNCERTAIN: a person decides (the review hub). Nothing is
              ever sent blind twice.
  3. RECORD   the outcome is written only if the lease token is still ours.

A posting is DONE only when the target says so: a created record whose echoed
figures match what was sent (HTTP), a 997 or ack file naming it (SFTP), the
delivered file itself (SFTP DELIVERY mode, stated as delivery-level), or a
person's confirmation (DOWNLOAD). An acknowledgement that contradicts what was
sent is MISMATCH, never DONE.

FAILED / REJECTED / MISMATCH / UNCERTAIN are the review hub's POSTING kind and
raise the posting.failed Flow Builder trigger once per entry into an exception
state (idempotency key <event>:<posting>:<exception number>).

One clock: `now()` (the gates pin it).
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.erp import ErpLookupTable, ErpMapping, ErpPosting, ErpPostingAttempt, ErpTarget
from app.services import audit_service
from app.services.erp import canonical as C
from app.services.erp import gate
from app.services.erp import mapping as M
from app.services.erp import presets as PR
from app.services.erp import render as R
from app.services.erp import sources as S
from app.services.erp import vocabulary as v
from app.services.erp.formats import jsonapi, tally, x12

logger = logging.getLogger("app.services.erp.service")


class ErpError(ValueError):
    def __init__(self, code: str, message: str, problems: Optional[list[str]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.problems = problems or []


def now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, actor: Optional[uuid.UUID],
           operation: str, action: AuditAction = AuditAction.UPDATED, outcome: AuditOutcome = AuditOutcome.ALLOWED,
           **extra: Any) -> None:
    audit_service.record(db, organization_id=organization_id, workspace_id=workspace_id, actor_id=actor,
                         resource_type=AuditResourceType.WORKSPACE, resource_id=workspace_id, action=action,
                         outcome=outcome, details={"erp_posting": {"operation": operation, **extra}})


def _workspace(db: Session, workspace_id: uuid.UUID) -> Any:
    from app.models.workspace import Workspace

    ws = db.get(Workspace, workspace_id)
    if ws is None:
        raise ErpError("NOT_FOUND", "Workspace not found.")
    return ws


def local_today(db: Session, workspace_id: uuid.UUID, at: Optional[datetime] = None) -> date:
    from app.services.obligations import temporal as T

    return T.local_today(at or now(), _workspace(db, workspace_id).timezone)


# ---------------------------------------------------------------------------
# credentials: one Fernet ciphertext per target (the configured EMAIL_ENCRYPTION_KEYS)
# ---------------------------------------------------------------------------

_CREDENTIAL_FIELDS = {
    v.AUTH_BEARER: ({"token"}, set()),
    v.AUTH_BASIC: ({"username", "password"}, set()),
    v.AUTH_API_KEY: ({"value"}, {"header"}),
    v.AUTH_OAUTH2_REFRESH: ({"client_id", "client_secret", "refresh_token"}, {"access_token", "expires_at"}),
    v.AUTH_OAUTH2_CLIENT: ({"client_id", "client_secret"}, {"access_token", "expires_at"}),
    v.AUTH_SSH_PASSWORD: ({"username", "password"}, set()),
    v.AUTH_SSH_KEY: ({"username", "private_key"}, {"passphrase"}),
}


def _check_credential(auth_mode: str, credential: Mapping[str, Any]) -> dict:
    if auth_mode not in _CREDENTIAL_FIELDS:
        raise ErpError("NO_CREDENTIAL", f"{auth_mode} takes no credential")
    required, optional = _CREDENTIAL_FIELDS[auth_mode]
    unknown = set(credential) - required - optional
    missing = [k for k in sorted(required) if not str(credential.get(k) or "").strip()]
    if unknown:
        raise ErpError("CREDENTIAL", f"unknown credential field(s): {sorted(unknown)}")
    if missing:
        raise ErpError("CREDENTIAL", f"the credential needs {', '.join(missing)}")
    if any(len(str(val)) > 16000 for val in credential.values()):
        raise ErpError("CREDENTIAL", "a credential value is too long")
    return {k: (val if k == "expires_at" else str(val)) for k, val in credential.items() if val not in (None, "")}


def _encrypt(credential: Mapping[str, Any]) -> tuple[str, str]:
    from app.core.encryption import encrypt_secret

    plain = json.dumps(dict(credential), sort_keys=True)
    return encrypt_secret(plain), hashlib.sha256(plain.encode()).hexdigest()[:12]


def credential_of(target: ErpTarget) -> Optional[dict]:
    if not target.credential_ciphertext:
        return None
    from app.core.encryption import DecryptionError, decrypt_secret

    try:
        return json.loads(decrypt_secret(target.credential_ciphertext))
    except (DecryptionError, ValueError) as exc:
        raise ErpError("CREDENTIAL_UNREADABLE", "the target's credential cannot be decrypted under the configured "
                                                "keys; set it again") from exc


def set_credential(db: Session, *, target: ErpTarget, credential: Mapping[str, Any], actor_user_id: uuid.UUID) -> ErpTarget:
    clean = _check_credential(target.auth_mode, credential)
    target.credential_ciphertext, target.credential_fingerprint = _encrypt(clean)
    target.credential_updated_at = now()
    target.updated_at = now()
    target.revision += 1
    db.flush()
    _audit(db, organization_id=target.organization_id, workspace_id=target.workspace_id, actor=actor_user_id,
           operation="credential_set", action=AuditAction.ROTATED, target_id=str(target.id),
           fingerprint=target.credential_fingerprint, auth_mode=target.auth_mode)
    return target


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------

_PREFIX = re.compile(r"^[a-z][a-z0-9_]{0,24}$")


def _preflight_url(url: str, what: str) -> None:
    from app.services import webhook_service

    try:
        webhook_service._preflight_check_url(url)
    except webhook_service.InvalidURLError as exc:
        raise ErpError("URL_REFUSED", f"{what}: {exc}") from exc


def _validate_target(fmt: str, transport: str, preset: str, ack_mode: str, auth_mode: str, config: Mapping[str, Any],
                     *, check_network: bool = True) -> dict:
    from app.services.erp.transport import sftp

    problems = []
    if fmt not in v.FORMATS:
        problems.append(f"unknown format {fmt!r}")
    if transport not in v.TRANSPORTS:
        problems.append(f"unknown transport {transport!r}")
    if preset not in v.PRESETS:
        problems.append(f"unknown preset {preset!r}")
    if problems:
        raise ErpError("INVALID", "; ".join(problems), problems)
    if (fmt == v.FORMAT_JSON) != (transport == v.TRANSPORT_HTTP):
        problems.append("REST / OData JSON is sent over HTTP, and only JSON is")
    if (fmt == v.FORMAT_JSON) != (preset != v.PRESET_NONE):
        problems.append("a JSON target needs a preset (and only a JSON target has one)")
    if ack_mode not in v.ACK_BY_TRANSPORT[transport]:
        problems.append(f"{transport} is acknowledged by {', '.join(v.ACK_BY_TRANSPORT[transport])}")
    if ack_mode == v.ACK_X12_997 and fmt != v.FORMAT_X12:
        problems.append("a 997 acknowledges X12 only")
    if auth_mode not in v.AUTH_BY_TRANSPORT[transport]:
        problems.append(f"{transport} authenticates with {', '.join(v.AUTH_BY_TRANSPORT[transport])}")
    if fmt == v.FORMAT_JSON and preset in jsonapi.PRESETS and auth_mode not in jsonapi.preset(preset).auth:
        problems.append(f"{v.PRESET_LABELS[preset]} authenticates with "
                        f"{', '.join(jsonapi.preset(preset).auth)}")
    cfg = json.loads(json.dumps(dict(config or {})))
    if len(json.dumps(cfg)) > 32768:
        problems.append("the configuration is too large")
    if fmt == v.FORMAT_TALLY and not str((cfg.get("tally") or {}).get("company") or "").strip():
        problems.append("a Tally target needs tally.company (the company to import into)")
    if fmt == v.FORMAT_X12:
        try:
            x12.Envelope.from_config(cfg)
        except x12.X12Error as exc:
            problems.append(str(exc))
    if fmt == v.FORMAT_CSV and (cfg.get("csv") or {}).get("delimiter", ",") not in (",", ";"):
        problems.append("csv.delimiter is ',' or ';'")
    if transport == v.TRANSPORT_HTTP:
        from app.services.erp.transport import http

        try:
            url = http.base_url(cfg)
            if check_network:
                _preflight_url(url, "http.base_url")
        except http.HttpError as exc:
            problems.append(str(exc))
        if auth_mode in (v.AUTH_OAUTH2_REFRESH, v.AUTH_OAUTH2_CLIENT):
            token_url = str((cfg.get("oauth") or {}).get("token_url") or "")
            if not token_url.startswith("https://"):
                problems.append("OAuth needs oauth.token_url (https://)")
            elif check_network:
                _preflight_url(token_url, "oauth.token_url")
        preset_def = jsonapi.PRESETS.get(preset)
        if preset_def is not None:
            section = {v.PRESET_QBO: "qbo", v.PRESET_ZOHO: "zoho", v.PRESET_BC: "bc", v.PRESET_S4: "s4"}.get(preset)
            for key in preset_def.config_keys:
                if not str((cfg.get(section) or {}).get(key) or "").strip():
                    problems.append(f"{v.PRESET_LABELS[preset]} needs {section}.{key}")
            if preset in (v.PRESET_GENERIC_REST, v.PRESET_GENERIC_ODATA):
                paths = (cfg.get("rest") or {}).get("paths") or {}
                if not isinstance(paths, dict) or not paths:
                    problems.append("a generic target needs rest.paths (object kind -> path)")
                elif any(k not in v.OBJECT_KINDS or not str(p).startswith("/") for k, p in paths.items()):
                    problems.append("rest.paths maps object kinds to paths beginning with '/'")
    if transport == v.TRANSPORT_SFTP:
        try:
            s = sftp.settings(cfg)
            if check_network and not sftp.allow_private_for_tests:
                from app.core import ssrf_client

                try:
                    ssrf_client.resolve_and_validate(s["host"], s["port"], timeout=5.0)
                except ssrf_client.ForbiddenAddressError as exc:
                    problems.append(f"sftp.host: {exc}")
                except (ssrf_client.DNSResolutionError, ssrf_client.TimeoutExceededError):
                    pass  # unresolvable now is not refused (it is checked again at every connect)
        except sftp.SftpError as exc:
            problems.append(str(exc))
    if problems:
        raise ErpError("INVALID", "; ".join(problems), problems)
    return cfg


def _ensure_lookup_tables(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, prefix: str,
                          actor: Optional[uuid.UUID]) -> None:
    for name in PR.table_names(prefix).values():
        stmt = pg_insert(ErpLookupTable).values(
            id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, name=name,
            description="Created with the target; fill it before posting.", entries={}, created_by_user_id=actor)
        db.execute(stmt.on_conflict_do_nothing(index_elements=["workspace_id", "name"]))


def create_target(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, actor_user_id: uuid.UUID,
                  name: str, format: str, transport: str, preset: str = v.PRESET_NONE, ack_mode: Optional[str] = None,
                  auth_mode: Optional[str] = None, config: Optional[Mapping[str, Any]] = None,
                  credential: Optional[Mapping[str, Any]] = None, lookup_prefix: Optional[str] = None,
                  auto_post: bool = False, auto_sources: Sequence[str] = (), auto_objects: Sequence[str] = (),
                  max_attempts: Optional[int] = None, ack_timeout_hours: Optional[int] = None,
                  check_network: bool = True) -> ErpTarget:
    count = db.execute(select(func.count()).select_from(ErpTarget).where(ErpTarget.workspace_id == workspace_id)).scalar_one()
    if count >= v.MAX_TARGETS_PER_WORKSPACE:
        raise ErpError("TOO_MANY_TARGETS", f"at most {v.MAX_TARGETS_PER_WORKSPACE} targets per workspace")
    ack_mode = ack_mode or v.ACK_BY_TRANSPORT.get(transport, (None,))[0]
    auth_mode = auth_mode or (jsonapi.preset(preset).auth[0] if preset in jsonapi.PRESETS
                              else v.AUTH_BY_TRANSPORT.get(transport, (v.AUTH_NONE,))[0])
    cfg = _validate_target(format, transport, preset, ack_mode or "", auth_mode, config or {}, check_network=check_network)
    clean_name = " ".join(str(name or "").split())
    if not 1 <= len(clean_name) <= 120:
        raise ErpError("INVALID", "a target needs a name (1 to 120 characters)")
    if db.execute(select(ErpTarget.id).where(ErpTarget.workspace_id == workspace_id,
                                             ErpTarget.name == clean_name)).first():
        raise ErpError("NAME_TAKEN", f"a target named {clean_name!r} already exists")
    prefix = (lookup_prefix or re.sub(r"[^a-z0-9]+", "_", clean_name.lower()).strip("_")[:20] or "erp")
    if not prefix[0].isalpha():
        prefix = "t_" + prefix
    prefix = prefix[:25]
    if not _PREFIX.match(prefix):
        raise ErpError("INVALID", "lookup_prefix is lower-case letters, digits and underscores, starting with a letter")
    cfg["lookup_prefix"] = prefix
    sources_, objects_ = _auto(auto_sources, auto_objects)
    target = ErpTarget(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, name=clean_name,
                       format=format, transport=transport, preset=preset, ack_mode=ack_mode, auth_mode=auth_mode,
                       status=v.TARGET_ACTIVE, config=cfg, auto_post=bool(auto_post),
                       auto_post_since=now() if auto_post else None, auto_sources=sources_, auto_objects=objects_,
                       max_attempts=_attempts(max_attempts), ack_timeout_hours=_ack_hours(ack_timeout_hours),
                       created_by_user_id=actor_user_id, created_at=now(), updated_at=now())
    db.add(target)
    db.flush()
    _ensure_lookup_tables(db, organization_id=organization_id, workspace_id=workspace_id, prefix=prefix,
                          actor=actor_user_id)
    tables = set(PR.table_names(prefix).values())
    for kind in PR.supported_objects(format, preset):
        spec = PR.default_mapping(format, preset, kind, prefix)
        M.validate(spec, object_kind=kind, contract=PR.contract(format, preset, kind), lookup_tables=tables)
        db.add(ErpMapping(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id,
                          target_id=target.id, object_kind=kind, version=1, status=v.MAPPING_ACTIVE, spec=spec,
                          spec_sha=M.spec_sha(spec), note="Default mapping", created_by_user_id=actor_user_id,
                          created_at=now()))
    db.flush()
    _audit(db, organization_id=organization_id, workspace_id=workspace_id, actor=actor_user_id, operation="target_created",
           action=AuditAction.DESTINATION_CREATED, target_id=str(target.id), format=format, transport=transport,
           preset=preset, host=_host(cfg, transport))
    if credential:
        set_credential(db, target=target, credential=credential, actor_user_id=actor_user_id)
    return target


def _host(cfg: Mapping[str, Any], transport: str) -> Optional[str]:
    from urllib.parse import urlsplit

    if transport == v.TRANSPORT_HTTP:
        return urlsplit(str((cfg.get("http") or {}).get("base_url") or "")).hostname
    if transport == v.TRANSPORT_SFTP:
        return str((cfg.get("sftp") or {}).get("host") or "") or None
    return None


def _attempts(value: Optional[int]) -> int:
    n = v.DEFAULT_MAX_ATTEMPTS if value is None else int(value)
    if not 1 <= n <= v.MAX_MAX_ATTEMPTS:
        raise ErpError("INVALID", f"max_attempts is 1 to {v.MAX_MAX_ATTEMPTS}")
    return n


def _ack_hours(value: Optional[int]) -> int:
    n = v.DEFAULT_ACK_TIMEOUT_HOURS if value is None else int(value)
    if not 1 <= n <= 720:
        raise ErpError("INVALID", "ack_timeout_hours is 1 to 720")
    return n


def _auto(sources_: Sequence[str], objects_: Sequence[str]) -> tuple[list[str], list[str]]:
    bad = [s for s in sources_ if s not in v.SOURCE_KINDS] + [o for o in objects_ if o not in v.OBJECT_KINDS]
    if bad:
        raise ErpError("INVALID", f"unknown auto-post source or object kind(s): {bad}")
    return sorted(set(sources_)), sorted(set(objects_))


def update_target(db: Session, *, target: ErpTarget, actor_user_id: uuid.UUID, changes: Mapping[str, Any],
                  check_network: bool = True) -> ErpTarget:
    has_postings = db.execute(select(ErpPosting.id).where(ErpPosting.target_id == target.id).limit(1)).first()
    if any(k in changes and changes[k] != getattr(target, k) for k in ("format", "transport", "preset")) and has_postings:
        raise ErpError("HAS_POSTINGS", "the format, transport and preset of a target with postings cannot change "
                                       "(create a new target)")
    fmt = changes.get("format") or target.format
    transport = changes.get("transport") or target.transport
    preset = changes.get("preset") or target.preset
    ack_mode = changes.get("ack_mode") or target.ack_mode
    auth_mode = changes.get("auth_mode") or target.auth_mode
    config = dict(changes["config"]) if changes.get("config") is not None else dict(target.config)
    config.setdefault("lookup_prefix", target.config.get("lookup_prefix"))
    cfg = _validate_target(fmt, transport, preset, ack_mode, auth_mode, config, check_network=check_network)
    if auth_mode != target.auth_mode and target.credential_ciphertext:
        target.credential_ciphertext = target.credential_fingerprint = None
        target.credential_updated_at = None
    if "name" in changes and changes["name"] is not None:
        clean = " ".join(str(changes["name"]).split())
        if not 1 <= len(clean) <= 120:
            raise ErpError("INVALID", "a target needs a name (1 to 120 characters)")
        if clean != target.name and db.execute(select(ErpTarget.id).where(
                ErpTarget.workspace_id == target.workspace_id, ErpTarget.name == clean)).first():
            raise ErpError("NAME_TAKEN", f"a target named {clean!r} already exists")
        target.name = clean
    target.format, target.transport, target.preset = fmt, transport, preset
    target.ack_mode, target.auth_mode, target.config = ack_mode, auth_mode, cfg
    if "status" in changes and changes["status"] is not None:
        if changes["status"] not in v.TARGET_STATUSES:
            raise ErpError("INVALID", "status is ACTIVE or DISABLED")
        target.status = changes["status"]
    if "auto_post" in changes and changes["auto_post"] is not None:
        turning_on = bool(changes["auto_post"]) and not target.auto_post
        target.auto_post = bool(changes["auto_post"])
        if turning_on:
            target.auto_post_since = now()   # only outcomes approved from now on post themselves
    if changes.get("auto_sources") is not None or changes.get("auto_objects") is not None:
        target.auto_sources, target.auto_objects = _auto(
            changes.get("auto_sources") if changes.get("auto_sources") is not None else target.auto_sources,
            changes.get("auto_objects") if changes.get("auto_objects") is not None else target.auto_objects)
    if changes.get("max_attempts") is not None:
        target.max_attempts = _attempts(changes["max_attempts"])
    if changes.get("ack_timeout_hours") is not None:
        target.ack_timeout_hours = _ack_hours(changes["ack_timeout_hours"])
    target.revision += 1
    target.updated_at = now()
    db.flush()
    # a new format/preset gets default mappings for the kinds it now supports
    prefix = cfg.get("lookup_prefix") or "erp"
    _ensure_lookup_tables(db, organization_id=target.organization_id, workspace_id=target.workspace_id, prefix=prefix,
                          actor=actor_user_id)
    have = {m for (m,) in db.execute(select(ErpMapping.object_kind).where(ErpMapping.target_id == target.id,
                                                                          ErpMapping.status == v.MAPPING_ACTIVE))}
    for kind in PR.supported_objects(fmt, preset):
        if kind not in have:
            _new_version(db, target, kind, PR.default_mapping(fmt, preset, kind, prefix), "Default mapping",
                         actor_user_id)
    _audit(db, organization_id=target.organization_id, workspace_id=target.workspace_id, actor=actor_user_id,
           operation="target_updated", target_id=str(target.id), fields=sorted(changes))
    return target


def delete_target(db: Session, *, target: ErpTarget, actor_user_id: uuid.UUID) -> None:
    if db.execute(select(ErpPosting.id).where(ErpPosting.target_id == target.id).limit(1)).first():
        raise ErpError("HAS_POSTINGS", "a target with postings is part of the ledger; disable it instead")
    _audit(db, organization_id=target.organization_id, workspace_id=target.workspace_id, actor=actor_user_id,
           operation="target_deleted", action=AuditAction.DELETED, target_id=str(target.id), name=target.name)
    db.delete(target)
    db.flush()


# ---------------------------------------------------------------------------
# mappings and lookup tables
# ---------------------------------------------------------------------------


def lookup_names(db: Session, workspace_id: uuid.UUID) -> set[str]:
    return set(db.execute(select(ErpLookupTable.name).where(ErpLookupTable.workspace_id == workspace_id)).scalars())


def lookups(db: Session, workspace_id: uuid.UUID) -> dict[str, dict[str, str]]:
    return {t.name: dict(t.entries or {}) for t in
            db.execute(select(ErpLookupTable).where(ErpLookupTable.workspace_id == workspace_id)).scalars()}


def active_mapping(db: Session, target: ErpTarget, object_kind: str) -> Optional[ErpMapping]:
    return db.execute(select(ErpMapping).where(ErpMapping.target_id == target.id, ErpMapping.object_kind == object_kind,
                                               ErpMapping.status == v.MAPPING_ACTIVE)).scalar_one_or_none()


def _new_version(db: Session, target: ErpTarget, object_kind: str, spec: Mapping, note: Optional[str],
                 actor: Optional[uuid.UUID]) -> ErpMapping:
    current = active_mapping(db, target, object_kind)
    last = db.execute(select(func.max(ErpMapping.version)).where(ErpMapping.target_id == target.id,
                                                                  ErpMapping.object_kind == object_kind)).scalar()
    if current is not None:
        current.status, current.retired_at = v.MAPPING_RETIRED, now()
        db.flush()
    row = ErpMapping(id=uuid.uuid4(), organization_id=target.organization_id, workspace_id=target.workspace_id,
                     target_id=target.id, object_kind=object_kind, version=(last or 0) + 1, status=v.MAPPING_ACTIVE,
                     spec=dict(spec), spec_sha=M.spec_sha(spec), note=(note or "")[:300] or None,
                     created_by_user_id=actor, created_at=now())
    db.add(row)
    db.flush()
    return row


def validate_mapping(db: Session, target: ErpTarget, object_kind: str, spec: Any) -> dict:
    if object_kind not in PR.supported_objects(target.format, target.preset):
        raise ErpError("UNSUPPORTED", f"this target does not take a {v.OBJECT_LABELS[object_kind].lower()}")
    try:
        return M.validate(spec, object_kind=object_kind, contract=PR.contract(target.format, target.preset, object_kind),
                          lookup_tables=lookup_names(db, target.workspace_id))
    except M.MappingError as exc:
        raise ErpError("MAPPING_INVALID", "the mapping was refused", exc.problems) from exc


def save_mapping(db: Session, *, target: ErpTarget, object_kind: str, spec: Any, note: Optional[str],
                 actor_user_id: uuid.UUID) -> ErpMapping:
    clean = validate_mapping(db, target, object_kind, spec)
    current = active_mapping(db, target, object_kind)
    if current is not None and current.spec_sha == M.spec_sha(clean):
        return current
    row = _new_version(db, target, object_kind, clean, note, actor_user_id)
    _audit(db, organization_id=target.organization_id, workspace_id=target.workspace_id, actor=actor_user_id,
           operation="mapping_saved", target_id=str(target.id), object_kind=object_kind, version=row.version,
           spec_sha=row.spec_sha)
    return row


def restore_default_mapping(db: Session, *, target: ErpTarget, object_kind: str, actor_user_id: uuid.UUID) -> ErpMapping:
    prefix = target.config.get("lookup_prefix") or "erp"
    return save_mapping(db, target=target, object_kind=object_kind, note="Default mapping restored",
                        spec=PR.default_mapping(target.format, target.preset, object_kind, prefix),
                        actor_user_id=actor_user_id)


def _entries(raw: Any) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise ErpError("INVALID", "entries is an object of string keys to string values")
    if len(raw) > v.MAX_LOOKUP_ENTRIES:
        raise ErpError("INVALID", f"at most {v.MAX_LOOKUP_ENTRIES} entries")
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, (Mapping, list, tuple, set)):
            raise ErpError("INVALID", f"{key!r}: a lookup value is text (a nested object or list is not a code)")
        k, val = str(key), "" if value is None else str(value)
        if not 1 <= len(k) <= 200 or len(val) > 500:
            raise ErpError("INVALID", "keys are 1 to 200 characters, values at most 500")
        if any(ord(ch) < 32 for ch in k + val):
            raise ErpError("INVALID", "control characters are not allowed in a lookup table")
        out[k] = val
    return out


def create_lookup(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, actor_user_id: uuid.UUID,
                  name: str, description: Optional[str], entries: Any) -> ErpLookupTable:
    name = str(name or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
        raise ErpError("INVALID", "a lookup table name is lower-case letters, digits and underscores")
    if db.execute(select(func.count()).select_from(ErpLookupTable).where(
            ErpLookupTable.workspace_id == workspace_id)).scalar_one() >= v.MAX_LOOKUP_TABLES:
        raise ErpError("TOO_MANY", f"at most {v.MAX_LOOKUP_TABLES} lookup tables per workspace")
    if name in lookup_names(db, workspace_id):
        raise ErpError("NAME_TAKEN", f"a lookup table named {name!r} already exists")
    row = ErpLookupTable(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, name=name,
                         description=(description or "")[:300] or None, entries=_entries(entries or {}),
                         created_by_user_id=actor_user_id, created_at=now(), updated_at=now())
    db.add(row)
    db.flush()
    _audit(db, organization_id=organization_id, workspace_id=workspace_id, actor=actor_user_id,
           operation="lookup_created", action=AuditAction.CREATED, name=name, entries=len(row.entries))
    return row


def update_lookup(db: Session, *, table: ErpLookupTable, actor_user_id: uuid.UUID, description: Optional[str] = None,
                  entries: Any = None, merge: bool = False) -> ErpLookupTable:
    if entries is not None:
        new = _entries(entries)
        table.entries = {**(table.entries or {}), **new} if merge else new
        if len(table.entries) > v.MAX_LOOKUP_ENTRIES:
            raise ErpError("INVALID", f"at most {v.MAX_LOOKUP_ENTRIES} entries")
    if description is not None:
        table.description = description[:300] or None
    table.revision += 1
    table.updated_at = now()
    db.flush()
    _audit(db, organization_id=table.organization_id, workspace_id=table.workspace_id, actor=actor_user_id,
           operation="lookup_updated", name=table.name, entries=len(table.entries or {}))
    return table


def delete_lookup(db: Session, *, table: ErpLookupTable, actor_user_id: uuid.UUID) -> None:
    used = [m for m in db.execute(select(ErpMapping).where(ErpMapping.workspace_id == table.workspace_id,
                                                           ErpMapping.status == v.MAPPING_ACTIVE)).scalars()
            if f'"table": "{table.name}"' in json.dumps(m.spec)]
    if used:
        raise ErpError("IN_USE", f"{len(used)} active mapping(s) look values up in {table.name!r}")
    _audit(db, organization_id=table.organization_id, workspace_id=table.workspace_id, actor=actor_user_id,
           operation="lookup_deleted", action=AuditAction.DELETED, name=table.name)
    db.delete(table)
    db.flush()


# ---------------------------------------------------------------------------
# the ledger: planning
# ---------------------------------------------------------------------------


def idempotency_key(workspace_id: uuid.UUID, target_id: uuid.UUID, object_kind: str, source_kind: str,
                    source_id: uuid.UUID) -> str:
    raw = f"erp-posting/v1|{workspace_id}|{target_id}|{object_kind}|{source_kind}|{source_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


def remote_id(key: str) -> str:
    return "FP-" + key[:32]


def _allocate_control(db: Session, target_id: uuid.UUID) -> x12.ControlNumbers:
    """The next interchange / group control number for this target, under the target's row lock."""
    n = db.execute(sa_update(ErpTarget).where(ErpTarget.id == target_id)
                   .values(control_sequence=text("CASE WHEN control_sequence >= 999999999 THEN 1 "
                                                 "ELSE control_sequence + 1 END"))
                   .returning(ErpTarget.control_sequence)).scalar_one()
    return x12.ControlNumbers(int(n), int(n))


def ledger_row(db: Session, target_id: uuid.UUID, object_kind: str, source_kind: str,
               source_id: uuid.UUID) -> Optional[ErpPosting]:
    return db.execute(select(ErpPosting).where(ErpPosting.target_id == target_id, ErpPosting.object_kind == object_kind,
                                               ErpPosting.source_kind == source_kind,
                                               ErpPosting.source_id == source_id)).scalar_one_or_none()


def bill_external_id(db: Session, target_id: uuid.UUID, source_kind: str, source_id: uuid.UUID) -> Optional[str]:
    row = ledger_row(db, target_id, v.OBJECT_VENDOR_BILL, source_kind, source_id)
    return row.external_id if row is not None and row.state == v.STATE_DONE else None


@dataclass
class _Rendered:
    obj: Optional[C.PostingObject]
    rendered: Optional[R.Rendered]
    mapping: Optional[ErpMapping]
    error: Optional[str] = None
    problems: list[str] = field(default_factory=list)
    code: Optional[str] = None


def _render_for(db: Session, target: ErpTarget, outcome: S.Outcome, object_kind: str, posting_id: uuid.UUID,
                key: str, *, control: Optional[x12.ControlNumbers]) -> _Rendered:
    today = local_today(db, target.workspace_id)
    mapping = active_mapping(db, target, object_kind)
    if mapping is None:
        return _Rendered(None, None, None, "no active mapping for this object kind", code="NO_MAPPING")
    try:
        obj = S.build(db, outcome, object_kind, today=today)
    except (C.BuildError, S.SourceError) as exc:
        return _Rendered(None, None, mapping, f"the {v.OBJECT_LABELS[object_kind].lower()} could not be built: {exc}",
                         code=getattr(exc, "code", "BUILD"))
    extra = {"id": str(posting_id), "idempotency_key": key, "date": today,
             "bill_external_id": bill_external_id(db, target.id, outcome.kind, outcome.id)}
    try:
        rendered = R.render(R.TargetView(target.format, target.preset, target.config), obj, mapping.spec,
                            lookups=lookups(db, target.workspace_id), extra=extra, posting_id=str(posting_id),
                            remote_id=remote_id(key), at=now(), control=control)
    except R.RenderError as exc:
        problems = list(exc.problems)
        if object_kind == v.OBJECT_PAYMENT_REFERENCE and extra["bill_external_id"] is None and \
                any("bill" in p.lower() or "linked" in p.lower() for p in problems):
            problems.insert(0, "the vendor bill must be posted (DONE) to this target before its payment")
        return _Rendered(obj, None, mapping, f"{exc}: " + "; ".join(problems[:3]), problems, code=exc.code)
    return _Rendered(obj, rendered, mapping)


def _attempt(db: Session, posting: ErpPosting, kind: str, outcome: str, *, message: Optional[str] = None,
             http_status: Optional[int] = None, detail: Optional[dict] = None, actor: Optional[uuid.UUID] = None) -> None:
    seq = db.execute(select(func.coalesce(func.max(ErpPostingAttempt.seq), 0)).where(
        ErpPostingAttempt.posting_id == posting.id)).scalar_one() + 1
    stamp = now()
    db.add(ErpPostingAttempt(id=uuid.uuid4(), posting_id=posting.id, workspace_id=posting.workspace_id, seq=seq,
                             kind=kind, outcome=outcome, http_status=http_status,
                             message=(message or "")[:2000] or None, detail=_jsonable(detail or {}),
                             actor_user_id=actor, started_at=stamp, finished_at=stamp))
    db.flush()


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda o: format(o, "f") if isinstance(o, Decimal) else str(o)))


def _enter(db: Session, posting: ErpPosting, target: ErpTarget, state: str, *, message: Optional[str] = None) -> None:
    """Move a posting to a state; entering an exception state counts it and tells Flow Builder once."""
    stamp = now()
    posting.state = state
    posting.updated_at = stamp
    if state != v.STATE_SENDING:
        posting.lease_token = posting.lease_until = None
    if state != v.STATE_RETRYING:
        posting.next_attempt_at = None
    if message is not None:
        posting.last_error = message[:4000] if state not in (v.STATE_DONE, v.STATE_DELIVERED) else posting.last_error
    if state == v.STATE_DONE:
        posting.acknowledged_at = stamp
    if state == v.STATE_CANCELLED:
        posting.cancelled_at = stamp
    if state in v.EXCEPTION_STATES:
        posting.exception_seq += 1
        db.flush()
        _emit_failed(db, posting, target, message or state)
        _audit(db, organization_id=posting.organization_id, workspace_id=posting.workspace_id, actor=None,
               operation="posting_exception", action=AuditAction.SYNC_FAILED, outcome=AuditOutcome.DENIED,
               posting_id=str(posting.id), state=state, target_id=str(target.id), message=(message or "")[:300])
    elif state == v.STATE_DONE:
        db.flush()
        _audit(db, organization_id=posting.organization_id, workspace_id=posting.workspace_id, actor=None,
               operation="posting_done", action=AuditAction.SYNC_COMPLETED, posting_id=str(posting.id),
               target_id=str(target.id), external_id=posting.external_id)
    db.flush()


def _emit_failed(db: Session, posting: ErpPosting, target: ErpTarget, message: str) -> None:
    from app.services import outbox_service

    outbox_service.emit_trigger(
        db, organization_id=posting.organization_id, workspace_id=posting.workspace_id,
        event_type=v.EVENT_POSTING_FAILED, resource_id=posting.id,
        idempotency_key=f"{v.EVENT_POSTING_FAILED}:{posting.id}:{posting.exception_seq}",
        payload={"posting_id": str(posting.id), "target": target.name, "target_id": str(target.id),
                 "object_kind": posting.object_kind, "state": posting.state,
                 "document_number": posting.document_number or "",
                 "amount": format(posting.amount, "f") if posting.amount is not None else "",
                 "currency": posting.currency or "", "reason": (message or "")[:500],
                 "attempts": int(posting.attempts), "work_item_id": str(posting.work_item_id) if posting.work_item_id else ""})


def _rendered_values(r: _Rendered) -> dict:
    """The ledger columns a render produces (inserted WITH the row: a PENDING posting always has its bytes)."""
    out: dict = {"mapping_id": r.mapping.id if r.mapping else None,
                 "mapping_version": r.mapping.version if r.mapping else None}
    if r.obj is not None:
        out.update(canonical=_jsonable(r.obj.as_json()), source_digest=r.obj.digest(),
                   document_number=(r.obj.document_number or "")[:100] or None, amount=r.obj.total,
                   currency=r.obj.currency or None)
    if r.rendered is not None:
        out.update(mapped=_jsonable(r.rendered.record.as_json()), rendered=r.rendered.data,
                   rendered_media_type=r.rendered.media_type, rendered_filename=r.rendered.filename,
                   content_sha=r.rendered.sha256, control_numbers=r.rendered.control or {})
    return out


def _fill(posting: ErpPosting, r: _Rendered) -> None:
    for key, value in _rendered_values(r).items():
        setattr(posting, key, value)


def plan(db: Session, *, target: ErpTarget, source_kind: str, source_id: uuid.UUID, object_kind: str, origin: str,
         actor_user_id: Optional[uuid.UUID]) -> tuple[ErpPosting, bool]:
    """(the posting, created?). Idempotent: the ledger key has exactly one posting, whoever asks."""
    if origin not in v.ORIGINS:
        raise ErpError("INVALID", f"unknown origin {origin!r}")
    existing = ledger_row(db, target.id, object_kind, source_kind, source_id)
    if existing is not None:
        return existing, False
    if target.status != v.TARGET_ACTIVE:
        raise ErpError("TARGET_DISABLED", f"the target {target.name!r} is disabled")
    if object_kind not in PR.supported_objects(target.format, target.preset):
        raise ErpError("UNSUPPORTED", f"{target.name} does not take a {v.OBJECT_LABELS[object_kind].lower()}")
    try:
        outcome = S.load(db, target.workspace_id, source_kind, source_id)
    except S.SourceError as exc:
        raise ErpError(exc.code, str(exc)) from exc
    if object_kind not in outcome.objects:
        raise ErpError("NOT_AVAILABLE", f"a {v.OBJECT_LABELS[object_kind].lower()} cannot be built from this "
                                        f"{v.SOURCE_LABELS[source_kind].lower()}")
    key = idempotency_key(target.workspace_id, target.id, object_kind, source_kind, source_id)
    posting_id = uuid.uuid4()
    control = _allocate_control(db, target.id) if target.format == v.FORMAT_X12 else None
    r = _render_for(db, target, outcome, object_kind, posting_id, key, control=control)
    ok = r.rendered is not None
    stamp = now()
    values = dict(id=posting_id, organization_id=target.organization_id, workspace_id=target.workspace_id,
                  target_id=target.id, object_kind=object_kind, source_kind=source_kind, source_id=source_id,
                  work_item_id=outcome.documents.get("invoice") if object_kind != v.OBJECT_PURCHASE_ORDER
                  else outcome.documents.get("po"), origin=origin,
                  state=v.STATE_PENDING if ok else v.STATE_FAILED, idempotency_key=key, engine_version=v.ENGINE_VERSION,
                  max_attempts=int(target.max_attempts), exception_seq=0 if ok else 1,
                  last_error=None if ok else r.error, created_by_user_id=actor_user_id, created_at=stamp,
                  updated_at=stamp, ack={}, control_numbers={})
    values.update(_rendered_values(r))
    if object_kind == v.OBJECT_GOODS_RECEIPT:
        values["work_item_id"] = outcome.documents.get("receipt") or values["work_item_id"]
    # ARCH47-S1:plan-race. No conflict target: the ledger key and the idempotency key (derived from it) are both
    # unique, and two planners racing collide on whichever the index check reaches first -- either means "theirs".
    inserted = db.execute(pg_insert(ErpPosting).values(**values).on_conflict_do_nothing()
                          .returning(ErpPosting.id)).scalar()
    if inserted is None:  # someone planned it between our read and our insert: theirs is THE posting
        row = ledger_row(db, target.id, object_kind, source_kind, source_id)
        assert row is not None
        return row, False
    posting = db.get(ErpPosting, posting_id)
    if ok:
        _attempt(db, posting, v.ATTEMPT_RENDER, v.OUTCOME_OK,
                 message=f"rendered {len(r.rendered.data)} bytes with mapping v{r.mapping.version}",
                 detail={"sha256": r.rendered.sha256, "notes": r.obj.notes if r.obj else []})
    else:
        _attempt(db, posting, v.ATTEMPT_RENDER, v.OUTCOME_PERMANENT, message=r.error,
                 detail={"problems": r.problems[:25], "code": r.code})
        _emit_failed(db, posting, target, r.error or "render failed")
    _audit(db, organization_id=target.organization_id, workspace_id=target.workspace_id, actor=actor_user_id,
           operation="posting_planned", action=AuditAction.CREATED, posting_id=str(posting.id),
           target_id=str(target.id), object_kind=object_kind, source_kind=source_kind, source_id=str(source_id),
           origin=origin, state=posting.state, content_sha=posting.content_sha)
    if ok:
        _after_render(db, posting, target)
    return posting, True


def _after_render(db: Session, posting: ErpPosting, target: ErpTarget) -> None:
    if target.transport == v.TRANSPORT_DOWNLOAD:
        posting.state = v.STATE_DELIVERED
        posting.delivered_at = now()
        _attempt(db, posting, v.ATTEMPT_SEND, v.OUTCOME_OK,
                 message="ready to download and import; confirm the import (or upload the ERP's response)")
        db.flush()
        return
    enqueue_delivery(db, posting)


def enqueue_delivery(db: Session, posting: ErpPosting, *, at: Optional[datetime] = None) -> None:
    from app.services import job_service

    job_service.enqueue(db, job_type=v.JOB_DELIVER, organization_id=posting.organization_id,
                        payload={"posting_id": str(posting.id)}, max_attempts=1, available_at=at,
                        idempotency_key=f"erp:deliver:{posting.id}:{posting.attempts + 1}:{posting.exception_seq}")


# ---------------------------------------------------------------------------
# the ledger: delivery
# ---------------------------------------------------------------------------


@dataclass
class Result:
    state: str
    message: str
    attempts: list[dict] = field(default_factory=list)   # {kind, outcome, message, http_status, detail}
    external_id: Optional[str] = None
    ack: dict = field(default_factory=dict)
    remote_path: Optional[str] = None
    retry_after: Optional[int] = None
    credential: Optional[dict] = None
    probe_first: bool = False


def _claim(db: Session, posting_id: uuid.UUID, token: uuid.UUID) -> Optional[tuple[ErpPosting, ErpTarget, bool]]:
    row = db.execute(select(ErpPosting).where(ErpPosting.id == posting_id)
                     .with_for_update(skip_locked=True)).scalar_one_or_none()
    if row is None:
        db.rollback()
        return None
    stamp = now()
    target = db.get(ErpTarget, row.target_id)
    if row.state in v.SENDABLE_STATES and (row.next_attempt_at is None or row.next_attempt_at <= stamp):
        probe_first = bool((row.ack or {}).get("probe_first"))
    elif row.state == v.STATE_SENDING and row.lease_until is not None and row.lease_until < stamp:
        probe_first = True   # the worker that held it disappeared mid-send: its outcome is unknown
        _attempt(db, row, v.ATTEMPT_SEND, v.OUTCOME_UNCERTAIN,
                 message="the previous attempt's lease expired before it recorded an outcome")
    else:
        db.rollback()
        return None
    if row.erased_at is not None:
        # ARCH-20 erased what the ledger held of it: nothing left to send. An interrupted send's outcome is
        # still unknown (a person decides); anything else will not be posted.
        if row.state == v.STATE_SENDING:
            _enter(db, row, target, v.STATE_UNCERTAIN, message="the posting's content was erased while a send's "
                                                                "outcome was unknown: check the target")
        else:
            row.cancelled_at = stamp
            _enter(db, row, target, v.STATE_CANCELLED, message="the posting's content was erased")
        db.commit()
        return None
    if target is None or target.status != v.TARGET_ACTIVE:
        _enter(db, row, target, v.STATE_FAILED, message="the target is disabled") if target else None
        db.commit()
        return None
    if row.attempts >= row.max_attempts:
        _enter(db, row, target, v.STATE_FAILED, message=f"no success after {row.attempts} attempt(s): "
                                                       f"{row.last_error or 'see the attempts'}")
        db.commit()
        return None
    row.state = v.STATE_SENDING
    row.lease_token = token
    row.lease_until = stamp + timedelta(seconds=v.SEND_LEASE_SECONDS)
    row.attempts += 1
    row.next_attempt_at = None
    row.updated_at = stamp
    db.commit()
    return row, target, probe_first


def deliver(db: Session, posting_id: uuid.UUID) -> dict:
    """The erp.deliver_posting job. Commits its claim before any network I/O, and its outcome after."""
    token = uuid.uuid4()
    claimed = _claim(db, posting_id, token)
    if claimed is None:
        return {"delivered": False, "reason": "not due, already claimed, or finished"}
    posting, target, probe_first = claimed
    snapshot = _Snapshot.of(posting, target)
    try:
        credential = credential_of(target) if target.auth_mode != v.AUTH_NONE else None
        if target.auth_mode != v.AUTH_NONE and credential is None:
            result = Result(v.STATE_FAILED, "the target has no credential")
        elif target.transport == v.TRANSPORT_HTTP:
            result = _talk_http(snapshot, credential, probe_first)
        elif target.transport == v.TRANSPORT_SFTP:
            result = _talk_sftp(snapshot, credential, probe_first)
        else:
            result = Result(v.STATE_DELIVERED, "ready to download")
    except ErpError as exc:
        result = Result(v.STATE_FAILED, str(exc))
    except Exception as exc:  # noqa: BLE001 - an unexpected error while talking: the send may have happened
        logger.exception("erp.deliver_unexpected", extra={"posting_id": str(posting_id)})
        result = Result(v.STATE_UNCERTAIN, f"unexpected error during delivery: {type(exc).__name__}: {exc}"[:500])
    return _record(db, posting_id, token, result)


@dataclass(frozen=True)
class _Snapshot:
    """What talking to the target needs, detached from the session (no ORM access during network I/O)."""

    posting_id: uuid.UUID
    object_kind: str
    rendered: bytes
    filename: str
    sha256: str
    idempotency_key: str
    control_numbers: dict
    preset: str
    format: str
    ack_mode: str
    auth_mode: str
    config: dict
    remote_path: Optional[str]
    currency: Optional[str] = None

    @classmethod
    def of(cls, p: ErpPosting, t: ErpTarget) -> "_Snapshot":
        return cls(p.id, p.object_kind, bytes(p.rendered or b""), p.rendered_filename or "", p.content_sha or "",
                   p.idempotency_key, dict(p.control_numbers or {}), t.preset, t.format, t.ack_mode, t.auth_mode,
                   dict(t.config or {}), p.remote_path, p.currency)


def _talk_http(s: _Snapshot, credential: Optional[dict], probe_first: bool) -> Result:
    from app.services.erp.transport import http

    preset = jsonapi.preset(s.preset)
    ep = jsonapi.endpoint_for(s.preset, s.object_kind, s.config)
    body = jsonapi.loads(s.rendered)
    doc = jsonapi.doc_number_of(s.preset, s.object_kind, body, s.config)
    sess = http.Session(auth_mode=s.auth_mode, credential=credential, config=s.config)
    attempts: list[dict] = []

    def finish(result: Result) -> Result:
        result.attempts = attempts + result.attempts
        result.credential = sess.credential if sess.credential_changed else None
        return result

    def probe() -> tuple[str, Optional[str], str]:
        path = jsonapi.probe_request(s.preset, s.object_kind, doc, s.config)
        if path is None:
            return "NONE", None, f"{preset.label} offers no way to look this {s.object_kind.lower()} up"
        r = sess.request("GET", path, headers={"Accept": "application/json"})
        if r.kind != v.OUTCOME_OK:
            attempts.append({"kind": v.ATTEMPT_PROBE, "outcome": v.OUTCOME_TRANSIENT, "http_status": r.status,
                             "message": r.message, "detail": r.recorded()})
            return "ERROR", None, r.message
        found, ext = jsonapi.probe_result(s.preset, s.object_kind, r.json(), s.config)
        attempts.append({"kind": v.ATTEMPT_PROBE, "outcome": v.OUTCOME_FOUND if found else v.OUTCOME_ABSENT,
                         "http_status": r.status, "message": f"looked up {doc!r}: {'found ' + str(ext) if found else 'absent'}",
                         "detail": r.recorded()})
        return ("FOUND" if found else "ABSENT"), ext, ""

    if probe_first:
        state, ext, why = probe()
        if state == "FOUND":
            return finish(Result(v.STATE_DONE, f"the target already has it ({ext}): found by probe", external_id=ext,
                                 ack={"via": "probe", "external_id": ext}))
        if state == "NONE":
            return finish(Result(v.STATE_UNCERTAIN, f"the previous send's outcome is unknown and {why}; check the "
                                                    f"target, then retry (it is not there) or accept (it is)"))
        if state == "ERROR":
            return finish(Result(v.STATE_RETRYING, f"could not look the object up before re-sending: {why}",
                                 ack={"probe_first": True}))
    headers = {**preset.headers, "Content-Type": "application/json"}
    path = jsonapi.fill(ep.path, s.config, s.preset)
    if preset.idempotency_query:
        path += ("&" if "?" in path else "?") + f"{preset.idempotency_query}={s.idempotency_key[:50]}"
    if preset.idempotency_header:
        headers[preset.idempotency_header] = str(uuid.UUID(s.idempotency_key[:32]))
    if s.preset == v.PRESET_GENERIC_REST or s.preset == v.PRESET_GENERIC_ODATA:
        header = (s.config.get("rest") or {}).get("idempotency_header")
        if header and re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(header)):
            headers[str(header)] = s.idempotency_key
    if preset.csrf:
        failed = sess.fetch_csrf(path)
        if failed is not None:
            attempts.append({"kind": v.ATTEMPT_SEND, "outcome": failed.kind, "http_status": failed.status,
                             "message": f"CSRF token: {failed.message}", "detail": failed.recorded()})
            state = v.STATE_RETRYING if failed.kind == v.OUTCOME_TRANSIENT else v.STATE_FAILED
            return finish(Result(state, f"could not get a CSRF token: {failed.message}"))
    r = sess.request(ep.method, path, body=s.rendered, headers=headers, write=True)
    attempts.append({"kind": v.ATTEMPT_SEND, "outcome": r.kind, "http_status": r.status,
                     "message": r.message or f"{ep.method} {path.split('?')[0]}", "detail": {**r.recorded(),
                                                                                             "calls": sess.log[-4:]}})
    if r.kind == v.OUTCOME_OK:
        response = r.json()
        location = r.headers.get("location")
        if ep.follow_location and location:
            g = sess.request("GET", location, headers={"Accept": "application/json"})
            attempts.append({"kind": v.ATTEMPT_ACK, "outcome": g.kind, "http_status": g.status,
                             "message": "read the created record back", "detail": g.recorded()})
            if g.kind == v.OUTCOME_OK:
                response = g.json()
        ack = jsonapi.acknowledge(s.preset, s.object_kind, body, response, location=location, config=s.config,
                                  currency=s.currency)
        attempts.append({"kind": v.ATTEMPT_ACK, "outcome": ack.outcome, "message": ack.message,
                         "detail": {"echoed": ack.echoed}})
        state = v.STATE_DONE if ack.outcome == v.OUTCOME_ACCEPTED else v.STATE_MISMATCH
        return finish(Result(state, ack.message, external_id=ack.external_id,
                             ack={"via": "response", "echoed": ack.echoed, "external_id": ack.external_id}))
    if r.kind == v.OUTCOME_PERMANENT and r.status is not None and preset.duplicate(r.status, r.json()):
        state, ext, why = probe()
        if state == "FOUND":
            return finish(Result(v.STATE_DONE, f"the target refused a duplicate: it already has it ({ext})",
                                 external_id=ext, ack={"via": "duplicate+probe", "external_id": ext}))
        return finish(Result(v.STATE_REJECTED, "the target reports a duplicate document number that is not this "
                                               f"posting: {preset.error_text(r.json()) or r.message}"))
    if r.kind == v.OUTCOME_UNCERTAIN:
        state, ext, why = probe()
        if state == "FOUND":
            return finish(Result(v.STATE_DONE, f"the send's answer was lost, but the target has it ({ext})",
                                 external_id=ext, ack={"via": "probe", "external_id": ext}))
        if state == "ABSENT":
            return finish(Result(v.STATE_RETRYING, f"{r.message}; the target does not have it: will send again"))
        if state == "ERROR":
            return finish(Result(v.STATE_RETRYING, f"{r.message}; the look-up failed too: will look again before "
                                                   "re-sending", ack={"probe_first": True}))
        return finish(Result(v.STATE_UNCERTAIN, f"{r.message}; {why}: check the target, then retry or accept"))
    if r.kind == v.OUTCOME_TRANSIENT:
        return finish(Result(v.STATE_RETRYING, r.message, retry_after=r.retry_after))
    text_ = preset.error_text(r.json()) if r.status else ""
    state = v.STATE_REJECTED if r.status is not None else v.STATE_FAILED
    return finish(Result(state, f"{r.message}: {text_}" if text_ else r.message))


def _talk_sftp(s: _Snapshot, credential: Optional[dict], probe_first: bool) -> Result:
    import posixpath

    from app.services.erp.transport import sftp

    attempts: list[dict] = []
    try:
        with sftp.Connection(config=s.config, auth_mode=s.auth_mode, credential=credential) as c:
            final = posixpath.join(c.cfg["directory"], s.filename)
            if probe_first:
                state = c.probe(final, s.sha256)
                temp_exists = c._stat(posixpath.join(c.cfg["directory"], f".{s.filename}.part")) is not None
                attempts.append({"kind": v.ATTEMPT_PROBE, "outcome": state if state != v.OUTCOME_MISMATCH else
                                 v.OUTCOME_MISMATCH, "message": f"{final}: {state.lower()}"
                                 + (" (an unfinished upload is there)" if temp_exists else "")})
                if state == v.OUTCOME_FOUND:
                    return _delivered(s, final, attempts, "already on the server (found by probe)")
                if state == v.OUTCOME_MISMATCH:
                    return Result(v.STATE_FAILED, f"a different file already has the name {final}", attempts)
                if not temp_exists:
                    acked = _ack_for(c, s)
                    if acked is not None:
                        return acked
                    return Result(v.STATE_UNCERTAIN, "the previous attempt's outcome is unknown: the file is not on "
                                                     "the server and no unfinished upload is either (an importer may "
                                                     "have taken it). Check the ERP, then retry or accept.", attempts,
                                  remote_path=final)
            d = c.upload(s.filename, s.rendered, s.sha256)
            attempts.append({"kind": v.ATTEMPT_SEND, "outcome": d.kind, "message": d.message,
                             "detail": {"remote_path": d.remote_path, "size": d.size}})
            if d.kind in (v.OUTCOME_OK, v.OUTCOME_FOUND):
                return _delivered(s, d.remote_path or final, attempts, d.message)
            if d.kind == v.OUTCOME_UNCERTAIN:
                state = c.probe(final, s.sha256)
                attempts.append({"kind": v.ATTEMPT_PROBE, "outcome": state, "message": f"{final}: {state.lower()}"})
                if state == v.OUTCOME_FOUND:
                    return _delivered(s, final, attempts, "delivered (confirmed by probe after an uncertain rename)")
                return Result(v.STATE_UNCERTAIN, f"{d.message}; the file is not visible now (an importer may have "
                                                 "taken it): check the ERP, then retry or accept", attempts,
                              remote_path=final)
            if d.kind == v.OUTCOME_TRANSIENT:
                return Result(v.STATE_RETRYING, d.message, attempts)
            return Result(v.STATE_FAILED, d.message, attempts)
    except sftp.SftpError as exc:
        attempts.append({"kind": v.ATTEMPT_SEND, "outcome": exc.kind, "message": str(exc)})
        return Result(v.STATE_RETRYING if exc.kind == v.OUTCOME_TRANSIENT else v.STATE_FAILED, str(exc), attempts)


def _delivered(s: _Snapshot, path: str, attempts: list[dict], message: str) -> Result:
    if s.ack_mode == v.ACK_DELIVERY:
        return Result(v.STATE_DONE, f"{message} (delivery-level acknowledgement: the file is on the server with our "
                                    "size)", attempts, ack={"via": "delivery", "remote_path": path}, remote_path=path)
    return Result(v.STATE_DELIVERED, message, attempts, remote_path=path)


def _ack_for(c: Any, s: _Snapshot) -> Optional[Result]:
    """An acknowledgement already on the server for this posting (used before any re-send)."""
    from app.services.erp.transport import sftp

    if s.ack_mode == v.ACK_X12_997:
        for name, content in c.ack_files():
            try:
                ack = x12.parse_997(content)
            except x12.X12Error:
                continue
            outcome, message = x12.correlate(ack, s.control_numbers, s.object_kind)
            if outcome != v.OUTCOME_PENDING:
                return Result(_ACK_STATE[outcome], f"{name}: {message}", ack={"via": "997", "file": name,
                                                                               "ack": ack.as_json()})
    elif s.ack_mode == v.ACK_FILE:
        for name, content in c.ack_files(name=s.filename):
            outcome, message, ext = sftp.parse_ack_file(content)
            return Result(_ACK_STATE[outcome], f"{name}: {message}", external_id=ext, ack={"via": "ack_file",
                                                                                            "file": name})
    return None


_ACK_STATE = {v.OUTCOME_ACCEPTED: v.STATE_DONE, v.OUTCOME_REJECTED: v.STATE_REJECTED,
              v.OUTCOME_MISMATCH: v.STATE_MISMATCH}


def backoff_seconds(attempt: int, retry_after: Optional[int] = None, *, rng: Optional[random.Random] = None) -> int:
    """min(CEILING, BASE x 2^(attempt-1)), then full jitter in [half, whole]; never below Retry-After."""
    delay = min(v.RETRY_CEILING_SECONDS, v.RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1)))
    jittered = int((rng or random).uniform(delay / 2, delay))
    return max(jittered, int(retry_after or 0), 1)


def _record(db: Session, posting_id: uuid.UUID, token: uuid.UUID, result: Result) -> dict:
    row = db.execute(select(ErpPosting).where(ErpPosting.id == posting_id).with_for_update()).scalar_one_or_none()
    if row is None or row.lease_token != token or row.state != v.STATE_SENDING:
        db.rollback()
        logger.warning("erp.lost_lease", extra={"posting_id": str(posting_id)})
        return {"delivered": False, "reason": "the lease was lost; another worker owns this posting"}
    target = db.get(ErpTarget, row.target_id)
    for a in result.attempts:
        _attempt(db, row, a["kind"], a["outcome"], message=a.get("message"), http_status=a.get("http_status"),
                 detail=a.get("detail"))
    if result.credential is not None and target is not None:
        target.credential_ciphertext, target.credential_fingerprint = _encrypt(result.credential)
        target.credential_updated_at = now()
    if result.external_id:
        row.external_id = str(result.external_id)[:200]
    if result.remote_path:
        row.remote_path = result.remote_path[:500]
    ack = {k: val for k, val in (row.ack or {}).items() if k != "probe_first"}
    ack.update(result.ack)
    row.ack = _jsonable(ack)
    state = result.state
    if state == v.STATE_RETRYING and row.attempts >= row.max_attempts:
        state = v.STATE_FAILED
        result.message = f"no success after {row.attempts} attempt(s): {result.message}"
    if state in (v.STATE_DELIVERED, v.STATE_DONE) and row.delivered_at is None:
        row.delivered_at = now()
    if state == v.STATE_RETRYING:
        delay = backoff_seconds(row.attempts, result.retry_after)
        row.state = v.STATE_RETRYING
        row.lease_token = row.lease_until = None
        row.next_attempt_at = now() + timedelta(seconds=delay)
        row.last_error = result.message[:4000]
        row.updated_at = now()
        db.flush()
        enqueue_delivery(db, row, at=row.next_attempt_at)
    else:
        _enter(db, row, target, state, message=result.message)
    db.commit()
    return {"delivered": state in (v.STATE_DONE, v.STATE_DELIVERED), "state": state, "message": result.message,
            "posting_id": str(posting_id)}


# ---------------------------------------------------------------------------
# acknowledgements after delivery
# ---------------------------------------------------------------------------


def poll_acks(db: Session, target: ErpTarget, *, limit: int = 200) -> dict:
    """SFTP targets: read 997s / ack files for postings awaiting acknowledgement; time out the silent ones."""
    waiting = list(db.execute(select(ErpPosting).where(ErpPosting.target_id == target.id,
                                                       ErpPosting.state == v.STATE_DELIVERED)
                              .order_by(ErpPosting.delivered_at).limit(limit)).scalars())
    summary = {"waiting": len(waiting), "done": 0, "rejected": 0, "mismatch": 0, "timed_out": 0}
    if not waiting:
        return summary
    stamp = now()
    results: dict[uuid.UUID, Result] = {}
    if target.transport == v.TRANSPORT_SFTP and target.ack_mode in (v.ACK_X12_997, v.ACK_FILE):
        from app.services.erp.transport import sftp

        snapshots = [_Snapshot.of(p, target) for p in waiting]
        try:
            with sftp.Connection(config=target.config, auth_mode=target.auth_mode, credential=credential_of(target)) as c:
                for snap in snapshots:
                    found = _ack_for(c, snap)
                    if found is not None:
                        results[snap.posting_id] = found
        except (sftp.SftpError, ErpError) as exc:
            logger.warning("erp.ack_poll_failed", extra={"target_id": str(target.id), "error": str(exc)[:300]})
    for p in waiting:
        r = results.get(p.id)
        if r is not None:
            _attempt(db, p, v.ATTEMPT_ACK, {v.STATE_DONE: v.OUTCOME_ACCEPTED, v.STATE_REJECTED: v.OUTCOME_REJECTED}
                     .get(r.state, v.OUTCOME_MISMATCH), message=r.message, detail=r.ack)
            if r.external_id:
                p.external_id = r.external_id[:200]
            p.ack = _jsonable({**(p.ack or {}), **r.ack})
            _enter(db, p, target, r.state, message=r.message)
            summary[{v.STATE_DONE: "done", v.STATE_REJECTED: "rejected"}.get(r.state, "mismatch")] += 1
        elif p.delivered_at is not None and p.delivered_at + timedelta(hours=int(target.ack_timeout_hours)) <= stamp:
            _attempt(db, p, v.ATTEMPT_ACK, v.OUTCOME_PENDING, message="no acknowledgement in time")
            _enter(db, p, target, v.STATE_FAILED, message=f"no acknowledgement within {target.ack_timeout_hours} "
                                                          "hours of delivery")
            summary["timed_out"] += 1
    db.flush()
    return summary


def acknowledge(db: Session, *, posting: ErpPosting, actor_user_id: uuid.UUID, accepted: Optional[bool] = None,
                reference: Optional[str] = None, reason: Optional[str] = None,
                response_file: Optional[bytes] = None) -> ErpPosting:
    """A person confirms what the target did with a delivered posting (DOWNLOAD targets, or any awaiting one):
    their verdict, or the ERP's own response file (a 997, Tally's import RESPONSE, an .ack)."""
    from app.services.erp.transport import sftp

    target = db.get(ErpTarget, posting.target_id)
    if posting.state != v.STATE_DELIVERED:
        raise ErpError("NOT_DELIVERED", f"the posting is {posting.state}; only one awaiting acknowledgement is confirmed")
    ext = (reference or "").strip()[:200] or None
    if response_file:
        if target.format == v.FORMAT_X12 and response_file.lstrip().startswith(b"ISA"):
            try:
                outcome, message = x12.correlate(x12.parse_997(response_file), posting.control_numbers or {},
                                                 posting.object_kind)
            except x12.X12Error as exc:
                raise ErpError("INVALID", f"the file is not a 997 for this interchange: {exc}") from exc
            if outcome == v.OUTCOME_PENDING:
                raise ErpError("NOT_OURS", "that 997 acknowledges another interchange")
        else:
            outcome, message, found_id = sftp.parse_ack_file(response_file)
            ext = ext or found_id
        state = _ACK_STATE[outcome]
    else:
        if accepted is None:
            raise ErpError("INVALID", "say whether the target accepted it, or upload its response file")
        state = v.STATE_DONE if accepted else v.STATE_REJECTED
        message = ("confirmed imported by a person" if accepted else f"refused by the target: {reason or 'no reason given'}")
    if ext:
        posting.external_id = ext
    posting.ack = _jsonable({**(posting.ack or {}), "via": "person" if not response_file else "response_file",
                             "by": str(actor_user_id)})
    _attempt(db, posting, v.ATTEMPT_ACK, {v.STATE_DONE: v.OUTCOME_ACCEPTED, v.STATE_REJECTED: v.OUTCOME_REJECTED}
             .get(state, v.OUTCOME_MISMATCH), message=message, actor=actor_user_id)
    _enter(db, posting, target, state, message=message)
    _audit(db, organization_id=posting.organization_id, workspace_id=posting.workspace_id, actor=actor_user_id,
           operation="posting_acknowledged", action=AuditAction.ACCEPTED if state == v.STATE_DONE else AuditAction.DECLINED,
           posting_id=str(posting.id), state=state)
    return posting


# ---------------------------------------------------------------------------
# a person acts: retry, accept, cancel (the review hub's POSTING verdicts)
# ---------------------------------------------------------------------------


def review(db: Session, *, posting: ErpPosting, verdict: str, actor_user_id: uuid.UUID, note: Optional[str] = None,
           reference: Optional[str] = None) -> ErpPosting:
    verdict = (verdict or "").strip().upper()
    if verdict not in v.VERDICTS:
        raise ErpError("INVALID", f"verdict is one of {', '.join(v.VERDICTS)}")
    target = db.get(ErpTarget, posting.target_id)
    state = posting.state
    if verdict == v.VERDICT_CANCEL:
        if state in v.FINAL_STATES or state == v.STATE_SENDING:
            raise ErpError("NOT_CANCELLABLE", f"a {state} posting cannot be cancelled")
    elif state not in v.EXCEPTION_STATES and not (verdict == v.VERDICT_RETRY and state == v.STATE_RETRYING):
        raise ErpError("NOT_IN_EXCEPTION", f"the posting is {state}; retry and accept apply to a failed, rejected, "
                                           "mismatched or uncertain posting")
    posting.reviewed_at = now()
    posting.reviewed_by_user_id = actor_user_id
    posting.review_note = (note or "").strip()[:500] or None
    _attempt(db, posting, v.ATTEMPT_REVIEW, v.OUTCOME_OK, message=f"{verdict} by a person" + (f": {note}" if note else ""),
             actor=actor_user_id)
    if verdict == v.VERDICT_CANCEL:
        _enter(db, posting, target, v.STATE_CANCELLED, message=note or "cancelled by a person")
    elif verdict == v.VERDICT_ACCEPT:
        if reference:
            posting.external_id = reference.strip()[:200]
        posting.ack = _jsonable({**(posting.ack or {}), "via": "person", "accepted_state": state})
        if posting.delivered_at is None:
            posting.delivered_at = now()
        _enter(db, posting, target, v.STATE_DONE, message=f"accepted by a person (was {state})")
    else:
        if posting.rendered is None and posting.erased_at is None:
            _rerender(db, posting, target)
            if posting.rendered is None:
                db.flush()
                return posting
        elif posting.rendered is None:
            raise ErpError("ERASED", "the posting's content was erased; it cannot be sent again")
        ack = dict(posting.ack or {})
        ack.pop("probe_first", None)   # a person looked: an UNCERTAIN posting is not at the target
        posting.ack = ack
        if posting.attempts >= posting.max_attempts:
            posting.max_attempts = min(100, posting.attempts + int(target.max_attempts))
        posting.state = v.STATE_PENDING
        posting.next_attempt_at = None
        posting.lease_token = posting.lease_until = None
        posting.updated_at = now()
        db.flush()
        _after_render(db, posting, target)
    _audit(db, organization_id=posting.organization_id, workspace_id=posting.workspace_id, actor=actor_user_id,
           operation="posting_reviewed", posting_id=str(posting.id), verdict=verdict, from_state=state,
           to_state=posting.state)
    db.flush()
    return posting


def _rerender(db: Session, posting: ErpPosting, target: ErpTarget) -> None:
    """A posting that was never sent (it failed to render) is rendered again with today's mapping and lookups."""
    try:
        outcome = S.load(db, posting.workspace_id, posting.source_kind, posting.source_id)
    except S.SourceError as exc:
        posting.last_error = f"the source is no longer postable: {exc}"
        _attempt(db, posting, v.ATTEMPT_RENDER, v.OUTCOME_PERMANENT, message=posting.last_error)
        _enter(db, posting, target, v.STATE_FAILED, message=posting.last_error)
        return
    control = _allocate_control(db, target.id) if target.format == v.FORMAT_X12 else None
    r = _render_for(db, target, outcome, posting.object_kind, posting.id, posting.idempotency_key, control=control)
    _fill(posting, r)
    if r.rendered is None:
        _attempt(db, posting, v.ATTEMPT_RENDER, v.OUTCOME_PERMANENT, message=r.error,
                 detail={"problems": r.problems[:25], "code": r.code})
        _enter(db, posting, target, v.STATE_FAILED, message=r.error)
        return
    _attempt(db, posting, v.ATTEMPT_RENDER, v.OUTCOME_OK, message=f"rendered again with mapping v{r.mapping.version}",
             detail={"sha256": r.rendered.sha256})


# ---------------------------------------------------------------------------
# the sweep (scripts/sweep_erp_postings.py, every 10 minutes)
# ---------------------------------------------------------------------------


@dataclass
class SweepSummary:
    at: str
    workspaces: int = 0
    planned: int = 0
    already: int = 0
    refused: int = 0
    enqueued: int = 0
    reclaimed: int = 0
    acks: dict = field(default_factory=dict)
    skipped_without_plan: int = 0

    def as_json(self) -> dict:
        return dict(self.__dict__)


def sweep(db: Session, *, apply: bool = True) -> SweepSummary:
    moment = now()
    summary = SweepSummary(at=moment.isoformat())
    targets = list(db.execute(select(ErpTarget).where(ErpTarget.status == v.TARGET_ACTIVE)
                              .order_by(ErpTarget.workspace_id, ErpTarget.created_at)).scalars())
    held: dict[uuid.UUID, bool] = {}
    seen_ws: set[uuid.UUID] = set()
    for target in targets:
        if target.organization_id not in held:
            held[target.organization_id] = gate.capability_held(db, target.organization_id)
        if not held[target.organization_id]:
            summary.skipped_without_plan += 1
            continue
        seen_ws.add(target.workspace_id)
        if target.auto_post and target.auto_post_since is not None:
            for outcome in S.eligible(db, target.workspace_id, since=target.auto_post_since,
                                      kinds=tuple(target.auto_sources or v.SOURCE_KINDS)):
                for kind in outcome.objects:
                    if (target.auto_objects and kind not in target.auto_objects) or \
                            kind not in PR.supported_objects(target.format, target.preset):
                        continue
                    if ledger_row(db, target.id, kind, outcome.kind, outcome.id) is not None:
                        summary.already += 1
                        continue
                    if not apply:
                        summary.planned += 1
                        continue
                    try:
                        with db.begin_nested():
                            _, created = plan(db, target=target, source_kind=outcome.kind, source_id=outcome.id,
                                              object_kind=kind, origin=v.ORIGIN_AUTO, actor_user_id=None)
                        summary.planned += int(created)
                        summary.already += int(not created)
                    except ErpError:
                        summary.refused += 1
        due = list(db.execute(select(ErpPosting).where(ErpPosting.target_id == target.id, or_(
            and_(ErpPosting.state == v.STATE_RETRYING, ErpPosting.next_attempt_at <= moment),
            and_(ErpPosting.state == v.STATE_PENDING, ErpPosting.created_at <= moment - timedelta(minutes=5)),
            and_(ErpPosting.state == v.STATE_SENDING, ErpPosting.lease_until < moment)))).scalars())
        for p in due:
            if apply:
                if p.state == v.STATE_SENDING:
                    summary.reclaimed += 1
                enqueue_delivery(db, p)
            summary.enqueued += 1
        if apply and (target.transport == v.TRANSPORT_SFTP or target.transport == v.TRANSPORT_DOWNLOAD):
            result = poll_acks(db, target)
            if result.get("waiting"):
                summary.acks[str(target.id)] = result
    summary.workspaces = len(seen_ws)
    return summary


# ---------------------------------------------------------------------------
# ARCH-20 erasure
# ---------------------------------------------------------------------------


def erase_for_work_items(db: Session, work_item_ids: Sequence[uuid.UUID]) -> int:
    """A posting quotes its documents (figures, names, lines): erasing a document erases what the ledger holds of
    it. The ledger row stays (a posting that happened stays a fact: its target, state, external id and time);
    one not yet delivered is cancelled."""
    ids = list(work_item_ids)
    if not ids:
        return 0
    rows = list(db.execute(select(ErpPosting).where(ErpPosting.work_item_id.in_(ids))).scalars())
    stamp = now()
    for p in rows:
        if p.state in (v.STATE_PENDING, v.STATE_RETRYING) or p.state in v.EXCEPTION_STATES:
            p.state, p.cancelled_at = v.STATE_CANCELLED, stamp
            p.next_attempt_at = p.lease_token = p.lease_until = None
        p.rendered = p.canonical = p.mapped = None
        p.document_number = None
        p.last_error = None
        p.erased_at = stamp
        p.updated_at = stamp
    if rows:
        db.execute(sa_update(ErpPostingAttempt).where(ErpPostingAttempt.posting_id.in_([p.id for p in rows]))
                   .values(detail={}, message=None))
    db.flush()
    return len(rows)


def test_target(db: Session, *, target: ErpTarget, actor_user_id: uuid.UUID) -> dict:
    """Can FlowPilot reach and authenticate to the target? Nothing is posted: HTTP reads the probe path (a
    document number that cannot exist) or the base URL; SFTP connects, checks the pinned host key, logs in and
    lists the upload directory."""
    result: dict = {"ok": False, "kind": v.OUTCOME_PERMANENT, "message": "", "detail": {}}
    try:
        credential = credential_of(target)
        if target.transport == v.TRANSPORT_DOWNLOAD:
            result.update(ok=True, kind=v.OUTCOME_OK, message="download targets have nothing to connect to")
        elif credential is None:
            result["message"] = "set the target's credential first"
        elif target.transport == v.TRANSPORT_HTTP:
            from app.services.erp.transport import http

            sess = http.Session(auth_mode=target.auth_mode, credential=credential, config=target.config)
            kinds = PR.supported_objects(target.format, target.preset)
            path = next((p for p in (jsonapi.probe_request(target.preset, k, "FLOWPILOT-CONNECTIVITY-CHECK",
                                                           target.config) for k in kinds) if p), None) or "/"
            r = sess.request("GET", path, headers={"Accept": "application/json"})
            reachable = r.status is not None
            result.update(ok=r.kind == v.OUTCOME_OK, kind=r.kind,
                          message=(f"reached the target ({r.status})" if reachable else r.message),
                          detail={"status": r.status, "path": path.split("?")[0]})
            if sess.credential_changed:
                target.credential_ciphertext, target.credential_fingerprint = _encrypt(sess.credential)
                target.credential_updated_at = now()
        else:
            from app.services.erp.transport import sftp

            with sftp.Connection(config=target.config, auth_mode=target.auth_mode, credential=credential) as c:
                names = c.sftp.listdir(c.cfg["directory"])
            result.update(ok=True, kind=v.OUTCOME_OK, message=f"connected, host key verified, {len(names)} "
                                                              f"entr{'y' if len(names) == 1 else 'ies'} in the "
                                                              "upload directory")
    except ErpError as exc:
        result["message"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - a connectivity test reports, never raises
        result.update(kind=getattr(exc, "kind", v.OUTCOME_TRANSIENT), message=str(exc)[:500])
    _audit(db, organization_id=target.organization_id, workspace_id=target.workspace_id, actor=actor_user_id,
           operation="target_tested", action=AuditAction.DESTINATION_TESTED, target_id=str(target.id),
           ok=result["ok"], kind=result["kind"])
    return result


def outcomes_with_status(db: Session, workspace_id: uuid.UUID, *, limit: int = 100) -> list[dict]:
    """Approved outcomes and, per active target and object, the posting state (for the console's "Ready" list)."""
    targets = list(db.execute(select(ErpTarget).where(ErpTarget.workspace_id == workspace_id,
                                                      ErpTarget.status == v.TARGET_ACTIVE)).scalars())
    out = []
    for outcome in S.eligible(db, workspace_id, limit=limit):
        states = {}
        for t in targets:
            for kind in outcome.objects:
                if kind in PR.supported_objects(t.format, t.preset):
                    row = ledger_row(db, t.id, kind, outcome.kind, outcome.id)
                    states[f"{t.id}:{kind}"] = {"target_id": str(t.id), "object_kind": kind,
                                                "posting_id": str(row.id) if row else None,
                                                "state": row.state if row else None}
        out.append({**outcome.as_json(), "postings": list(states.values())})
    return out


__all__ = ["ErpError", "Result", "SweepSummary", "acknowledge", "active_mapping", "backoff_seconds",
           "bill_external_id", "create_lookup", "create_target", "credential_of", "delete_lookup", "delete_target",
           "deliver", "enqueue_delivery", "erase_for_work_items", "idempotency_key", "ledger_row", "local_today",
           "lookup_names", "lookups", "now", "outcomes_with_status", "plan", "poll_acks", "remote_id",
           "restore_default_mapping", "review", "save_mapping", "set_credential", "sweep", "update_lookup",
           "update_target", "validate_mapping"]
