"""ARCH-27 §1, §2, §3 — the partner-tier API.

    GET    /partners                                       my memberships
    POST   /partners                                       create      [platform]
    GET    /partners/{pid}                                 detail      [member]
    PATCH  /partners/{pid}                                 update      [P-OWNER]

    GET    /partners/{pid}/members                         list        [member]
    POST   /partners/{pid}/members                         add         [P-OWNER]
    DELETE /partners/{pid}/members/{user_id}               remove      [P-OWNER]

    GET    /partners/{pid}/book                            book        [member]
    POST   /partners/{pid}/book                            assign      [P-ADMIN]
    DELETE /partners/{pid}/book/{organization_id}          release     [P-ADMIN]

    GET    /partners/{pid}/signing-keys                    list        [member]
    POST   /partners/{pid}/signing-keys                    register    [P-OWNER]
    POST   /partners/{pid}/signing-keys/{key_id}/revoke    revoke      [P-OWNER]

    GET    /partners/{pid}/agreements                      list        [member]
    POST   /partners/{pid}/agreements                      create      [platform]

    GET    /partners/{pid}/payouts                         periods     [member]
    POST   /partners/{pid}/payouts                         compute     [P-ADMIN]
    GET    /partners/{pid}/payouts/{period_id}             statement   [member]
    POST   /partners/{pid}/payouts/{period_id}/seal        seal        [platform]
    POST   /partners/{pid}/payouts/{period_id}/paid        mark paid   [platform]

    GET    /partners/{pid}/economics                       summary     [member]

    GET    /partners/{pid}/catalog                         items       [member]
    POST   /partners/{pid}/catalog                         create      [P-ADMIN]
    POST   /partners/{pid}/catalog/{item_id}/manifests     publish     [P-ADMIN]
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, require_superadmin
from app.core.client_ip import client_ip
from app.models.partner import (
    MarketplaceItem,
    MarketplaceManifest,
    Partner,
    PartnerPayoutPeriod,
    PartnerRevShareAgreement,
)
from app.models.user import User
from app.schemas.partner import (
    BookOfBusinessEntry,
    ManifestResponse,
    ManifestSubmission,
    MarketplaceItemCreate,
    MarketplaceItemResponse,
    OrganizationAssignmentCreate,
    PartnerCreate,
    PartnerEconomicsSummary,
    PartnerMemberCreate,
    PartnerMemberResponse,
    PartnerResponse,
    PartnerUpdate,
    PayoutPeriodCompute,
    PayoutPeriodMarkPaid,
    PayoutPeriodResponse,
    PayoutPeriodSeal,
    PayoutStatementResponse,
    RevShareAgreementCreate,
    RevShareAgreementResponse,
    RevShareLedgerLine,
    SigningKeyCreate,
    SigningKeyResponse,
    SigningKeyRevoke,
)
from app.services.partner import marketplace_service, rev_share_service, tenancy_service
from app.services.partner.tenancy_service import (
    ROLES_MANAGING_BOOK,
    ROLES_MANAGING_CATALOG,
    ROLES_MANAGING_MEMBERS,
    ROLES_READING,
    PartnerError,
)

logger = logging.getLogger("app.api.v1.partner")

router = APIRouter(prefix="/partners", tags=["Partner Portal"])


def _http(exc: PartnerError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


def _partner_ctx(
    db: Session,
    *,
    partner_id: uuid.UUID,
    user: User,
    allowed_roles: frozenset[str],
) -> Partner:
    try:
        tenancy_service.require_membership(
            db,
            partner_id=partner_id,
            user_id=user.id,
            allowed_roles=allowed_roles,
        )
        return tenancy_service.get_partner(db, partner_id=partner_id)
    except PartnerError as exc:
        raise _http(exc) from exc


# ---------------------------------------------------------------------------
# Partner lifecycle
# ---------------------------------------------------------------------------


@router.get("", response_model=list[PartnerResponse])
def list_my_partners(
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> list[Partner]:
    memberships = tenancy_service.memberships_for_user(db, user_id=current_user.id)
    if not memberships:
        return []
    partner_ids = [membership.partner_id for membership in memberships]
    return list(
        db.execute(
            select(Partner).where(Partner.id.in_(partner_ids)).order_by(Partner.name)
        )
        .scalars()
        .all()
    )


@router.post("", response_model=PartnerResponse, status_code=status.HTTP_201_CREATED)
def create_partner(
    payload: PartnerCreate,
    request: Request,
    operator: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> Partner:
    try:
        partner = tenancy_service.create_partner(
            db,
            slug=payload.slug,
            name=payload.name,
            owner_organization_id=payload.owner_organization_id,
            billing_email=payload.billing_email,
            notes=payload.notes,
            actor_id=operator.id,
            ip_address=client_ip(request),
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    db.refresh(partner)
    return partner


@router.get("/{partner_id}", response_model=PartnerResponse)
def get_partner(
    partner_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> Partner:
    return _partner_ctx(
        db, partner_id=partner_id, user=current_user, allowed_roles=ROLES_READING
    )


@router.patch("/{partner_id}", response_model=PartnerResponse)
def update_partner(
    partner_id: uuid.UUID,
    payload: PartnerUpdate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> Partner:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_MEMBERS,
    )
    data = payload.model_dump(exclude_unset=True)
    for field_name, value in data.items():
        setattr(partner, field_name, value)
    db.commit()
    db.refresh(partner)
    return partner


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{partner_id}/members", response_model=list[PartnerMemberResponse])
def list_members(
    partner_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> list[Any]:
    _partner_ctx(
        db, partner_id=partner_id, user=current_user, allowed_roles=ROLES_READING
    )
    from app.models.partner import PartnerMember

    return list(
        db.execute(
            select(PartnerMember)
            .where(PartnerMember.partner_id == partner_id)
            .order_by(PartnerMember.created_at)
        )
        .scalars()
        .all()
    )


@router.post(
    "/{partner_id}/members",
    response_model=PartnerMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_member(
    partner_id: uuid.UUID,
    payload: PartnerMemberCreate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> Any:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_MEMBERS,
    )
    if db.get(User, payload.user_id) is None:
        raise HTTPException(status_code=404, detail="User not found.")
    try:
        member = tenancy_service.add_member(
            db,
            partner=partner,
            user_id=payload.user_id,
            role=payload.role,
            actor_id=current_user.id,
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    db.refresh(member)
    return member


@router.delete(
    "/{partner_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def remove_member(
    partner_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> Response:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_MEMBERS,
    )
    try:
        tenancy_service.remove_member(
            db, partner=partner, user_id=user_id, actor_id=current_user.id
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Book of business
# ---------------------------------------------------------------------------


@router.get("/{partner_id}/book", response_model=list[BookOfBusinessEntry])
def get_book(
    partner_id: uuid.UUID,
    current_user: CurrentUser,
    include_ended: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _partner_ctx(
        db, partner_id=partner_id, user=current_user, allowed_roles=ROLES_READING
    )
    return tenancy_service.book_entries(
        db, partner_id=partner_id, include_ended=include_ended
    )


@router.post(
    "/{partner_id}/book",
    response_model=BookOfBusinessEntry,
    status_code=status.HTTP_201_CREATED,
)
def assign_organization(
    partner_id: uuid.UUID,
    payload: OrganizationAssignmentCreate,
    current_user: CurrentUser,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_BOOK,
    )
    try:
        tenancy_service.assign_organization(
            db,
            partner=partner,
            organization_id=payload.organization_id,
            effective_from=payload.effective_from,
            actor_id=current_user.id,
            ip_address=client_ip(request),
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()

    for entry in tenancy_service.book_entries(db, partner_id=partner_id):
        if entry["organization_id"] == payload.organization_id:
            return entry
    raise HTTPException(status_code=500, detail="Assignment did not persist.")


@router.delete(
    "/{partner_id}/book/{organization_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def release_organization(
    partner_id: uuid.UUID,
    organization_id: uuid.UUID,
    current_user: CurrentUser,
    request: Request,
    db: Session = Depends(get_db),
) -> Response:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_BOOK,
    )
    try:
        tenancy_service.release_organization(
            db,
            partner=partner,
            organization_id=organization_id,
            actor_id=current_user.id,
            ip_address=client_ip(request),
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Signing keys
# ---------------------------------------------------------------------------


@router.get("/{partner_id}/signing-keys", response_model=list[SigningKeyResponse])
def list_signing_keys(
    partner_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> list[Any]:
    _partner_ctx(
        db, partner_id=partner_id, user=current_user, allowed_roles=ROLES_READING
    )
    return tenancy_service.signing_keys(db, partner_id=partner_id)


@router.post(
    "/{partner_id}/signing-keys",
    response_model=SigningKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_signing_key(
    partner_id: uuid.UUID,
    payload: SigningKeyCreate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> Any:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_MEMBERS,
    )
    try:
        resolved = marketplace_service.algorithm_for_key(payload.public_key_pem)
        if resolved != payload.algorithm:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"You declared {payload.algorithm} but that PEM is a "
                    f"{resolved} key."
                ),
            )
        fingerprint = marketplace_service.fingerprint_public_key(
            payload.public_key_pem
        )
        key = tenancy_service.register_signing_key(
            db,
            partner=partner,
            key_id=payload.key_id,
            algorithm=resolved,
            public_key_pem=payload.public_key_pem,
            fingerprint=fingerprint,
            actor_id=current_user.id,
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    db.refresh(key)
    return key


@router.post(
    "/{partner_id}/signing-keys/{key_id}/revoke",
    response_model=SigningKeyResponse,
)
def revoke_signing_key(
    partner_id: uuid.UUID,
    key_id: str,
    payload: SigningKeyRevoke,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> Any:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_MEMBERS,
    )
    try:
        key = tenancy_service.revoke_signing_key(
            db,
            partner=partner,
            key_id=key_id,
            reason=payload.reason,
            actor_id=current_user.id,
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    db.refresh(key)
    return key


# ---------------------------------------------------------------------------
# Agreements
# ---------------------------------------------------------------------------


@router.get(
    "/{partner_id}/agreements", response_model=list[RevShareAgreementResponse]
)
def list_agreements(
    partner_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> list[Any]:
    _partner_ctx(
        db, partner_id=partner_id, user=current_user, allowed_roles=ROLES_READING
    )
    return list(
        db.execute(
            select(PartnerRevShareAgreement)
            .where(PartnerRevShareAgreement.partner_id == partner_id)
            .order_by(PartnerRevShareAgreement.effective_from.desc())
        )
        .scalars()
        .all()
    )


@router.post(
    "/{partner_id}/agreements",
    response_model=RevShareAgreementResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_agreement(
    partner_id: uuid.UUID,
    payload: RevShareAgreementCreate,
    operator: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> Any:
    partner = db.get(Partner, partner_id)
    if partner is None:
        raise HTTPException(status_code=404, detail="Partner not found.")

    existing = db.execute(
        select(PartnerRevShareAgreement).where(
            PartnerRevShareAgreement.partner_id == partner_id,
            PartnerRevShareAgreement.status == "ACTIVE",
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                "This partner already has an ACTIVE agreement. End it with an "
                "effective_to date before writing a replacement — two "
                "overlapping active agreements make 'which rate applies' "
                "planner-dependent."
            ),
        )

    agreement = PartnerRevShareAgreement(
        partner_id=partner_id,
        name=payload.name,
        basis=payload.basis,
        share_bps=payload.share_bps,
        zero_byok_share_bps=payload.zero_byok_share_bps,
        currency=payload.currency,
        minimum_payout_micros=payload.minimum_payout_micros,
        unknown_cost_basis_policy=payload.unknown_cost_basis_policy,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        status="ACTIVE",
    )
    db.add(agreement)
    db.commit()
    db.refresh(agreement)
    logger.info(
        "partner.agreement_created",
        extra={"partner_id": str(partner_id), "operator": str(operator.id)},
    )
    return agreement


# ---------------------------------------------------------------------------
# Payout periods
# ---------------------------------------------------------------------------


@router.get("/{partner_id}/payouts", response_model=list[PayoutPeriodResponse])
def list_payout_periods(
    partner_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> list[Any]:
    _partner_ctx(
        db, partner_id=partner_id, user=current_user, allowed_roles=ROLES_READING
    )
    return rev_share_service.periods_for(db, partner_id=partner_id)


@router.post(
    "/{partner_id}/payouts",
    response_model=PayoutPeriodResponse,
    status_code=status.HTTP_201_CREATED,
)
def compute_payout_period(
    partner_id: uuid.UUID,
    payload: PayoutPeriodCompute,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> Any:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_BOOK,
    )
    try:
        period = rev_share_service.compute_period(
            db,
            partner=partner,
            period_start=payload.period_start,
            period_end=payload.period_end,
            actor_id=current_user.id,
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    db.refresh(period)
    return period


@router.get(
    "/{partner_id}/payouts/{period_id}", response_model=PayoutStatementResponse
)
def get_payout_statement(
    partner_id: uuid.UUID,
    period_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> PayoutStatementResponse:
    _partner_ctx(
        db, partner_id=partner_id, user=current_user, allowed_roles=ROLES_READING
    )
    period = db.get(PartnerPayoutPeriod, period_id)
    if period is None or period.partner_id != partner_id:
        raise HTTPException(status_code=404, detail="Payout period not found.")

    matches, _stored, recomputed = rev_share_service.verify_digest(
        db, period=period
    )
    lines = rev_share_service.ledger_lines(
        db, partner_id=partner_id, period_id=period_id
    )
    return PayoutStatementResponse(
        period=PayoutPeriodResponse.model_validate(period),
        lines=[RevShareLedgerLine.model_validate(line) for line in lines],
        digest_matches=bool(matches and period.status != "DRAFT"),
        recomputed_digest=recomputed,
    )


@router.post(
    "/{partner_id}/payouts/{period_id}/seal", response_model=PayoutPeriodResponse
)
def seal_payout_period(
    partner_id: uuid.UUID,
    period_id: uuid.UUID,
    payload: PayoutPeriodSeal,
    request: Request,
    operator: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> Any:
    partner = db.get(Partner, partner_id)
    period = db.get(PartnerPayoutPeriod, period_id)
    if partner is None or period is None or period.partner_id != partner_id:
        raise HTTPException(status_code=404, detail="Payout period not found.")
    try:
        rev_share_service.seal_period(
            db,
            partner=partner,
            period=period,
            settlement_notes=payload.settlement_notes,
            actor_id=operator.id,
            ip_address=client_ip(request),
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    db.refresh(period)
    return period


@router.post(
    "/{partner_id}/payouts/{period_id}/paid", response_model=PayoutPeriodResponse
)
def mark_payout_paid(
    partner_id: uuid.UUID,
    period_id: uuid.UUID,
    payload: PayoutPeriodMarkPaid,
    operator: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
) -> Any:
    partner = db.get(Partner, partner_id)
    period = db.get(PartnerPayoutPeriod, period_id)
    if partner is None or period is None or period.partner_id != partner_id:
        raise HTTPException(status_code=404, detail="Payout period not found.")
    try:
        rev_share_service.mark_paid(
            db,
            partner=partner,
            period=period,
            payment_reference=payload.payment_reference,
            actor_id=operator.id,
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    db.refresh(period)
    return period


@router.get("/{partner_id}/economics", response_model=PartnerEconomicsSummary)
def get_economics(
    partner_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    partner = _partner_ctx(
        db, partner_id=partner_id, user=current_user, allowed_roles=ROLES_READING
    )
    return rev_share_service.economics_summary(db, partner=partner)


# ---------------------------------------------------------------------------
# Catalog authoring
# ---------------------------------------------------------------------------


@router.get("/{partner_id}/catalog", response_model=list[MarketplaceItemResponse])
def list_catalog_items(
    partner_id: uuid.UUID,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    partner = _partner_ctx(
        db, partner_id=partner_id, user=current_user, allowed_roles=ROLES_READING
    )
    items = list(
        db.execute(
            select(MarketplaceItem)
            .where(MarketplaceItem.partner_id == partner_id)
            .order_by(MarketplaceItem.name)
        )
        .scalars()
        .all()
    )

    payload: list[dict[str, Any]] = []
    for item in items:
        latest = db.execute(
            select(MarketplaceManifest)
            .where(
                MarketplaceManifest.item_id == item.id,
                MarketplaceManifest.status == "PUBLISHED",
            )
            .order_by(MarketplaceManifest.published_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        payload.append(
            {
                "id": item.id,
                "partner_id": item.partner_id,
                "partner_name": partner.name,
                "slug": item.slug,
                "name": item.name,
                "summary": item.summary,
                "category": item.category,
                "status": item.status,
                "visibility": item.visibility,
                "latest_version": latest.version if latest else None,
                "latest_manifest_id": latest.id if latest else None,
                "installed": False,
                "created_at": item.created_at,
            }
        )
    return payload


@router.post(
    "/{partner_id}/catalog",
    response_model=MarketplaceItemResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_catalog_item(
    partner_id: uuid.UUID,
    payload: MarketplaceItemCreate,
    current_user: CurrentUser,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_CATALOG,
    )
    try:
        item = marketplace_service.create_item(
            db,
            partner=partner,
            slug=payload.slug,
            name=payload.name,
            summary=payload.summary,
            category=payload.category,
            visibility=payload.visibility,
            actor_id=current_user.id,
        )
    except PartnerError as exc:
        raise _http(exc) from exc
    db.commit()
    db.refresh(item)
    return {
        "id": item.id,
        "partner_id": item.partner_id,
        "partner_name": partner.name,
        "slug": item.slug,
        "name": item.name,
        "summary": item.summary,
        "category": item.category,
        "status": item.status,
        "visibility": item.visibility,
        "latest_version": None,
        "latest_manifest_id": None,
        "installed": False,
        "created_at": item.created_at,
    }


@router.post(
    "/{partner_id}/catalog/{item_id}/manifests",
    response_model=ManifestResponse,
    status_code=status.HTTP_201_CREATED,
)
def publish_manifest(
    partner_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ManifestSubmission,
    current_user: CurrentUser,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    partner = _partner_ctx(
        db,
        partner_id=partner_id,
        user=current_user,
        allowed_roles=ROLES_MANAGING_CATALOG,
    )
    try:
        item = marketplace_service.get_item(db, item_id=item_id)
        manifest, signature = marketplace_service.publish_manifest(
            db,
            partner=partner,
            item=item,
            version=payload.version,
            nodes=payload.nodes,
            edges=payload.edges,
            signing_key_id=payload.signing_key_id,
            signature_b64=payload.signature,
            actor_id=current_user.id,
            ip_address=client_ip(request),
        )
    except PartnerError as exc:
        db.commit()
        raise _http(exc) from exc
    db.commit()
    db.refresh(manifest)
    return {
        "id": manifest.id,
        "item_id": manifest.item_id,
        "version": manifest.version,
        "status": manifest.status,
        "content_digest": manifest.content_digest,
        "node_count": manifest.node_count,
        "edge_count": manifest.edge_count,
        "published_at": manifest.published_at,
        "signatures": [
            {
                "id": signature.id,
                "algorithm": signature.algorithm,
                "signed_digest": signature.signed_digest,
                "verified_at": signature.verified_at,
                "signing_key_fingerprint": None,
                "signing_key_status": None,
            }
        ],
    }