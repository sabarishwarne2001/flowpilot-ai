"""ARCH-50 — the sovereign edition's licence: Ed25519-signed, verified offline.

ARCH50-S1:licence

A LICENCE IS A JSON DOCUMENT
============================

    {"format": "flowpilot-licence/1",
     "key_id": "ed25519:<16 hex>",
     "payload": {"licence_id": "...", "licensee": "...", "edition": "sovereign",
                 "issued_at": "2026-09-29T00:00:00Z", "not_before": "...", "expires_at": "...",
                 "grace_days": 14, "max_organizations": 5, "max_seats": 500,
                 "features": ["egress_lockdown", "local_llm"], "deployment_id": "(optional)"},
     "signature": "<base64 Ed25519 over the canonical JSON of payload>"}

`scripts/licence_tool.py issue` writes one; the console's Sovereign page (or `LICENCE_FILE`) installs it. Every
read RE-VERIFIES the signature against a key pinned in `app/core/licence_keys.py`; nothing stored is trusted as
"valid". Nothing here touches the network.

WHAT IT GOVERNS
===============
Only the sovereign edition (`FLOWPILOT_EDITION=sovereign`). The hosted service (`saas`) needs none: status
NOT_REQUIRED. On a sovereign deployment, creating an organization needs a VALID licence (or one inside its grace
period) with room under `max_organizations`; that refusal is 402 `LICENCE_REQUIRED`. Existing organizations keep
working when a licence lapses -- a licence must never be the thing that loses a customer's data -- and the
console shows the state to the operator. A licence signed by a development key is refused when
ENVIRONMENT=production.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from app.core import licence_keys, signing
from app.core.exceptions import FlowPilotError

logger = logging.getLogger("app.services.sovereign.licence")

FORMAT = "flowpilot-licence/1"
EDITION_SAAS = "saas"
EDITION_SOVEREIGN = "sovereign"
EDITIONS: tuple[str, ...] = (EDITION_SAAS, EDITION_SOVEREIGN)

STATUS_NOT_REQUIRED = "NOT_REQUIRED"
STATUS_VALID = "VALID"
STATUS_GRACE = "GRACE"
STATUS_EXPIRED = "EXPIRED"
STATUS_NOT_YET_VALID = "NOT_YET_VALID"
STATUS_MISSING = "MISSING"
STATUS_MALFORMED = "MALFORMED"
STATUS_UNTRUSTED_KEY = "UNTRUSTED_KEY"
STATUS_BAD_SIGNATURE = "BAD_SIGNATURE"
STATUS_DEVELOPMENT_KEY = "DEVELOPMENT_KEY_IN_PRODUCTION"
STATUS_WRONG_EDITION = "WRONG_EDITION"
STATUS_WRONG_DEPLOYMENT = "WRONG_DEPLOYMENT"
STATUSES: tuple[str, ...] = (STATUS_NOT_REQUIRED, STATUS_VALID, STATUS_GRACE, STATUS_EXPIRED, STATUS_NOT_YET_VALID,
                             STATUS_MISSING, STATUS_MALFORMED, STATUS_UNTRUSTED_KEY, STATUS_BAD_SIGNATURE,
                             STATUS_DEVELOPMENT_KEY, STATUS_WRONG_EDITION, STATUS_WRONG_DEPLOYMENT)
USABLE: frozenset[str] = frozenset({STATUS_VALID, STATUS_GRACE})

REQUIRED_FIELDS: tuple[str, ...] = ("licence_id", "licensee", "edition", "issued_at", "not_before", "expires_at",
                                    "max_organizations", "max_seats", "features")
FEATURES: tuple[str, ...] = ("egress_lockdown", "local_llm", "invoiced_billing", "pitr")
MAX_GRACE_DAYS = 60


class LicenceFormatError(ValueError):
    pass


class LicenceRequired(FlowPilotError):
    status_code = 402
    code = "LICENCE_REQUIRED"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = {"code": "LICENCE_REQUIRED", **details}


@dataclass(frozen=True)
class LicenceDocument:
    payload: dict[str, Any]
    key_id: str
    signature: str

    def as_dict(self) -> dict[str, Any]:
        return {"format": FORMAT, "key_id": self.key_id, "payload": self.payload, "signature": self.signature}


@dataclass
class LicenceStatus:
    status: str
    edition: str
    reason: str = ""
    licence_id: Optional[str] = None
    licensee: Optional[str] = None
    key_id: Optional[str] = None
    expires_at: Optional[datetime] = None
    grace_until: Optional[datetime] = None
    days_left: Optional[int] = None
    max_organizations: Optional[int] = None
    max_seats: Optional[int] = None
    features: list[str] = field(default_factory=list)
    source: Optional[str] = None

    @property
    def usable(self) -> bool:
        return self.status in USABLE or self.status == STATUS_NOT_REQUIRED

    def as_dict(self) -> dict[str, Any]:
        return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in self.__dict__.items()} | {
            "usable": self.usable}


def _settings() -> Any:
    """The application's settings; in the offline tool (scripts/licence_tool.py, which may run where the
    application is not configured) the same names from the environment."""
    try:
        from app.core.config import settings

        return settings
    except Exception:  # noqa: BLE001
        from types import SimpleNamespace

        return SimpleNamespace(**{k: os.environ.get(k) for k in ("FLOWPILOT_EDITION", "ENVIRONMENT", "LICENCE_FILE",
                                                                "FLOWPILOT_DEPLOYMENT_ID")})


def edition() -> str:
    raw = str(getattr(_settings(), "FLOWPILOT_EDITION", EDITION_SAAS) or EDITION_SAAS).strip().lower()
    return raw if raw in EDITIONS else EDITION_SOVEREIGN  # an unknown edition is held to the stricter rules


def _ts(value: Any, name: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise LicenceFormatError(f"payload.{name} is not an ISO-8601 timestamp") from exc
    if moment.tzinfo is None:
        raise LicenceFormatError(f"payload.{name} has no timezone")
    return moment.astimezone(timezone.utc)


def parse(document: Any) -> LicenceDocument:
    if isinstance(document, (bytes, bytearray)):
        document = document.decode("utf-8-sig")
    if isinstance(document, str):
        try:
            document = json.loads(document)
        except ValueError as exc:
            raise LicenceFormatError("the licence is not JSON") from exc
    if not isinstance(document, Mapping):
        raise LicenceFormatError("the licence is not a JSON object")
    if document.get("format") != FORMAT:
        raise LicenceFormatError(f"the licence format is not {FORMAT}")
    payload = document.get("payload")
    if not isinstance(payload, Mapping):
        raise LicenceFormatError("the licence has no payload object")
    missing = [f for f in REQUIRED_FIELDS if f not in payload]
    if missing:
        raise LicenceFormatError(f"the payload lacks {missing}")
    for name in ("issued_at", "not_before", "expires_at"):
        _ts(payload[name], name)
    for name in ("max_organizations", "max_seats"):
        if not isinstance(payload[name], int) or isinstance(payload[name], bool) or payload[name] < 1:
            raise LicenceFormatError(f"payload.{name} must be a positive integer")
    if not isinstance(payload["features"], list) or not all(isinstance(f, str) for f in payload["features"]):
        raise LicenceFormatError("payload.features must be a list of strings")
    grace = payload.get("grace_days", 0)
    if not isinstance(grace, int) or isinstance(grace, bool) or not 0 <= grace <= MAX_GRACE_DAYS:
        raise LicenceFormatError(f"payload.grace_days must be 0 to {MAX_GRACE_DAYS}")
    if not str(payload["licence_id"]).strip() or len(str(payload["licence_id"])) > 64:
        raise LicenceFormatError("payload.licence_id must be 1 to 64 characters")
    key_id = str(document.get("key_id") or "")
    signature = str(document.get("signature") or "")
    if not key_id or not signature:
        raise LicenceFormatError("the licence carries no key_id or signature")
    return LicenceDocument(payload=dict(payload), key_id=key_id, signature=signature)


def issue(private_key_b64: str, payload: Mapping[str, Any]) -> LicenceDocument:
    """Sign a payload (the tool's half; lives here so issue and verify share one canonical form)."""
    body = dict(payload)
    public = signing.public_key_of(private_key_b64)
    doc = LicenceDocument(payload=body, key_id=signing.key_id(public),
                          signature=signing.sign(private_key_b64, signing.canonical_json(body)))
    parse(doc.as_dict())  # the issuer gets the same refusals the verifier would give
    return doc


def verify(doc: LicenceDocument, *, at: Optional[datetime] = None,
           trusted: Optional[Mapping[str, Mapping[str, Any]]] = None,
           environment: Optional[str] = None) -> LicenceStatus:
    ed = edition()
    moment = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    keys = licence_keys.TRUSTED_LICENCE_KEYS if trusted is None else trusted
    env = str(environment if environment is not None else getattr(_settings(), "ENVIRONMENT", "development")).lower()
    p = doc.payload
    base = dict(edition=ed, licence_id=str(p.get("licence_id")), licensee=str(p.get("licensee")), key_id=doc.key_id)
    key = keys.get(doc.key_id)
    if key is None:
        return LicenceStatus(STATUS_UNTRUSTED_KEY, reason="the licence is signed by a key this release does not "
                                                          "trust", **base)
    if signing.key_id(str(key["public_key"])) != doc.key_id:
        return LicenceStatus(STATUS_UNTRUSTED_KEY, reason="the pinned key does not match its key id", **base)
    if not signing.verify(str(key["public_key"]), signing.canonical_json(p), doc.signature):
        return LicenceStatus(STATUS_BAD_SIGNATURE, reason="the signature does not match the payload", **base)
    if env == "production" and not bool(key.get("production")):
        return LicenceStatus(STATUS_DEVELOPMENT_KEY, reason="a development key cannot license a production "
                                                            "deployment", **base)
    if str(p.get("edition")) != EDITION_SOVEREIGN:
        return LicenceStatus(STATUS_WRONG_EDITION, reason=f"the licence is for edition {p.get('edition')!r}", **base)
    bound = p.get("deployment_id")
    if bound:
        here = str(os.environ.get("FLOWPILOT_DEPLOYMENT_ID") or getattr(_settings(), "FLOWPILOT_DEPLOYMENT_ID", "")
                   or "")
        if here != str(bound):
            return LicenceStatus(STATUS_WRONG_DEPLOYMENT, reason="the licence is bound to another deployment", **base)
    not_before, expires = _ts(p["not_before"], "not_before"), _ts(p["expires_at"], "expires_at")
    grace_until = expires + timedelta(days=int(p.get("grace_days", 0) or 0))
    common = dict(expires_at=expires, grace_until=grace_until, max_organizations=int(p["max_organizations"]),
                  max_seats=int(p["max_seats"]), features=sorted(str(f) for f in p["features"]), **base)
    if moment < not_before:
        return LicenceStatus(STATUS_NOT_YET_VALID, reason="the licence is not valid yet", **common)
    if moment <= expires:
        return LicenceStatus(STATUS_VALID, days_left=(expires - moment).days, **common)
    if moment <= grace_until:
        return LicenceStatus(STATUS_GRACE, reason="expired; inside the grace period", days_left=0, **common)
    return LicenceStatus(STATUS_EXPIRED, reason="the licence and its grace period have ended", days_left=0, **common)


def _stored(db: Any) -> Optional[tuple[LicenceDocument, str]]:
    from sqlalchemy import text

    row = db.execute(text("SELECT payload, key_id, signature FROM platform_licences WHERE is_current")).first()
    if row is not None:
        return LicenceDocument(payload=dict(row[0]), key_id=row[1], signature=row[2]), "database"
    path = str(getattr(_settings(), "LICENCE_FILE", "") or "").strip()
    if path and os.path.isfile(path):
        with open(path, "rb") as handle:
            return parse(handle.read()), "file"
    return None


def current(db: Any, *, at: Optional[datetime] = None) -> LicenceStatus:
    ed = edition()
    try:
        stored = _stored(db)
    except LicenceFormatError as exc:
        return LicenceStatus(STATUS_MALFORMED if ed == EDITION_SOVEREIGN else STATUS_NOT_REQUIRED, edition=ed,
                             reason=str(exc))
    if stored is None:
        if ed == EDITION_SAAS:
            return LicenceStatus(STATUS_NOT_REQUIRED, edition=ed, reason="the hosted edition needs no licence")
        return LicenceStatus(STATUS_MISSING, edition=ed, reason="no licence is installed")
    doc, source = stored
    status = verify(doc, at=at)
    status.source = source
    if ed == EDITION_SAAS and status.status not in USABLE:
        # A broken licence on the hosted edition changes nothing; report it without letting it gate anything.
        status.reason = f"{status.status}: {status.reason} (ignored: the hosted edition needs no licence)"
        status.status = STATUS_NOT_REQUIRED
    return status


def install(db: Any, document: Any, *, actor_id: Any = None, at: Optional[datetime] = None) -> LicenceStatus:
    from sqlalchemy import text
    import uuid

    doc = parse(document)
    status = verify(doc, at=at)
    if status.status not in USABLE:
        raise LicenceRequired(f"the licence was refused: {status.status} ({status.reason})", status=status.status)
    db.execute(text("UPDATE platform_licences SET is_current = false WHERE is_current"))
    db.execute(text("INSERT INTO platform_licences (id, licence_id, key_id, payload, signature, is_current, "
                    "uploaded_by_user_id) VALUES (:id, :lid, :kid, CAST(:p AS jsonb), :s, true, :u)"),
               {"id": uuid.uuid4(), "lid": str(doc.payload["licence_id"]), "kid": doc.key_id,
                "p": json.dumps(doc.payload), "s": doc.signature, "u": actor_id})
    db.flush()
    logger.info("licence.installed", extra={"licence_id": str(doc.payload["licence_id"]), "key_id": doc.key_id})
    status.source = "database"
    return status


def usage(db: Any) -> dict[str, int]:
    from sqlalchemy import text

    orgs = db.execute(text("SELECT count(*) FROM organizations WHERE status <> 'ARCHIVED'")).scalar_one()
    seats = db.execute(text("SELECT count(DISTINCT user_id) FROM organization_members WHERE status = 'ACTIVE'")
                       ).scalar_one()
    return {"organizations": int(orgs), "seats": int(seats)}


def require_capacity(db: Any, *, operation: str, adding_organizations: int = 0, adding_seats: int = 0) -> None:
    """Raise LICENCE_REQUIRED on a sovereign deployment whose licence cannot cover the operation. No-op on SaaS."""
    if edition() != EDITION_SOVEREIGN:
        return
    status = current(db)
    if status.status not in USABLE:
        raise LicenceRequired(f"This deployment's licence is {status.status}: {status.reason}. {operation} needs a "
                              "valid licence.", status=status.status, operation=operation)
    counts = usage(db)
    if adding_organizations and counts["organizations"] + adding_organizations > int(status.max_organizations or 0):
        raise LicenceRequired(f"The licence covers {status.max_organizations} organizations; this deployment has "
                              f"{counts['organizations']}.", status=status.status, operation=operation,
                              limit="max_organizations")
    if adding_seats and counts["seats"] + adding_seats > int(status.max_seats or 0):
        raise LicenceRequired(f"The licence covers {status.max_seats} seats; this deployment has {counts['seats']}.",
                              status=status.status, operation=operation, limit="max_seats")


__all__ = ["EDITIONS", "EDITION_SAAS", "EDITION_SOVEREIGN", "FEATURES", "FORMAT", "LicenceDocument",
           "LicenceFormatError", "LicenceRequired", "LicenceStatus", "STATUSES", "USABLE", "current", "edition",
           "install", "issue", "parse", "require_capacity", "usage", "verify"]
