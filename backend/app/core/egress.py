"""ARCH-50 — the egress gate: ONE place that decides whether the platform may open a connection.

ARCH50-S1:egress-gate

WHERE IT SITS
=============

Every outbound client in `app/` is opened HERE, or is handed a transport made here:

    http_client(channel, ...)       the SSRF-safe HTTPS client (webhooks, warehouse control planes, ERP REST,
                                    OIDC / SAML metadata, the Azure BYOK probe), checked on every request
    httpx_client(channel, ...)      an httpx.Client whose transport checks every request (LLM provider SDKs,
                                    the operator's local model, the BYOK probes, the Dodo gateway) -- redirects
                                    included, because each hop goes back through the transport
    requests_session(channel, ...)  a requests.Session whose adapter checks every request (google-auth's token
                                    exchange, whose `token_uri` comes from a tenant-supplied key file)
    open_connection(channel, ...)   the only `socket.create_connection` outside the SSRF client (SMTP, SFTP)
    attach_boto(client, channel)    a botocore `before-send` hook: every S3 request (storage, the S3 bundle
                                    exporter, the backup mirror) is checked before it leaves
    guard(channel, host, port)      the decision itself, for the fixed-host SDKs (Stripe) and DNS resolvers

`verify_arch50.py` T3 walks every module under `app/` (and the operator scripts that talk to the network) and
fails if a socket, `http.client` connection, `httpx`, `requests`, `smtplib`, `paramiko.Transport`, a provider
SDK or `boto3.client` is opened anywhere else, or opened there without the gate.

THE DECISION
============

    1. A channel whose destination is ALWAYS the operator's (the local model, internal RPC) reaches only the
       host the operator configured -- in every mode. A tenant can never point these anywhere.
    2. Deployment DENY mode (`EGRESS_MODE=deny`, the sovereign / air-gapped edition): nothing leaves except a
       destination the operator declared -- derived from the operator's own settings for its channel (the
       object store for STORAGE, the relay for SMTP_PLATFORM, the resolvers for DNS, the local model, the
       reranker, the backup mirror) or listed in `EGRESS_OPERATOR_HOSTS` (`host`, `*.suffix`, an IP or CIDR,
       optionally `CHANNEL=` in front and `:port` behind).
    3. Tenant LOCKDOWN (`capability.egress_lockdown`, Enterprise): when an organization has switched it on,
       every connection made on its behalf over a channel that carries its data to a destination IT chose
       (webhooks, warehouses, ERP, identity metadata, its own SMTP server, LLM providers -- platform or BYOK)
       must match one of its allow rules.

A refusal FAILS CLOSED: `EgressDenied` (a `ForbiddenAddressError`, so every existing caller already treats it as
permanent -- never retried -- and a `FlowPilotError`: 403 `EGRESS_DENIED` at the API) is raised before a byte is
sent. It is recorded in `egress_refusals` (one row per organization, channel, host, reason and hour, with a
count) and, the first time in that hour, in the organization's audit log. A tenant policy that cannot be read
(the database is unreachable) is a refusal too (`POLICY_UNAVAILABLE`), never an allowance.

WHAT STILL WORKS WITH EVERYTHING DENIED
======================================

OCR (PaddleOCR) and embeddings (SentenceTransformers) run in-process; extraction, chat and the assistant run on
the operator's local model; object storage, the SMTP relay, the reranker and the backup mirror are declared
operator destinations; licensing verifies offline (Ed25519). Refused: public LLM providers and BYOK, tenant
webhooks / warehouses / ERP / SFTP / identity metadata to undeclared hosts, the card-payment gateways (the
sovereign edition bills through invoiced contracts), public DNS (domain verification) and ACME.

Infrastructure the platform cannot run without -- PostgreSQL and Redis -- is not an outbound client: their URLs
come only from the operator's environment and they are never opened on a tenant's behalf.
"""

from __future__ import annotations

import contextlib
import contextvars
import ipaddress
import logging
import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

from app.core.exceptions import FlowPilotError
from app.core.ssrf_client import ForbiddenAddressError, SSRFSafeHTTPClient, SSRFResponse

logger = logging.getLogger("app.core.egress")

# ---------------------------------------------------------------------------
# Vocabulary (mirrored by the migration's CHECKs and the console; T2/W3 gate the parity)
# ---------------------------------------------------------------------------

WEBHOOK = "WEBHOOK"
WAREHOUSE = "WAREHOUSE"
ERP_HTTP = "ERP_HTTP"
ERP_SFTP = "ERP_SFTP"
IDENTITY = "IDENTITY"
SMTP_TENANT = "SMTP_TENANT"
SMTP_PLATFORM = "SMTP_PLATFORM"
LLM_PROVIDER = "LLM_PROVIDER"
LLM_LOCAL = "LLM_LOCAL"
STORAGE = "STORAGE"
BILLING = "BILLING"
INTERNAL = "INTERNAL"
DNS = "DNS"
BACKUP = "BACKUP"

CHANNELS: tuple[str, ...] = (
    WEBHOOK, WAREHOUSE, ERP_HTTP, ERP_SFTP, IDENTITY, SMTP_TENANT, LLM_PROVIDER,
    SMTP_PLATFORM, LLM_LOCAL, STORAGE, BILLING, INTERNAL, DNS, BACKUP,
)
#: Channels that carry a tenant's data to a destination the tenant (or the platform, for LLM providers) chose:
#: the ones an organization's lockdown governs. The console lists exactly these as allow-rule channels.
TENANT_CHANNELS: frozenset[str] = frozenset({WEBHOOK, WAREHOUSE, ERP_HTTP, ERP_SFTP, IDENTITY, SMTP_TENANT,
                                             LLM_PROVIDER})
OPERATOR_CHANNELS: frozenset[str] = frozenset(CHANNELS) - TENANT_CHANNELS
#: Operator channels that may reach ONLY the operator's configured host, in every mode.
STRICT_OPERATOR_CHANNELS: frozenset[str] = frozenset({LLM_LOCAL, INTERNAL})

CHANNEL_LABELS: dict[str, str] = {
    WEBHOOK: "Outgoing webhooks",
    WAREHOUSE: "Warehouse & BI egress",
    ERP_HTTP: "ERP posting (REST / OData)",
    ERP_SFTP: "ERP posting (SFTP)",
    IDENTITY: "SSO metadata (OIDC / SAML)",
    SMTP_TENANT: "Your SMTP server",
    LLM_PROVIDER: "AI model providers (platform and BYOK)",
    SMTP_PLATFORM: "Platform email relay",
    LLM_LOCAL: "Operator's local model",
    STORAGE: "Object storage",
    BILLING: "Payment gateway",
    INTERNAL: "Internal services (reranker)",
    DNS: "DNS resolvers (domain verification)",
    BACKUP: "Off-host backup mirror",
}

MODE_OPEN = "open"
MODE_DENY = "deny"
MODES: tuple[str, ...] = (MODE_OPEN, MODE_DENY)

REASON_DEPLOYMENT_DENY = "DEPLOYMENT_DENY"
REASON_TENANT_LOCKDOWN = "TENANT_LOCKDOWN"
REASON_OPERATOR_ONLY = "OPERATOR_ONLY"
REASON_INVALID = "INVALID_DESTINATION"
REASON_POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
REASONS: tuple[str, ...] = (REASON_DEPLOYMENT_DENY, REASON_TENANT_LOCKDOWN, REASON_OPERATOR_ONLY, REASON_INVALID,
                            REASON_POLICY_UNAVAILABLE)

EGRESS_DENIED_CODE = "EGRESS_DENIED"

#: How long a worker trusts its copy of an organization's policy. A rule added in the console reaches every
#: worker within this; the process that wrote it drops its copy at once.
POLICY_TTL_SECONDS: float = 15.0

_ZERO_UUID = uuid.UUID(int=0)
_UNSET: Any = object()


class EgressDenied(ForbiddenAddressError, FlowPilotError):
    """Raised BEFORE a connection opens. Permanent: a caller must not retry it."""

    status_code = 403
    code = EGRESS_DENIED_CODE

    def __init__(self, decision: "Decision") -> None:
        self.decision = decision
        where = decision.host + (f":{decision.port}" if decision.port else "")
        super().__init__(f"Outbound connection to {where} over {decision.channel} refused ({decision.reason}): "
                         f"{decision.explanation}")
        self.details = {"code": EGRESS_DENIED_CODE, "channel": decision.channel, "host": decision.host,
                        "port": decision.port, "reason": decision.reason, "mode": decision.mode}


def refusal_in(exc: Optional[BaseException]) -> Optional[EgressDenied]:
    """The gate's refusal inside an exception chain. The provider SDKs (openai, groq, anthropic, mistral,
    google-genai) wrap whatever their transport raises in their own connection error, after their own retries; the
    refusal is still the cause, and a caller must treat it as a refusal (permanent, 403), not as an outage."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, EgressDenied):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return None


class PolicyUnavailable(RuntimeError):
    """An organization's egress policy could not be read. Never read as 'no lockdown'."""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    channel: str
    host: str
    port: Optional[int]
    mode: str
    reason: Optional[str] = None
    explanation: str = ""
    organization_id: Optional[uuid.UUID] = None
    matched: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "channel": self.channel, "host": self.host, "port": self.port,
                "mode": self.mode, "reason": self.reason, "explanation": self.explanation,
                "organization_id": str(self.organization_id) if self.organization_id else None,
                "matched": self.matched}


# ---------------------------------------------------------------------------
# Host patterns
# ---------------------------------------------------------------------------


def normalize_host(host: Any) -> str:
    text = str(host or "").strip().lower()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    return text.rstrip(".")


@dataclass(frozen=True)
class HostPattern:
    """`host`, `*.suffix` (strict subdomains), an IP literal or a CIDR; an optional port."""

    raw: str
    kind: str  # EXACT | SUFFIX | IP | CIDR
    value: str
    port: Optional[int] = None

    def matches(self, host: str, port: Optional[int]) -> bool:
        if self.port is not None and port is not None and int(port) != self.port:
            return False
        if self.port is not None and port is None:
            return False
        host = normalize_host(host)
        if self.kind == "EXACT":
            return host == self.value
        if self.kind == "SUFFIX":
            return host.endswith("." + self.value) and host != self.value
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        if self.kind == "IP":
            return ip == ipaddress.ip_address(self.value)
        return ip in ipaddress.ip_network(self.value, strict=False)


_LABEL_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-")


class PatternError(ValueError):
    pass


def parse_pattern(raw: str, port: Optional[int] = None) -> HostPattern:
    """Parse and validate one pattern. `host:port` is accepted (IPv6 only as `[addr]:port`)."""
    text = str(raw or "").strip().lower()
    if not text or len(text) > 253:
        raise PatternError("a host pattern is 1 to 253 characters")
    if "://" in text:
        raise PatternError(f"{raw!r} is a URL: enter the host name alone")
    if any(ch in text for ch in "?#@ \t\\"):
        raise PatternError(f"{raw!r} is not a host name, *.suffix, IP address or CIDR")
    # port suffix
    if text.startswith("["):
        close = text.find("]")
        if close < 0:
            raise PatternError(f"{raw!r} has an unclosed '['")
        rest = text[close + 1:]
        text = text[1:close]
        if rest:
            if not rest.startswith(":") or not rest[1:].isdigit():
                raise PatternError(f"{raw!r}: expected [address]:port")
            port = int(rest[1:])
    elif text.count(":") == 1:
        head, _, tail = text.partition(":")
        if not tail.isdigit():
            raise PatternError(f"{raw!r}: the port must be a number")
        text, port = head, int(tail)
    if port is not None and not 1 <= int(port) <= 65535:
        raise PatternError("a port is 1 to 65535")
    if "/" in text:
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError as exc:
            raise PatternError(f"{raw!r} is not a valid CIDR") from exc
        if net.prefixlen == 0:
            raise PatternError("a CIDR of /0 would allow everything; list the destinations instead")
        return HostPattern(raw=str(raw).strip(), kind="CIDR", value=str(net), port=port)
    try:
        ip = ipaddress.ip_address(text)
        return HostPattern(raw=str(raw).strip(), kind="IP", value=str(ip), port=port)
    except ValueError:
        pass
    kind = "EXACT"
    if text.startswith("*."):
        kind, text = "SUFFIX", text[2:]
    if "*" in text:
        raise PatternError(f"{raw!r}: a wildcard is only allowed as a leading '*.'")
    text = text.rstrip(".")
    labels = text.split(".")
    if kind == "SUFFIX" and len(labels) < 2:
        raise PatternError(f"{raw!r}: '*.' needs a domain with at least two labels (e.g. *.example.com)")
    for label in labels:
        if not label or len(label) > 63 or not set(label) <= _LABEL_OK or label.startswith("-") or label.endswith("-"):
            raise PatternError(f"{raw!r} is not a valid host name")
    return HostPattern(raw=str(raw).strip(), kind=kind, value=text, port=port)


# ---------------------------------------------------------------------------
# Deployment configuration (operator-only: environment, never the database)
# ---------------------------------------------------------------------------


def _settings() -> Any:
    from app.core.config import settings

    return settings


def current_mode() -> str:
    raw = str(getattr(_settings(), "EGRESS_MODE", MODE_OPEN) or MODE_OPEN).strip().lower()
    return raw if raw in MODES else MODE_DENY  # an unknown mode is read as the safe one


def _url_host_port(url: Optional[str]) -> Optional[tuple[str, Optional[int]]]:
    if not url:
        return None
    parts = urlsplit(str(url).strip())
    if not parts.hostname:
        return None
    return normalize_host(parts.hostname), parts.port


def operator_destinations() -> dict[str, list[HostPattern]]:
    """What the operator's own settings declare, per channel (the auto-derived part of the deny-mode allowlist),
    plus every `EGRESS_OPERATOR_HOSTS` entry. Recomputed on each call: settings are the source of truth."""
    s = _settings()
    out: dict[str, list[HostPattern]] = {c: [] for c in CHANNELS}

    def add(channel: str, host: Optional[str], port: Optional[int] = None) -> None:
        if not host:
            return
        try:
            out[channel].append(parse_pattern(host, port))
        except PatternError:
            logger.warning("egress.operator_destination_unparseable", extra={"channel": channel, "host": host})

    storage = _url_host_port(getattr(s, "S3_ENDPOINT_URL", None))
    if storage:
        add(STORAGE, storage[0], storage[1])
    local = _url_host_port(getattr(s, "LOCAL_LLM_BASE_URL", None))
    if local:
        add(LLM_LOCAL, local[0], local[1])
    reranker = _url_host_port(getattr(s, "RERANKER_URL", None))
    if reranker:
        add(INTERNAL, reranker[0], reranker[1])
    smtp_host = str(getattr(s, "PLATFORM_SMTP_HOST", "") or "").strip()
    if smtp_host:
        add(SMTP_PLATFORM, smtp_host)
    for resolver in str(getattr(s, "DNS_RESOLVERS", "") or "").split(","):
        if resolver.strip():
            add(DNS, resolver.strip())
    backup = _url_host_port(os.environ.get("FLOWPILOT_BACKUP_S3_ENDPOINT"))
    if backup:
        add(BACKUP, backup[0], backup[1])
    for entry in str(getattr(s, "EGRESS_OPERATOR_HOSTS", "") or "").replace(";", ",").split(","):
        entry = entry.strip()
        if not entry:
            continue
        channel = None
        if "=" in entry:
            channel, _, entry = entry.partition("=")
            channel = channel.strip().upper()
            if channel not in out:
                logger.warning("egress.operator_host_unknown_channel", extra={"channel": channel})
                continue
        try:
            pattern = parse_pattern(entry.strip())
        except PatternError:
            logger.warning("egress.operator_host_unparseable", extra={"entry": entry})
            continue
        for target in ([channel] if channel else CHANNELS):
            out[target].append(pattern)
    return out


def _operator_allows(channel: str, host: str, port: Optional[int]) -> Optional[str]:
    for pattern in operator_destinations().get(channel, []):
        if pattern.matches(host, port):
            return pattern.raw
    return None


# ---------------------------------------------------------------------------
# Attribution: on whose behalf a connection is opened
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Attribution:
    organization_id: Optional[uuid.UUID]
    db: Any = None


_ATTRIBUTION: contextvars.ContextVar[Optional[_Attribution]] = contextvars.ContextVar("egress_attribution",
                                                                                       default=None)


@contextlib.contextmanager
def attributed(organization_id: Any, *, db: Any = None) -> Iterator[None]:
    """Connections opened inside this block are made on this organization's behalf. The job loops wrap every
    handler in it; an explicit `organization_id=` on an opener always wins. `db` (tests, dry runs) makes the
    policy read and the refusal record use that session instead of an independent one."""
    org = _as_uuid(organization_id)
    token = _ATTRIBUTION.set(_Attribution(org, db))
    try:
        yield
    finally:
        _ATTRIBUTION.reset(token)


def current_organization() -> Optional[uuid.UUID]:
    att = _ATTRIBUTION.get()
    return att.organization_id if att else None


def _as_uuid(value: Any) -> Optional[uuid.UUID]:
    if value is None or value == "":
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Tenant policy (read-through cache; writes in this process drop it)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    id: Optional[uuid.UUID]
    channel: Optional[str]
    pattern: HostPattern

    def permits(self, channel: str, host: str, port: Optional[int]) -> bool:
        return (self.channel is None or self.channel == channel) and self.pattern.matches(host, port)


@dataclass(frozen=True)
class TenantPolicy:
    lockdown: bool
    rules: tuple[Rule, ...] = ()
    loaded_at: float = field(default_factory=time.monotonic)

    def permits(self, channel: str, host: str, port: Optional[int]) -> Optional[Rule]:
        for rule in self.rules:
            if rule.permits(channel, host, port):
                return rule
        return None


_OPEN_POLICY = TenantPolicy(lockdown=False)
_cache: dict[uuid.UUID, TenantPolicy] = {}
_cache_lock = threading.Lock()


def clear_cache(organization_id: Any = None) -> None:
    with _cache_lock:
        if organization_id is None:
            _cache.clear()
        else:
            _cache.pop(_as_uuid(organization_id), None)  # type: ignore[arg-type]


def _read_policy(db: Any, organization_id: uuid.UUID) -> TenantPolicy:
    from sqlalchemy import text

    row = db.execute(text("SELECT lockdown_enabled FROM egress_policies WHERE organization_id = :o"),
                     {"o": organization_id}).first()
    if row is None or not bool(row[0]):
        return TenantPolicy(lockdown=False)
    rules = []
    for rid, channel, pattern, port in db.execute(
            text("SELECT id, channel, host_pattern, port FROM egress_allow_rules WHERE organization_id = :o "
                 "ORDER BY created_at, id"), {"o": organization_id}).all():
        try:
            rules.append(Rule(rid, channel, parse_pattern(pattern, port)))
        except PatternError:
            logger.error("egress.rule_unparseable", extra={"rule_id": str(rid)})
    return TenantPolicy(lockdown=True, rules=tuple(rules))


def policy_for(organization_id: uuid.UUID, *, db: Any = None) -> TenantPolicy:
    if db is not None:
        return _read_policy(db, organization_id)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(organization_id)
    if hit is not None and now - hit.loaded_at < POLICY_TTL_SECONDS:
        return hit
    try:
        from app.db.session import SessionLocal

        with SessionLocal() as session:
            policy = _read_policy(session, organization_id)
    except Exception as exc:  # noqa: BLE001 -- the database is unreachable or the table is missing
        raise PolicyUnavailable(f"{type(exc).__name__}: {exc}") from exc
    with _cache_lock:
        _cache[organization_id] = policy
    return policy


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------


def decide(channel: str, host: Any, port: Optional[int] = None, *, organization_id: Any = _UNSET,
           db: Any = None) -> Decision:
    if channel not in CHANNELS:
        raise ValueError(f"unknown egress channel {channel!r}")
    host_n = normalize_host(host)
    att = _ATTRIBUTION.get()
    org = _as_uuid(organization_id) if organization_id is not _UNSET else (att.organization_id if att else None)
    session = db if db is not None else (att.db if att else None)
    mode = current_mode()
    port_i = int(port) if port not in (None, "") else None

    def refuse(reason: str, explanation: str) -> Decision:
        return Decision(False, channel, host_n, port_i, mode, reason, explanation, org)

    if not host_n or len(host_n) > 253 or any(ch.isspace() for ch in host_n):
        return refuse(REASON_INVALID, "the destination has no usable host name")
    operator = _operator_allows(channel, host_n, port_i)
    if channel in STRICT_OPERATOR_CHANNELS and operator is None:
        return refuse(REASON_OPERATOR_ONLY, "this channel reaches only the host the operator configured")
    if mode == MODE_DENY and operator is None:
        return refuse(REASON_DEPLOYMENT_DENY, "this deployment denies outbound connections except to "
                                              "destinations its operator declared")
    matched = operator
    if channel in TENANT_CHANNELS and org is not None:
        try:
            policy = policy_for(org, db=session)
        except PolicyUnavailable as exc:
            return refuse(REASON_POLICY_UNAVAILABLE, f"the organization's egress policy could not be read ({exc})")
        if policy.lockdown:
            rule = policy.permits(channel, host_n, port_i)
            if rule is None:
                return refuse(REASON_TENANT_LOCKDOWN, "the organization's egress lockdown has no rule allowing "
                                                      "this destination")
            matched = rule.pattern.raw
    return Decision(True, channel, host_n, port_i, mode, None, "", org, matched)


def now() -> datetime:
    """ARCH-50's one clock (refusal buckets); the chaos and DR gates pin it."""
    return datetime.now(timezone.utc)


def _record_refusal(decision: Decision, session: Any = None) -> None:
    from sqlalchemy import text

    moment = now()
    bucket = moment.replace(minute=0, second=0, microsecond=0)
    sql = text(
        "INSERT INTO egress_refusals (id, organization_id, channel, host, port, reason, mode, bucket_start, "
        "first_at, last_at, count) VALUES (:id, :o, :c, :h, :p, :r, :m, :b, :t, :t, 1) "
        "ON CONFLICT ((COALESCE(organization_id, '00000000-0000-0000-0000-000000000000'::uuid)), channel, host, "
        "port, reason, bucket_start) DO UPDATE SET count = egress_refusals.count + 1, "
        # Concurrent refusals take their timestamps before they queue on the row: the one that commits last may
        # carry the EARLIEST instant (verify_arch50 X1 found last_at < first_at refused by the window CHECK, and the
        # refusal went uncounted). The window only ever widens.
        "first_at = LEAST(egress_refusals.first_at, EXCLUDED.first_at), "
        "last_at = GREATEST(egress_refusals.last_at, EXCLUDED.last_at) "
        "RETURNING count")
    params = {"id": uuid.uuid4(), "o": decision.organization_id, "c": decision.channel, "h": decision.host[:253],
              "p": int(decision.port or 0), "r": decision.reason, "m": decision.mode, "b": bucket, "t": moment}
    count = None
    try:
        if session is not None:
            with session.begin_nested():
                count = session.execute(sql, params).scalar()
        else:
            from app.db.session import SessionLocal

            with SessionLocal() as own:
                count = own.execute(sql, params).scalar()
                own.commit()
    except Exception as exc:  # noqa: BLE001 -- recording must never turn a refusal into an allowance
        logger.error("egress.refusal_not_recorded", extra={"error": f"{type(exc).__name__}: {exc}"})
    logger.warning("egress.refused", extra={"channel": decision.channel, "host": decision.host,
                                            "port": decision.port, "reason": decision.reason,
                                            "mode": decision.mode,
                                            "organization_id": str(decision.organization_id or "")})
    if decision.organization_id is not None and count == 1:
        try:
            from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
            from app.services import audit_service

            details = {"operation": "egress.refused", "channel": decision.channel, "host": decision.host,
                       "port": decision.port, "reason": decision.reason, "mode": decision.mode}
            if session is not None:
                audit_service.record(session, organization_id=decision.organization_id,
                                     resource_type=AuditResourceType.ORGANIZATION,
                                     resource_id=decision.organization_id, action=AuditAction.ACCESSED,
                                     outcome=AuditOutcome.DENIED, details=details)
                session.flush()
            else:
                audit_service.record_independently(organization_id=decision.organization_id,
                                                   resource_type=AuditResourceType.ORGANIZATION,
                                                   resource_id=decision.organization_id,
                                                   action=AuditAction.ACCESSED, outcome=AuditOutcome.DENIED,
                                                   details=details)
        except Exception as exc:  # noqa: BLE001
            logger.error("egress.refusal_not_audited", extra={"error": f"{type(exc).__name__}: {exc}"})


def guard(channel: str, host: Any, port: Optional[int] = None, *, organization_id: Any = _UNSET, db: Any = None,
          record: bool = True) -> Decision:
    """Decide, and raise `EgressDenied` (recorded and audited) unless the connection may open."""
    decision = decide(channel, host, port, organization_id=organization_id, db=db)
    if not decision.allowed:
        if record:
            att = _ATTRIBUTION.get()
            _record_refusal(decision, db if db is not None else (att.db if att else None))
        raise EgressDenied(decision)
    return decision


def guard_url(channel: str, url: str, *, organization_id: Any = _UNSET, db: Any = None) -> Decision:
    parts = urlsplit(str(url))
    port = parts.port or (443 if parts.scheme in ("https", "wss") else 80 if parts.scheme in ("http", "ws") else None)
    return guard(channel, parts.hostname or "", port, organization_id=organization_id, db=db)


# ---------------------------------------------------------------------------
# The openers -- the only ways to reach the network from app/
# ---------------------------------------------------------------------------


class GuardedHTTPClient(SSRFSafeHTTPClient):
    """The SSRF-safe client (https only, private ranges refused, the connection pinned to the checked
    address), with the egress decision taken on every request before DNS is even consulted."""

    def __init__(self, channel: str, organization_id: Any = _UNSET, **kwargs: Any) -> None:
        if channel not in CHANNELS:
            raise ValueError(f"unknown egress channel {channel!r}")
        super().__init__(**kwargs)
        self.egress_channel = channel
        self._egress_org = organization_id

    def request(self, method: str, url: str, *, headers: Optional[dict[str, str]] = None,
                body: bytes = b"") -> SSRFResponse:
        parts = urlsplit(url)
        guard(self.egress_channel, parts.hostname or "", parts.port or 443, organization_id=self._egress_org)
        return super().request(method, url, headers=headers, body=body)


def http_client(channel: str, *, organization_id: Any = _UNSET, **kwargs: Any) -> GuardedHTTPClient:
    return GuardedHTTPClient(channel, organization_id, **kwargs)


def _guard_httpx_request(channel: str, organization_id: Any, request: Any) -> None:
    url = request.url
    port = url.port or (443 if url.scheme == "https" else 80)
    guard(channel, url.host, port, organization_id=organization_id)


def httpx_client(channel: str, *, organization_id: Any = _UNSET, timeout: Any = None, **kwargs: Any) -> Any:
    """An httpx.Client whose transport takes the egress decision on every request (redirect hops included)."""
    import httpx

    if channel not in CHANNELS:
        raise ValueError(f"unknown egress channel {channel!r}")

    class _GuardTransport(httpx.HTTPTransport):
        def handle_request(self, request: Any) -> Any:
            _guard_httpx_request(channel, organization_id, request)
            return super().handle_request(request)

    if timeout is not None:
        kwargs["timeout"] = timeout
    return httpx.Client(transport=_GuardTransport(), **kwargs)


def requests_session(channel: str, *, organization_id: Any = _UNSET) -> Any:
    import requests
    from requests.adapters import HTTPAdapter

    if channel not in CHANNELS:
        raise ValueError(f"unknown egress channel {channel!r}")

    class _GuardAdapter(HTTPAdapter):
        def send(self, request: Any, *args: Any, **kwargs: Any) -> Any:
            parts = urlsplit(request.url)
            guard(channel, parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80),
                  organization_id=organization_id)
            return super().send(request, *args, **kwargs)

    session = requests.Session()
    session.mount("https://", _GuardAdapter())
    session.mount("http://", _GuardAdapter())
    return session


def open_connection(channel: str, host: str, port: int, *, timeout: Optional[float] = None,
                    organization_id: Any = _UNSET, address: Optional[str] = None,
                    source_address: Optional[tuple[str, int]] = None) -> socket.socket:
    """The decision is taken on the NAME the tenant or operator configured; `address` is the pre-validated IP
    the caller pinned (SMTP and SFTP resolve and check it first, so a second lookup cannot rebind the name)."""
    guard(channel, host, port, organization_id=organization_id)
    return socket.create_connection((address or host, int(port)), timeout, source_address)


def attach_boto(client: Any, channel: str, *, organization_id: Any = _UNSET) -> Any:
    """Check every botocore request before it is sent (`before-send` runs per attempt, redirects included)."""
    if channel not in CHANNELS:
        raise ValueError(f"unknown egress channel {channel!r}")

    def _before_send(request: Any = None, **_: Any) -> None:
        parts = urlsplit(getattr(request, "url", "") or "")
        guard(channel, parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80),
              organization_id=organization_id)
        return None

    client.meta.events.register("before-send", _before_send)
    return client


# ---------------------------------------------------------------------------
# What the console shows (operator view)
# ---------------------------------------------------------------------------

#: Where each channel is opened -- the inventory T3 proves complete.
INVENTORY: dict[str, tuple[str, ...]] = {
    WEBHOOK: ("app/services/webhook_dispatch.py",),
    WAREHOUSE: ("app/services/analytics/connectors/base.py", "app/services/analytics/connectors/bigquery.py",
                "app/services/analytics/connectors/s3_bundle.py"),
    ERP_HTTP: ("app/services/erp/transport/http.py",),
    ERP_SFTP: ("app/services/erp/transport/sftp.py",),
    IDENTITY: ("app/services/identity/_integration.py",),
    SMTP_TENANT: ("app/services/email_service.py",),
    SMTP_PLATFORM: ("app/services/email_service.py",),
    LLM_PROVIDER: ("app/services/llm_service.py", "app/services/byok/provider_clients.py",
                   "app/services/byok/credential_service.py"),
    LLM_LOCAL: ("app/services/sovereign/local_llm.py",),
    STORAGE: ("app/core/storage/s3.py",),
    BILLING: ("app/services/billing/stripe_gateway.py", "app/services/billing/dodo_gateway.py"),
    INTERNAL: ("app/core/internal_http.py",),
    DNS: ("app/services/identity/dns_service.py",),
    BACKUP: ("scripts/backup_floor.py",),
}


def deployment_summary() -> dict[str, Any]:
    ops = operator_destinations()
    return {
        "mode": current_mode(),
        "channels": [
            {"channel": c, "label": CHANNEL_LABELS[c], "tenant_governed": c in TENANT_CHANNELS,
             "operator_only": c in STRICT_OPERATOR_CHANNELS,
             "declared": [p.raw for p in ops.get(c, [])], "opened_in": list(INVENTORY.get(c, ()))}
            for c in CHANNELS
        ],
    }


__all__ = [
    "BACKUP", "BILLING", "CHANNELS", "CHANNEL_LABELS", "DNS", "Decision", "EGRESS_DENIED_CODE", "ERP_HTTP",
    "ERP_SFTP", "EgressDenied", "GuardedHTTPClient", "HostPattern", "IDENTITY", "INTERNAL", "INVENTORY",
    "LLM_LOCAL", "LLM_PROVIDER", "MODES", "MODE_DENY", "MODE_OPEN", "OPERATOR_CHANNELS", "PatternError",
    "PolicyUnavailable", "REASONS", "SMTP_PLATFORM", "SMTP_TENANT", "STORAGE", "STRICT_OPERATOR_CHANNELS",
    "TENANT_CHANNELS", "TenantPolicy", "WAREHOUSE", "WEBHOOK", "attach_boto", "attributed", "clear_cache",
    "current_mode", "current_organization", "decide", "deployment_summary", "guard", "guard_url", "http_client",
    "httpx_client", "normalize_host", "now", "open_connection", "operator_destinations", "parse_pattern",
    "policy_for", "refusal_in", "requests_session",
]
