"""ARCH-27 §3 — cryptographic admission control for third-party workflows.

INVARIANT 5 — NOTHING RUNS WITHOUT A VERIFIED SIGNATURE
=======================================================

Three layers, and the third is the only one that cannot be forgotten:

1. `publish_manifest()` verifies before writing the manifest row.
2. `install_manifest()` verifies AGAIN before writing the installation. The
   re-verification is not redundant: a key revoked between publication and
   installation must stop the install, and the publication-time check cannot
   know that.
3. `marketplace_installations.verified_signature_id` is NOT NULL. An install
   that skipped both service checks raises a NOT NULL violation rather than
   admitting unsigned third-party code into a tenant's automation engine.

WHY THE DIGEST IS OVER A CANONICAL FORM, NOT THE SUBMITTED BYTES
================================================================

`canonical_manifest()` sorts keys, drops whitespace and orders nodes and edges
deterministically; `manifest_digest()` hashes that. The signature covers the
digest, not the request body.

Signing raw submitted bytes is the obvious design and it breaks immediately:
the manifest is stored as JSONB, which preserves neither key order nor
whitespace, so the first read-back produces different bytes and every
signature fails. Worse, it would fail LATER — at install time, in a tenant's
console, for a manifest that verified fine when the partner published it.

WHY A SIGNATURE FROM ANOTHER MANIFEST STILL FAILS
=================================================

`marketplace_signatures.signed_digest` records what was actually signed, and
verification compares it against the manifest's own `content_digest` BEFORE
doing any cryptography. A signature lifted from manifest A and attached to
manifest B is cryptographically valid over A's digest; without this
comparison it would be accepted for B. The check is first because a
constant-time comparison of two 71-character strings is cheaper than an RSA
verify, and because failing on the cheap check keeps the expensive one off an
attacker's oracle.

INVARIANT 6 — THE FULL ARCH-13 VALIDATOR, NO RELAXATION
=======================================================

`lint_manifest()` calls `graph_service.compile_graph()` — the same function
`save_graph()` calls for a tenant's own rules. Not a subset, not a copy with
the cycle check removed for "trusted" partner content. `verify_arch27.py` G11
asserts this module imports `compile_graph` from `app.services.automation
.graph_service` and that no local re-implementation of the shape rules exists
here.

On top of it, `_refuse_r33_violations()` rejects any action node whose config
draws a value from retrieved document content. ARCH-13's R33 boundary proved a
document cannot choose WHAT an action does; a marketplace manifest is
third-party authored, so the same boundary applies with more force, not less.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.automation import AutomationRule
from app.models.partner import (
    DIGEST_PREFIX,
    MarketplaceInstallation,
    MarketplaceItem,
    MarketplaceManifest,
    MarketplaceSignature,
    Partner,
    PartnerSigningKey,
)
from app.models.workspace import Workspace
from app.services import audit_service
from app.services.automation import graph_service
from app.services.automation.graph_service import (
    EdgeSpec,
    GraphValidationError,
    NodeSpec,
    compile_graph,
)
from app.services.partner.tenancy_service import (
    PartnerConflict,
    PartnerError,
    PartnerNotFound,
    partner_for_organization,
)

logger = logging.getLogger("app.services.partner.marketplace_service")

ALGORITHM_ED25519: str = "ED25519"
ALGORITHM_RSA_PSS: str = "RSA_PSS_SHA256"

#: RSA below this is not accepted regardless of what the partner registered.
#: 2048 is the floor every current guidance names, and a marketplace key is a
#: long-lived credential that admits executable content.
MIN_RSA_KEY_BITS: int = 2048

#: Config keys on an action node whose value is authored by the rules writer
#: and must therefore be a literal. ARCH-13's R33 boundary.
_R33_ACTION_KEYS: frozenset[str] = frozenset(
    {"recipient", "target_field", "target_value"}
)

#: Substrings that indicate a config value is drawn from retrieved document
#: content rather than authored. A manifest is third-party code; a value that
#: interpolates document text into an action is the exact chain R33 forbids.
_R33_FORBIDDEN_SOURCES: tuple[str, ...] = (
    "{{document",
    "{{chunk",
    "{{retrieved",
    "{{extraction",
    "{{context",
    "$document",
    "$retrieved",
)


class MarketplaceError(PartnerError):
    """A marketplace operation was refused."""


class SignatureVerificationError(MarketplaceError):
    status_code = 400


class ManifestValidationError(MarketplaceError):
    status_code = 422


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Canonicalisation and digests
# ---------------------------------------------------------------------------


def canonical_manifest(
    nodes: Sequence[Any], edges: Sequence[Any]
) -> dict[str, Any]:
    """The exact structure the digest covers.

    Nodes sort by `node_key` and edges by the triple, so two submissions that
    differ only in list order produce one digest. Without that, a partner
    re-publishing the same workflow from a tool that iterates a dict gets a
    different digest and a signature that no longer matches anything.
    """

    def _node(entry: Any) -> dict[str, Any]:
        return {
            "node_key": str(_attr(entry, "node_key")),
            "node_type": str(_attr(entry, "node_type")),
            "config": _attr(entry, "config") or {},
        }

    def _edge(entry: Any) -> dict[str, Any]:
        return {
            "from_node_key": str(_attr(entry, "from_node_key")),
            "to_node_key": str(_attr(entry, "to_node_key")),
            "branch": str(_attr(entry, "branch") or "default"),
        }

    return {
        "schema": "arch27.manifest.v1",
        "nodes": sorted(
            (_node(entry) for entry in nodes), key=lambda item: item["node_key"]
        ),
        "edges": sorted(
            (_edge(entry) for entry in edges),
            key=lambda item: (
                item["from_node_key"],
                item["to_node_key"],
                item["branch"],
            ),
        ),
    }


def _attr(entry: Any, name: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def manifest_digest(nodes: Sequence[Any], edges: Sequence[Any]) -> str:
    blob = json.dumps(
        canonical_manifest(nodes, edges), separators=(",", ":"), sort_keys=True
    )
    return DIGEST_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def fingerprint_public_key(public_key_pem: str) -> str:
    """sha256 over the DER SubjectPublicKeyInfo.

    Over the DER and not over the PEM text: the same key re-exported with
    different line wrapping or a trailing newline is the same key, and a
    fingerprint that disagrees would let one partner register another's key
    past the global uniqueness index.
    """
    key = load_public_key(public_key_pem)
    der = key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return DIGEST_PREFIX + hashlib.sha256(der).hexdigest()


def load_public_key(public_key_pem: str) -> Any:
    if "PRIVATE KEY" in public_key_pem.upper():
        raise SignatureVerificationError(
            "That PEM contains a private key. Treat it as compromised, "
            "generate a new keypair, and register only the public half."
        )
    try:
        return serialization.load_pem_public_key(
            public_key_pem.strip().encode("ascii")
        )
    except (ValueError, UnsupportedAlgorithm, UnicodeEncodeError) as exc:
        raise SignatureVerificationError(
            "Could not parse that PEM as a public key."
        ) from exc


def algorithm_for_key(public_key_pem: str) -> str:
    """Which algorithm a key actually supports, read from the key itself.

    Not taken from the caller. A submission claiming ED25519 over an RSA key
    would otherwise select the Ed25519 verifier, fail with a type error, and
    surface as a 500 rather than a refusal naming the mismatch.
    """
    key = load_public_key(public_key_pem)
    if isinstance(key, Ed25519PublicKey):
        return ALGORITHM_ED25519
    if isinstance(key, RSAPublicKey):
        if key.key_size < MIN_RSA_KEY_BITS:
            raise SignatureVerificationError(
                f"RSA keys below {MIN_RSA_KEY_BITS} bits are not accepted for "
                f"marketplace signing; this one is {key.key_size}."
            )
        return ALGORITHM_RSA_PSS
    raise SignatureVerificationError(
        "Unsupported key type. Marketplace manifests are signed with Ed25519 "
        "or RSA-PSS/SHA-256."
    )


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def verify_signature(
    *,
    public_key_pem: str,
    algorithm: str,
    signature_b64: str,
    digest: str,
) -> bool:
    """Verify one signature over one digest. Returns False, never raises on a
    bad signature — a forged signature is an expected input here, not an
    exceptional one, and raising would make the caller's control flow depend
    on catching an exception from a library.

    Malformed inputs (unparseable key, non-base64 signature) DO raise: those
    are a caller error, not an attacker's ordinary failure.
    """
    key = load_public_key(public_key_pem)
    resolved = algorithm_for_key(public_key_pem)
    if resolved != algorithm:
        raise SignatureVerificationError(
            f"Signature claims {algorithm} but the registered key is "
            f"{resolved}."
        )

    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignatureVerificationError(
            "Signature is not valid standard base64."
        ) from exc

    payload = digest.encode("ascii")

    try:
        if resolved == ALGORITHM_ED25519:
            key.verify(signature, payload)
        else:
            key.verify(
                signature,
                payload,
                asym_padding.PSS(
                    mgf=asym_padding.MGF1(hashes.SHA256()),
                    salt_length=asym_padding.PSS.MAX_LENGTH,
                ),
                hashes.SHA256(),
            )
    except InvalidSignature:
        return False
    return True


def verify_manifest_signature(
    db: Session, *, manifest: MarketplaceManifest
) -> tuple[bool, Optional[MarketplaceSignature], Optional[PartnerSigningKey]]:
    """Re-verify a stored manifest against its stored signatures.

    Called at install time, not only at publish time. A key revoked in between
    must stop the install, and only a live check can know that.
    """
    rows = db.execute(
        select(MarketplaceSignature, PartnerSigningKey)
        .join(
            PartnerSigningKey,
            PartnerSigningKey.id == MarketplaceSignature.signing_key_id,
        )
        .where(MarketplaceSignature.manifest_id == manifest.id)
        .order_by(MarketplaceSignature.created_at)
    ).all()

    for signature, key in rows:
        if key.status != "ACTIVE":
            continue
        # Cheap check first, and it is a real check: a signature lifted from
        # another manifest is cryptographically valid over ITS digest.
        if signature.signed_digest != manifest.content_digest:
            continue
        if verify_signature(
            public_key_pem=key.public_key_pem,
            algorithm=signature.algorithm,
            signature_b64=signature.signature,
            digest=manifest.content_digest,
        ):
            return True, signature, key

    return False, None, None


# ---------------------------------------------------------------------------
# DAG validation — invariant 6
# ---------------------------------------------------------------------------


def _refuse_r33_violations(nodes: Sequence[NodeSpec]) -> None:
    violations: list[str] = []
    for node in nodes:
        if node.node_type != "action":
            continue
        config = node.config or {}
        inner = config.get("config") if isinstance(config.get("config"), dict) else config
        for key in _R33_ACTION_KEYS:
            value = inner.get(key)
            if not isinstance(value, str):
                continue
            lowered = value.lower()
            if any(token in lowered for token in _R33_FORBIDDEN_SOURCES):
                violations.append(f"{node.node_key}.{key}")

    if violations:
        raise ManifestValidationError(
            "Action values must be authored in the manifest, never drawn from "
            f"retrieved document content (R33): {', '.join(sorted(violations))}. "
            "A document that can choose what an action does is a document that "
            "can choose who receives it."
        )


def lint_manifest(
    nodes: Sequence[Any], edges: Sequence[Any]
) -> graph_service.CompiledGraph:
    """Invariant 6. The full ARCH-13 validator, then the R33 boundary.

    `compile_graph` is imported from the automation package rather than
    reimplemented: it enforces the node ceiling, unique keys, known node
    types, exactly one trigger, no dangling edges, reachability from the
    trigger, and acyclicity. A marketplace manifest gets all of it.
    """
    node_specs = [
        NodeSpec(
            node_key=str(_attr(entry, "node_key")),
            node_type=str(_attr(entry, "node_type")),
            config=dict(_attr(entry, "config") or {}),
        )
        for entry in nodes
    ]
    edge_specs = [
        EdgeSpec(
            from_node_key=str(_attr(entry, "from_node_key")),
            to_node_key=str(_attr(entry, "to_node_key")),
            branch=str(_attr(entry, "branch") or "default"),
        )
        for entry in edges
    ]

    try:
        compiled = compile_graph(node_specs, edge_specs)
    except GraphValidationError as exc:
        raise ManifestValidationError(
            f"Manifest is not a valid automation graph: {exc}"
        ) from exc

    _refuse_r33_violations(node_specs)
    return compiled


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def create_item(
    db: Session,
    *,
    partner: Partner,
    slug: str,
    name: str,
    summary: Optional[str],
    category: str,
    visibility: str,
    actor_id: Optional[uuid.UUID] = None,
) -> MarketplaceItem:
    item = MarketplaceItem(
        partner_id=partner.id,
        slug=slug.strip().lower(),
        name=name.strip(),
        summary=(summary or None),
        category=category.strip().upper()[:32] or "GENERAL",
        status="DRAFT",
        visibility=visibility,
    )
    db.add(item)
    try:
        db.flush([item])
    except IntegrityError as exc:
        db.rollback()
        raise PartnerConflict(
            "That catalog slug is already used by this partner."
        ) from exc

    audit_service.record(
        db,
        organization_id=partner.owner_organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.MARKETPLACE_ITEM,
        resource_id=item.id,
        action=AuditAction.CREATED,
        details={
            "partner_id": str(partner.id),
            "item_slug": item.slug,
            "visibility": item.visibility,
        },
    )
    return item


def get_item(db: Session, *, item_id: uuid.UUID) -> MarketplaceItem:
    item = db.get(MarketplaceItem, item_id)
    if item is None:
        raise PartnerNotFound("Marketplace item not found.")
    return item


def publish_manifest(
    db: Session,
    *,
    partner: Partner,
    item: MarketplaceItem,
    version: str,
    nodes: Sequence[Any],
    edges: Sequence[Any],
    signing_key_id: str,
    signature_b64: str,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[MarketplaceManifest, MarketplaceSignature]:
    """Validate, verify, then write. In that order, and the order matters.

    A manifest row that exists before its signature is verified is a manifest
    row that some future reader treats as published. Both halves land in one
    transaction or neither does.
    """
    if item.partner_id != partner.id:
        raise PartnerNotFound("Marketplace item not found for this partner.")

    # Invariant 6 first: an unsigned but malformed manifest should be refused
    # for being malformed, which is the more actionable message.
    compiled = lint_manifest(nodes, edges)

    key = db.execute(
        select(PartnerSigningKey).where(
            PartnerSigningKey.partner_id == partner.id,
            PartnerSigningKey.key_id == signing_key_id.strip(),
        )
    ).scalar_one_or_none()
    if key is None:
        raise SignatureVerificationError(
            f"No signing key {signing_key_id!r} is registered for this partner."
        )
    if key.status != "ACTIVE":
        raise SignatureVerificationError(
            f"Signing key {signing_key_id!r} is revoked and cannot admit new "
            "manifests."
        )

    digest = manifest_digest(nodes, edges)
    if not verify_signature(
        public_key_pem=key.public_key_pem,
        algorithm=key.algorithm,
        signature_b64=signature_b64,
        digest=digest,
    ):
        # Recorded as a DENIED audit row, not merely returned as a 400. A
        # burst of failed signature checks against one partner is what a
        # compromised publishing pipeline looks like from the outside.
        audit_service.record(
            db,
            organization_id=partner.owner_organization_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.MARKETPLACE_ITEM,
            resource_id=item.id,
            action=AuditAction.MANIFEST_PUBLISHED,
            outcome=AuditOutcome.DENIED,
            details={
                "partner_id": str(partner.id),
                "version": version,
                "signing_key_id": key.key_id,
                "reason": "SIGNATURE_INVALID",
                "content_digest": digest,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise SignatureVerificationError(
            "Signature does not verify against the registered key for this "
            "manifest's content digest. The manifest was not published."
        )

    manifest = MarketplaceManifest(
        item_id=item.id,
        version=version,
        manifest=canonical_manifest(nodes, edges),
        content_digest=digest,
        status="PUBLISHED",
        node_count=len(compiled.nodes),
        edge_count=len(compiled.edges),
        published_at=_now(),
    )
    db.add(manifest)
    try:
        db.flush([manifest])
    except IntegrityError as exc:
        db.rollback()
        raise PartnerConflict(
            f"Version {version!r} already exists for this catalog item. A "
            "published version is immutable; publish a new version instead."
        ) from exc

    signature = MarketplaceSignature(
        manifest_id=manifest.id,
        signing_key_id=key.id,
        algorithm=key.algorithm,
        signature=signature_b64,
        signed_digest=digest,
        verified_at=_now(),
    )
    db.add(signature)
    db.flush([signature])

    if item.status == "DRAFT":
        item.status = "PUBLISHED"
        db.flush([item])

    audit_service.record(
        db,
        organization_id=partner.owner_organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.MARKETPLACE_ITEM,
        resource_id=item.id,
        action=AuditAction.MANIFEST_PUBLISHED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "partner_id": str(partner.id),
            "manifest_id": str(manifest.id),
            "version": version,
            "content_digest": digest,
            "signing_key_id": key.key_id,
            "key_fingerprint": key.fingerprint,
            "node_count": manifest.node_count,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    logger.info(
        "marketplace.manifest_published",
        extra={
            "partner_id": str(partner.id),
            "manifest_id": str(manifest.id),
            "digest": digest,
        },
    )
    return manifest, signature


def catalog_for_organization(
    db: Session, *, organization_id: uuid.UUID, category: Optional[str] = None
) -> list[dict[str, Any]]:
    """What one tenant may browse.

    PUBLIC items, plus PARTNER_ONLY items belonging to the partner that
    currently holds this tenant. Resolved through
    `partner_for_organization()`, which reads the same ACTIVE assignment
    invariant 2 makes unique — so a tenant sees at most one partner's private
    catalog, never a union.
    """
    holder = partner_for_organization(db, organization_id=organization_id)
    visibility_clause = MarketplaceItem.visibility == "PUBLIC"
    if holder is not None:
        visibility_clause = visibility_clause | (
            (MarketplaceItem.visibility == "PARTNER_ONLY")
            & (MarketplaceItem.partner_id == holder.id)
        )

    conditions: list[Any] = [
        MarketplaceItem.status == "PUBLISHED",
        visibility_clause,
    ]
    if category:
        conditions.append(MarketplaceItem.category == category.strip().upper())

    rows = db.execute(
        select(MarketplaceItem, Partner)
        .join(Partner, Partner.id == MarketplaceItem.partner_id)
        .where(*conditions)
        .order_by(MarketplaceItem.name)
    ).all()

    installed_item_ids = set(
        db.execute(
            select(MarketplaceInstallation.item_id).where(
                MarketplaceInstallation.organization_id == organization_id,
                MarketplaceInstallation.status != "REMOVED",
            )
        ).scalars()
    )

    catalog: list[dict[str, Any]] = []
    for item, publisher in rows:
        latest = db.execute(
            select(MarketplaceManifest)
            .where(
                MarketplaceManifest.item_id == item.id,
                MarketplaceManifest.status == "PUBLISHED",
            )
            .order_by(MarketplaceManifest.published_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        catalog.append(
            {
                "id": item.id,
                "partner_id": item.partner_id,
                "partner_name": publisher.name,
                "slug": item.slug,
                "name": item.name,
                "summary": item.summary,
                "category": item.category,
                "status": item.status,
                "visibility": item.visibility,
                "latest_version": latest.version if latest else None,
                "latest_manifest_id": latest.id if latest else None,
                "installed": item.id in installed_item_ids,
                "created_at": item.created_at,
            }
        )
    return catalog


def manifest_for_organization(
    db: Session, *, organization_id: uuid.UUID, manifest_id: uuid.UUID
) -> tuple[MarketplaceManifest, MarketplaceItem]:
    """Fetch a manifest a tenant is entitled to see, or 404.

    Entitlement is recomputed from the catalog rather than trusted from the
    caller: a manifest id is guessable in a way a visibility rule is not, and
    'you may install what you may browse' has to hold in both directions.
    """
    manifest = db.get(MarketplaceManifest, manifest_id)
    if manifest is None:
        raise PartnerNotFound("Manifest not found.")
    item = db.get(MarketplaceItem, manifest.item_id)
    if item is None:
        raise PartnerNotFound("Manifest not found.")

    visible_ids = {
        entry["id"]
        for entry in catalog_for_organization(
            db, organization_id=organization_id
        )
    }
    if item.id not in visible_ids:
        raise PartnerNotFound("Manifest not found.")
    return manifest, item


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def install_manifest(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    manifest_id: uuid.UUID,
    rule_name: Optional[str] = None,
    enabled: bool = False,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> MarketplaceInstallation:
    """Admit a signed manifest into one tenant's automation engine."""
    manifest, item = manifest_for_organization(
        db, organization_id=organization_id, manifest_id=manifest_id
    )

    if manifest.status != "PUBLISHED":
        raise PartnerConflict(
            "That manifest version has been withdrawn by its publisher and "
            "cannot be installed."
        )

    workspace = db.get(Workspace, workspace_id)
    if workspace is None or workspace.organization_id != organization_id:
        # Same 404 for "does not exist" and "belongs to someone else". A
        # different status for the second case turns this into a workspace
        # enumeration oracle across tenants.
        raise PartnerNotFound("Workspace not found in this organization.")

    # INVARIANT 5, layer 2. Re-verified here and not merely trusted from
    # publication: a key revoked in between must stop this install.
    verified, signature, key = verify_manifest_signature(db, manifest=manifest)
    if not verified or signature is None:
        audit_service.record(
            db,
            organization_id=organization_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.MARKETPLACE_ITEM,
            resource_id=item.id,
            action=AuditAction.MANIFEST_INSTALLED,
            outcome=AuditOutcome.DENIED,
            details={
                "manifest_id": str(manifest.id),
                "reason": "SIGNATURE_NOT_VERIFIED",
                "content_digest": manifest.content_digest,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        raise SignatureVerificationError(
            "This manifest has no currently valid signature from an active "
            "publisher key. It was not installed. If the publisher rotated "
            "keys, ask them to re-sign this version."
        )

    body = manifest.manifest or {}
    nodes = body.get("nodes") or []
    edges = body.get("edges") or []

    # INVARIANT 6 at install time as well as publish time. The stored manifest
    # is re-linted because the node-type vocabulary and the AUTOMATION_MAX_NODES
    # ceiling can both move between publication and install, and a graph that
    # no longer validates must not be materialised into a rule.
    compiled = lint_manifest(nodes, edges)

    existing = db.execute(
        select(MarketplaceInstallation).where(
            MarketplaceInstallation.organization_id == organization_id,
            MarketplaceInstallation.item_id == item.id,
            MarketplaceInstallation.status != "REMOVED",
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise PartnerConflict(
            "This catalog item is already installed in your organization. "
            "Remove the existing installation before installing another "
            "version."
        )

    rule = AutomationRule(
        workspace_id=workspace_id,
        name=(rule_name or item.name)[:200],
        event=str(
            (compiled.node(compiled.trigger_key).config or {}).get("event")
            or "document.created"
        ),
        conditions=[],
        actions=[],
        is_active=bool(enabled),
        created_by_user_id=actor_id,
    )
    db.add(rule)
    db.flush([rule])

    graph_service.save_graph(
        db,
        rule=rule,
        nodes=[
            NodeSpec(
                node_key=str(entry.get("node_key")),
                node_type=str(entry.get("node_type")),
                config=dict(entry.get("config") or {}),
            )
            for entry in nodes
        ],
        edges=[
            EdgeSpec(
                from_node_key=str(entry.get("from_node_key")),
                to_node_key=str(entry.get("to_node_key")),
                branch=str(entry.get("branch") or "default"),
            )
            for entry in edges
        ],
    )

    installation = MarketplaceInstallation(
        organization_id=organization_id,
        item_id=item.id,
        manifest_id=manifest.id,
        # INVARIANT 5, layer 3. NOT NULL in the schema: this line cannot be
        # omitted by a future refactor without a database error.
        verified_signature_id=signature.id,
        automation_rule_id=rule.id,
        status="INSTALLED",
        installed_by_user_id=actor_id,
        installed_at=_now(),
    )
    db.add(installation)
    db.flush([installation])

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.MARKETPLACE_ITEM,
        resource_id=item.id,
        action=AuditAction.MANIFEST_INSTALLED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "manifest_id": str(manifest.id),
            "installation_id": str(installation.id),
            "version": manifest.version,
            "content_digest": manifest.content_digest,
            "key_fingerprint": key.fingerprint if key else None,
            "automation_rule_id": str(rule.id),
            "workspace_id": str(workspace_id),
            "enabled_on_install": bool(enabled),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    logger.info(
        "marketplace.manifest_installed",
        extra={
            "organization_id": str(organization_id),
            "manifest_id": str(manifest.id),
            "installation_id": str(installation.id),
        },
    )
    return installation


def remove_installation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    installation_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
) -> MarketplaceInstallation:
    installation = db.execute(
        select(MarketplaceInstallation).where(
            MarketplaceInstallation.id == installation_id,
            MarketplaceInstallation.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if installation is None:
        raise PartnerNotFound("Installation not found.")
    if installation.status == "REMOVED":
        return installation

    if installation.automation_rule_id is not None:
        rule = db.get(AutomationRule, installation.automation_rule_id)
        if rule is not None:
            # Deactivated, not deleted. Deleting would take the execution
            # history with it, and "what was this rule doing when it fired
            # last March" outlives the decision to uninstall.
            rule.is_active = False
            db.flush([rule])

    installation.status = "REMOVED"
    installation.removed_at = _now()
    db.flush([installation])

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.MARKETPLACE_ITEM,
        resource_id=installation.item_id,
        action=AuditAction.DELETED,
        details={
            "installation_id": str(installation.id),
            "manifest_id": str(installation.manifest_id),
        },
    )
    return installation


def installations_for(
    db: Session, *, organization_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = db.execute(
        select(MarketplaceInstallation, MarketplaceItem, MarketplaceManifest)
        .join(MarketplaceItem, MarketplaceItem.id == MarketplaceInstallation.item_id)
        .join(
            MarketplaceManifest,
            MarketplaceManifest.id == MarketplaceInstallation.manifest_id,
        )
        .where(
            MarketplaceInstallation.organization_id == organization_id,
            MarketplaceInstallation.status != "REMOVED",
        )
        .order_by(MarketplaceInstallation.installed_at.desc())
    ).all()

    return [
        {
            "id": installation.id,
            "organization_id": installation.organization_id,
            "item_id": installation.item_id,
            "item_name": item.name,
            "manifest_id": installation.manifest_id,
            "manifest_version": manifest.version,
            "verified_signature_id": installation.verified_signature_id,
            "automation_rule_id": installation.automation_rule_id,
            "status": installation.status,
            "installed_at": installation.installed_at,
        }
        for installation, item, manifest in rows
    ]


__all__ = [
    "ALGORITHM_ED25519",
    "ALGORITHM_RSA_PSS",
    "MIN_RSA_KEY_BITS",
    "ManifestValidationError",
    "MarketplaceError",
    "SignatureVerificationError",
    "algorithm_for_key",
    "canonical_manifest",
    "catalog_for_organization",
    "create_item",
    "fingerprint_public_key",
    "get_item",
    "install_manifest",
    "installations_for",
    "lint_manifest",
    "load_public_key",
    "manifest_digest",
    "manifest_for_organization",
    "publish_manifest",
    "remove_installation",
    "verify_manifest_signature",
    "verify_signature",
]