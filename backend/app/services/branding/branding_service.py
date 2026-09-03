"""ARCH-25 §3, §4 — brand tokens, tenant-scoped assets, sender domains.

INVARIANT 6, AND WHERE IT ACTUALLY LIVES
========================================

"Branding assets are tenant-scoped in regional storage with no cross-tenant
read path" is delivered by two lines and one assertion:

    driver = residency_service.driver_for_organization(organization)
    key = tenant_key(organization_id=..., namespace=StorageNamespace.LOGOS, ...)

`driver_for_organization` routes to the tenant's residency region and RAISES
if that region has no bucket — it does not fall back to the default bucket,
which is what makes the residency column mean something. `tenant_key` produces
`{organization_id}/logos/{file_id}.png`, so the key itself carries the tenant
boundary and `assert_key_belongs_to` can check it later.

This is deliberately NOT what the existing workspace logo upload does.
`app/api/v1/upload.py:upload_logo` writes `logos/{uuid4}.png` through the
global `get_storage_driver()` — no tenant prefix, no residency routing. That
predates ARCH-20 and is recorded as a pre-existing finding rather than fixed
here; ARCH-25 does not inherit it.

The third piece is `_assert_asset_belongs_to`, which every read and every FK
write passes through. It is a weaker guarantee than a composite foreign key
would have been, and the model module says plainly why the composite FK was
rejected. verify_arch25.py G9 asserts this assertion still exists, because an
assertion nothing checks is the orphaned-guard pattern with extra steps.

INVARIANT 5, AND WHY IT IS A STRING RATHER THAN A FLAG
======================================================

A lapsed sender domain must degrade VISIBLY. `sender_degradation_reason` on
the model returns a sentence naming the domain, and this module never returns
a bare boolean for that condition. A flag would push the copy into a frontend
switch statement, and the day someone adds a `default:` branch the lapse
becomes silent again — which is precisely the failure the invariant names.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.storage import (
    ObjectNotFoundError,
    StorageNamespace,
    assert_key_belongs_to,
    tenant_key,
)
from app.models.audit_log import AuditAction, AuditResourceType
from app.models.organization import Organization
from app.models.tenant_branding import (
    BRANDING_COLOR_TOKENS,
    SENDER_STATUS_LAPSED,
    SENDER_STATUS_PENDING,
    SENDER_STATUS_UNSET,
    SENDER_STATUS_VERIFIED,
    TenantBranding,
)
from app.models.uploaded_file import UploadedFile
from app.schemas.tenant_branding import (
    BrandingManifest,
    SenderDnsRecord,
    SenderDomainStatusResponse,
    TenantBrandingUpdate,
)
from app.services import audit_service
from app.services.branding.errors import (
    BrandingAssetError,
    CrossTenantAssetError,
    SenderDomainError,
)
from app.services.compliance import residency_service
from app.services.identity import dns_service

logger = logging.getLogger("app.services.branding.branding")

LOGO_MIME = "image/png"
LOGO_SUFFIX = "png"

ASSET_LOGO = "logo"
ASSET_FAVICON = "favicon"
ASSET_KINDS: tuple[str, ...] = (ASSET_LOGO, ASSET_FAVICON)

#: Public asset paths. Host-resolved, no organization id anywhere in them —
#: see the manifest docstring in app/schemas/tenant_branding.py.
LOGO_PUBLIC_PATH = "/api/v1/branding/logo"
FAVICON_PUBLIC_PATH = "/api/v1/branding/favicon"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_branding(
    db: Session, *, organization_id: uuid.UUID
) -> Optional[TenantBranding]:
    return db.execute(
        select(TenantBranding).where(
            TenantBranding.organization_id == organization_id
        )
    ).scalar_one_or_none()


def get_or_create_branding(
    db: Session, *, organization_id: uuid.UUID
) -> TenantBranding:
    """One row per tenant, created lazily on first read of the console.

    Creating it on read rather than on organization creation keeps the
    backfill out of ARCH-25: there is no migration populating a row for every
    existing organization, and a tenant who never opens the console never
    gets one. `is_enabled` defaults false, so the row's existence changes
    nothing a visitor sees.
    """
    existing = get_branding(db, organization_id=organization_id)
    if existing is not None:
        return existing

    row = TenantBranding(organization_id=organization_id)
    db.add(row)
    db.flush([row])
    return row


def _asset_url(kind: str) -> str:
    return LOGO_PUBLIC_PATH if kind == ASSET_LOGO else FAVICON_PUBLIC_PATH


def _assert_asset_belongs_to(
    record: Optional[UploadedFile], *, organization_id: uuid.UUID
) -> UploadedFile:
    """ARCH-25 invariant 6, at every crossing.

    Raises `CrossTenantAssetError` (404, not 403) when the row is missing,
    soft-deleted, or owned by a different tenant. All three collapse to one
    response on purpose: distinguishing "no such file" from "not your file"
    tells the caller a file id exists, which is the leak.

    The storage key is checked as well as the FK. They should never disagree,
    but the key is what the driver actually reads, and a row whose
    `file_path` points outside its own tenant prefix is the exact shape of the
    bug this guards against.
    """
    if record is None or record.deleted_at is not None:
        raise CrossTenantAssetError("Branding asset not found.")
    if record.organization_id != organization_id:
        logger.error(
            "branding.cross_tenant_asset_refused",
            extra={
                "file_id": str(record.id),
                "file_organization_id": str(record.organization_id),
                "requesting_organization_id": str(organization_id),
            },
        )
        raise CrossTenantAssetError("Branding asset not found.")
    try:
        assert_key_belongs_to(record.file_path, organization_id)
    except Exception as exc:  # noqa: BLE001 - key grammar violations are fatal
        logger.error(
            "branding.asset_key_outside_tenant_prefix",
            extra={"file_id": str(record.id), "file_path": record.file_path},
        )
        raise CrossTenantAssetError("Branding asset not found.") from exc
    return record


def resolve_asset(
    db: Session, *, branding: TenantBranding, kind: str
) -> UploadedFile:
    file_id = (
        branding.logo_file_id if kind == ASSET_LOGO else branding.favicon_file_id
    )
    if file_id is None:
        raise CrossTenantAssetError("Branding asset not found.")
    record = db.get(UploadedFile, file_id)
    return _assert_asset_belongs_to(
        record, organization_id=branding.organization_id
    )


def read_asset_bytes(
    *, organization: Organization, record: UploadedFile
) -> bytes:
    driver = residency_service.driver_for_organization(organization)
    try:
        return driver.get(record.file_path)
    except ObjectNotFoundError as exc:
        logger.error(
            "branding.asset_object_missing",
            extra={"file_id": str(record.id), "file_path": record.file_path},
        )
        raise CrossTenantAssetError("Branding asset not found.") from exc


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def update_branding(
    db: Session,
    *,
    branding: TenantBranding,
    payload: TenantBrandingUpdate,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> TenantBranding:
    """Apply only the fields the caller actually sent.

    `applied_fields()` reads `model_fields_set`, so an omitted key leaves the
    stored value alone and an explicit null clears it. Without that
    distinction a console rendering four colour pickers and submitting all of
    them would erase a value set on another screen, and the erasure would look
    like a successful save.

    Assets are not settable here: `logo_file_id` and `favicon_file_id` are
    absent from the schema entirely, so an administrator cannot point their
    branding at a file id they do not own. See `attach_asset`.
    """
    changed = payload.applied_fields()
    if not changed:
        return branding

    for field, value in changed.items():
        setattr(branding, field, value)

    branding.updated_by_user_id = actor_id
    db.flush([branding])

    audit_service.record(
        db,
        organization_id=branding.organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.TENANT_BRANDING,
        resource_id=branding.id,
        action=AuditAction.BRANDING_UPDATED,
        details={"fields": sorted(changed.keys())},
        **(audit_context or {}),
    )
    return branding


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


def _normalise_image(raw: bytes, *, kind: str) -> bytes:
    """Decode, bound, and re-encode as PNG.

    Re-encoding is the security step, not the resizing. An uploaded file is
    parsed by Pillow and written back out from the decoded pixels, so an SVG
    with a script element, a polyglot GIF/HTML file, or EXIF carrying anything
    at all does not survive the round trip. What lands in storage is bytes we
    produced.
    """
    if not raw:
        raise BrandingAssetError("The uploaded file is empty.")

    limit = int(
        getattr(settings, "BRANDING_MAX_LOGO_BYTES", 2 * 1024 * 1024)
        if kind == ASSET_LOGO
        else getattr(settings, "BRANDING_MAX_FAVICON_BYTES", 512 * 1024)
    )
    if len(raw) > limit:
        raise BrandingAssetError(
            f"The {kind} exceeds {limit // 1024} KB."
        )

    max_dimension = int(getattr(settings, "BRANDING_MAX_IMAGE_DIMENSION", 2048))

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:  # pragma: no cover - Pillow is a hard dependency
        raise BrandingAssetError(
            "Image processing is unavailable on this deployment."
        ) from exc

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(raw)) as image:
            if max(image.size) > max_dimension:
                raise BrandingAssetError(
                    f"The {kind} exceeds {max_dimension}px on its longest side."
                )
            buffer = io.BytesIO()
            image.convert("RGBA").save(buffer, format="PNG", optimize=True)
            return buffer.getvalue()
    except BrandingAssetError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BrandingAssetError(
            f"That file is not a readable image, so it was not stored as a "
            f"{kind}."
        ) from exc


def attach_asset(
    db: Session,
    *,
    organization: Organization,
    branding: TenantBranding,
    kind: str,
    raw: bytes,
    original_filename: str,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> UploadedFile:
    """Store a branding asset in the tenant's own regional prefix.

    The two lines that carry invariant 6 are `driver_for_organization` and
    `tenant_key`. Neither has a fallback: a tenant pinned to a region with no
    bucket raises rather than writing to the default bucket, and a key is
    always `{organization_id}/logos/{file_id}.png`.
    """
    if kind not in ASSET_KINDS:
        raise BrandingAssetError(f"Unknown branding asset kind {kind!r}.")
    if branding.organization_id != organization.id:
        raise CrossTenantAssetError("Branding asset not found.")

    normalised = _normalise_image(raw, kind=kind)

    # Raises ResidencyNotConfiguredError for a tenant pinned to a region with
    # no bucket. That refusal is the point — see residency_service.
    driver = residency_service.driver_for_organization(organization)

    file_id = uuid.uuid4()
    key = tenant_key(
        organization_id=organization.id,
        namespace=StorageNamespace.LOGOS,
        file_id=file_id,
        suffix=LOGO_SUFFIX,
    )
    driver.put(key, normalised, LOGO_MIME)

    record = UploadedFile(
        id=file_id,
        file_path=key,
        original_filename=(original_filename or f"{kind}.png")[:255],
        mime_type=LOGO_MIME,
        file_size=len(normalised),
        checksum_sha256=hashlib.sha256(normalised).hexdigest(),
        owner_id=actor_id,
        organization_id=organization.id,
        workspace_id=None,
    )
    db.add(record)
    db.flush([record])

    previous_id = (
        branding.logo_file_id if kind == ASSET_LOGO else branding.favicon_file_id
    )
    if kind == ASSET_LOGO:
        branding.logo_file_id = record.id
    else:
        branding.favicon_file_id = record.id
    branding.updated_by_user_id = actor_id
    db.flush([branding])

    if previous_id is not None:
        _retire_asset(db, organization=organization, file_id=previous_id)

    audit_service.record(
        db,
        organization_id=organization.id,
        actor_id=actor_id,
        resource_type=AuditResourceType.TENANT_BRANDING,
        resource_id=branding.id,
        action=AuditAction.BRANDING_UPDATED,
        details={
            "asset": kind,
            "uploaded_file_id": str(record.id),
            "size_bytes": len(normalised),
            "storage_key": key,
        },
        **(audit_context or {}),
    )
    return record


def _retire_asset(
    db: Session, *, organization: Organization, file_id: uuid.UUID
) -> None:
    """Soft-delete the row, then best-effort remove the object.

    Row first. If the object delete fails the row is still marked deleted and
    the object is garbage; if the object were deleted first and the flush
    failed, the row would point at bytes that no longer exist and every read
    would 404 on an asset the console still shows.
    """
    record = db.get(UploadedFile, file_id)
    if record is None or record.organization_id != organization.id:
        return
    record.deleted_at = utcnow()
    db.flush([record])
    try:
        residency_service.driver_for_organization(organization).delete(
            record.file_path
        )
    except Exception:  # noqa: BLE001 - orphaned bytes are not worth a 500
        logger.warning(
            "branding.asset_object_delete_failed",
            extra={"file_id": str(file_id), "file_path": record.file_path},
        )


def clear_asset(
    db: Session,
    *,
    organization: Organization,
    branding: TenantBranding,
    kind: str,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> TenantBranding:
    if kind not in ASSET_KINDS:
        raise BrandingAssetError(f"Unknown branding asset kind {kind!r}.")

    file_id = (
        branding.logo_file_id if kind == ASSET_LOGO else branding.favicon_file_id
    )
    if kind == ASSET_LOGO:
        branding.logo_file_id = None
    else:
        branding.favicon_file_id = None
    branding.updated_by_user_id = actor_id
    db.flush([branding])

    if file_id is not None:
        _retire_asset(db, organization=organization, file_id=file_id)

    audit_service.record(
        db,
        organization_id=organization.id,
        actor_id=actor_id,
        resource_type=AuditResourceType.TENANT_BRANDING,
        resource_id=branding.id,
        action=AuditAction.BRANDING_UPDATED,
        details={"asset": kind, "change": "cleared"},
        **(audit_context or {}),
    )
    return branding


# ---------------------------------------------------------------------------
# Sender domain
# ---------------------------------------------------------------------------


def sender_records_for(sender_domain: str) -> list[SenderDnsRecord]:
    """The records a tenant must publish, rendered for the console."""
    selector = "flowpilot"
    return [
        SenderDnsRecord(
            purpose="SPF",
            record_name=sender_domain,
            record_value="v=spf1 include:mail.flowpilot.ai ~all",
        ),
        SenderDnsRecord(
            purpose="DKIM",
            record_name=f"{selector}._domainkey.{sender_domain}",
            record_value="v=DKIM1; k=rsa; p=<public key issued on verification>",
        ),
        SenderDnsRecord(
            purpose="DMARC",
            record_name=f"_dmarc.{sender_domain}",
            record_value="v=DMARC1; p=none; rua=mailto:dmarc@flowpilot.ai",
        ),
    ]


def sender_status(branding: TenantBranding) -> SenderDomainStatusResponse:
    return SenderDomainStatusResponse(
        sender_domain=branding.sender_domain,
        sender_domain_status=branding.sender_domain_status,
        sender_domain_checked_at=branding.sender_domain_checked_at,
        sender_domain_last_error=branding.sender_domain_last_error,
        may_send_as_tenant=branding.may_send_as_tenant,
        degradation_reason=branding.sender_degradation_reason,
        required_records=(
            sender_records_for(branding.sender_domain)
            if branding.sender_domain
            else []
        ),
    )


def set_sender_domain(
    db: Session,
    *,
    branding: TenantBranding,
    sender_domain: Optional[str],
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
) -> TenantBranding:
    """Set or clear the custom From: domain.

    A new domain always lands PENDING, never VERIFIED. Mail keeps going out
    from the platform address until the records actually resolve —
    `ck_tenant_branding_sender_status_coherent` will not even let a row claim
    otherwise.
    """
    if sender_domain:
        branding.sender_domain = sender_domain
        branding.sender_domain_status = SENDER_STATUS_PENDING
        branding.sender_domain_last_error = None
        branding.sender_domain_checked_at = None
    else:
        branding.sender_domain = None
        branding.sender_domain_status = SENDER_STATUS_UNSET
        branding.sender_domain_last_error = None
        branding.sender_domain_checked_at = None

    branding.updated_by_user_id = actor_id
    db.flush([branding])

    audit_service.record(
        db,
        organization_id=branding.organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.TENANT_BRANDING,
        resource_id=branding.id,
        action=AuditAction.BRANDING_UPDATED,
        details={"sender_domain": sender_domain, "status": branding.sender_domain_status},
        **(audit_context or {}),
    )
    return branding


def verify_sender_domain(
    db: Session,
    *,
    branding: TenantBranding,
    actor_id: Optional[uuid.UUID] = None,
    audit_context: Optional[dict[str, Any]] = None,
    raise_on_failure: bool = True,
) -> SenderDomainStatusResponse:
    """Check SPF and DKIM, and settle the sender status.

    A previously VERIFIED domain that stops resolving moves to LAPSED, not to
    PENDING and not to UNSET. That is invariant 5: the console must be able to
    say "this broke" rather than "this is not set up", and the audit row is
    DISABLED rather than a new action because the platform has stopped sending
    as the tenant.

    A resolver outage leaves the status alone. Downgrading a working tenant to
    LAPSED because our resolver was unreachable would send their mail from the
    platform address for the duration of our incident.
    """
    if not branding.sender_domain:
        raise SenderDomainError(
            "No sender domain is configured for this organization."
        )

    now = utcnow()
    spf = dns_service.lookup_txt(branding.sender_domain)
    dkim = dns_service.lookup_txt(
        f"flowpilot._domainkey.{branding.sender_domain}"
    )

    if (spf.error and not spf.resolved) and (dkim.error and not dkim.resolved):
        logger.warning(
            "branding.sender_resolver_unavailable",
            extra={"sender_domain": branding.sender_domain},
        )
        return sender_status(branding)

    has_spf = any(
        record.strip().lower().startswith("v=spf1") for record in spf.records
    )
    has_dkim = any(
        "v=dkim1" in record.strip().lower() for record in dkim.records
    )

    branding.sender_domain_checked_at = now
    previous = branding.sender_domain_status

    if has_spf and has_dkim:
        branding.sender_domain_status = SENDER_STATUS_VERIFIED
        branding.sender_domain_last_error = None
    else:
        missing = [
            name
            for name, present in (("SPF", has_spf), ("DKIM", has_dkim))
            if not present
        ]
        branding.sender_domain_last_error = (
            f"Missing {' and '.join(missing)} record(s) on "
            f"{branding.sender_domain}."
        )
        branding.sender_domain_status = (
            SENDER_STATUS_LAPSED
            if previous == SENDER_STATUS_VERIFIED
            else SENDER_STATUS_PENDING
        )

    db.flush([branding])

    if previous == SENDER_STATUS_VERIFIED and branding.sender_domain_status == (
        SENDER_STATUS_LAPSED
    ):
        audit_service.record(
            db,
            organization_id=branding.organization_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.TENANT_BRANDING,
            resource_id=branding.id,
            action=AuditAction.DISABLED,
            outcome="DENIED",
            details={
                "sender_domain": branding.sender_domain,
                "reason": branding.sender_domain_last_error,
                "effect": "degraded_to_platform_sender",
            },
            **(audit_context or {}),
        )
        logger.error(
            "branding.sender_domain_lapsed",
            extra={
                "organization_id": str(branding.organization_id),
                "sender_domain": branding.sender_domain,
            },
        )

    status = sender_status(branding)
    for record in status.required_records:
        if record.purpose == "SPF":
            record.present = has_spf
        elif record.purpose == "DKIM":
            record.present = has_dkim

    if raise_on_failure and not branding.may_send_as_tenant:
        raise SenderDomainError(branding.sender_domain_last_error or
                                "The sender domain did not verify.")
    return status


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(branding: Optional[TenantBranding]) -> BrandingManifest:
    """The unauthenticated, host-resolved theme payload.

    Returns the platform default for a tenant with no row or with branding
    disabled. It never returns anything identifying: no organization id, no
    slug, no name beyond the brand string the tenant chose to display
    publicly. See the schema module for why that omission is the whole
    security argument for this endpoint.
    """
    if branding is None or not branding.has_visible_branding:
        return BrandingManifest.platform_default()

    tokens = {name: getattr(branding, name) for name in BRANDING_COLOR_TOKENS}
    return BrandingManifest(
        brand_name=branding.brand_name,
        color_scheme=branding.color_scheme,
        logo_url=LOGO_PUBLIC_PATH if branding.logo_file_id else None,
        favicon_url=FAVICON_PUBLIC_PATH if branding.favicon_file_id else None,
        support_email=branding.support_email,
        has_custom_branding=True,
        **tokens,
    )


__all__ = [
    "ASSET_FAVICON",
    "ASSET_KINDS",
    "ASSET_LOGO",
    "FAVICON_PUBLIC_PATH",
    "LOGO_PUBLIC_PATH",
    "attach_asset",
    "build_manifest",
    "clear_asset",
    "get_branding",
    "get_or_create_branding",
    "read_asset_bytes",
    "resolve_asset",
    "sender_records_for",
    "sender_status",
    "set_sender_domain",
    "update_branding",
    "verify_sender_domain",
]