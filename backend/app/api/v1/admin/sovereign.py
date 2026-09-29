"""ARCH-50 — the operator's Sovereign console (platform superadmins only).

    GET  /admin/sovereign                  edition, egress mode and channel inventory, the local model's config,
                                           the licence (re-verified), usage against it, DR evidence
    GET  /admin/sovereign/local-llm        probe the local model (reachable? which models does it serve?)
    GET  /admin/sovereign/refusals         refused connections across every organization and the deployment
    POST /admin/sovereign/egress-test      would this destination be allowed (optionally for an organization)?
    POST /admin/sovereign/licence          install a licence (verified offline before it is stored)
    GET  /admin/sovereign/release          verify the release manifest (SHA256SUMS signature + every file)

ARCH50-S1:sovereign-api. Declared once on the router: `require_superadmin` (404 for anyone else), as ARCH-18's
COGS console does. Nothing here CHANGES the egress mode or the local model: those are the operator's environment
(so a compromised admin account cannot open a sovereign deployment's egress from a browser).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_superadmin
from app.core import egress
from app.models.user import User
from app.schemas.sovereign import (EgressDecisionOut, EgressRefusalList, EgressRefusalOut, LicenceIn,
                                   LicenceStatusOut, OperatorEgressTestIn, ReleaseStatusOut, SovereignStatusOut)
from app.services.sovereign import dr, egress_policy, licence, local_llm, release

router = APIRouter(prefix="/admin/sovereign", tags=["Sovereign edition"], dependencies=[Depends(require_superadmin)])


def _licence_out(status: licence.LicenceStatus) -> LicenceStatusOut:
    return LicenceStatusOut(**status.as_dict())


@router.get("", response_model=SovereignStatusOut)
def sovereign_status(db: Session = Depends(get_db)) -> SovereignStatusOut:
    from app.core.config import settings

    cfg = local_llm.config()
    refused = db.execute(text("SELECT COALESCE(sum(count), 0) FROM egress_refusals WHERE last_at >= :t"),
                         {"t": egress.now() - timedelta(hours=24)}).scalar_one()
    return SovereignStatusOut(
        edition=licence.edition(),
        environment=str(getattr(settings, "ENVIRONMENT", "development")),
        egress=egress.deployment_summary(),
        local_llm={"mode": local_llm.mode(), **(cfg.public() if cfg else {"configured": False})},
        licence=_licence_out(licence.current(db)),
        usage=licence.usage(db),
        dr=dr.status(db),
        refusals_24h=int(refused),
    )


@router.get("/local-llm")
def local_llm_health() -> dict[str, Any]:
    return local_llm.health()


@router.get("/refusals", response_model=EgressRefusalList)
def all_refusals(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db)) -> EgressRefusalList:
    rows = egress_policy.list_refusals(db, organization_id=None, days=days, limit=500)
    return EgressRefusalList(days=days, refusals=[EgressRefusalOut(**r) for r in rows])


@router.post("/egress-test", response_model=EgressDecisionOut)
def operator_egress_test(payload: OperatorEgressTestIn, db: Session = Depends(get_db)) -> EgressDecisionOut:
    host, port = egress_policy.parse_destination(payload.destination)
    return EgressDecisionOut(**egress.decide(payload.channel, host, port, organization_id=payload.organization_id,
                                             db=db).as_dict())


@router.post("/licence", response_model=LicenceStatusOut)
def install_licence(payload: LicenceIn, db: Session = Depends(get_db),
                    user: User = Depends(require_superadmin)) -> LicenceStatusOut:
    try:
        status = licence.install(db, payload.licence, actor_id=user.id)
    except licence.LicenceFormatError as exc:
        raise licence.LicenceRequired(f"the licence could not be read: {exc}", status="MALFORMED") from exc
    db.commit()
    return _licence_out(status)


@router.get("/release", response_model=ReleaseStatusOut)
def release_status() -> ReleaseStatusOut:
    root = release.repository_root()
    return ReleaseStatusOut(**release.verify_release(root / "release", root=root))
