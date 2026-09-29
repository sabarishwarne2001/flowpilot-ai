"""ARCH-50 — an organization's egress lockdown (capability.egress_lockdown, Enterprise).

    GET    /organizations/{oid}/egress                       the policy, its rules, the deployment's mode   [ADMIN]
    PUT    /organizations/{oid}/egress                       switch the lockdown on or off                  [OWNER, audited]
    POST   /organizations/{oid}/egress/rules                 allow a destination (channel, host, port)      [OWNER, audited]
    DELETE /organizations/{oid}/egress/rules/{rule_id}       remove a rule                                  [OWNER, audited]
    GET    /organizations/{oid}/egress/refusals              what was refused, per hour, with counts        [ADMIN]
    POST   /organizations/{oid}/egress/test                  would this destination be allowed? (dry run)   [ADMIN]

ARCH50-S1:egress-api. Every route is gated on capability.egress_lockdown as the FIRST statement of its body (the
organization and the role are checked by the dependency before it): 402 CAPABILITY_REQUIRED, audited. Switching the
lockdown on and editing rules is the OWNER's decision (like BYOK credentials): it decides where the organization's
data may go. A lockdown keeps being ENFORCED after a downgrade (see egress_policy's docstring); only editing it
needs the plan.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import OrganizationContext, RequireOrgAdmin, RequireOrgOwner, get_db
from app.core import entitlements
from app.schemas.sovereign import (EgressDecisionOut, EgressLockdownIn, EgressPolicyOut, EgressRefusalList,
                                   EgressRefusalOut, EgressRuleIn, EgressRuleOut, EgressTestIn)
from app.services.sovereign import egress_policy

router = APIRouter(tags=["Egress lockdown"])


def _gate(db: Session, context: Any, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=entitlements.EGRESS_LOCKDOWN_CAPABILITY,
                                       operation=operation)


@router.get("/organizations/{organization_id}/egress", response_model=EgressPolicyOut)
def get_egress_policy(organization_id: uuid.UUID, db: Session = Depends(get_db),
                      context: OrganizationContext = Depends(RequireOrgAdmin)) -> EgressPolicyOut:
    _gate(db, context, "egress.read")
    return EgressPolicyOut(**egress_policy.get_policy(db, organization_id=context.organization_id))


@router.put("/organizations/{organization_id}/egress", response_model=EgressPolicyOut)
def set_egress_lockdown(organization_id: uuid.UUID, payload: EgressLockdownIn, db: Session = Depends(get_db),
                        context: OrganizationContext = Depends(RequireOrgOwner)) -> EgressPolicyOut:
    _gate(db, context, "egress.lockdown")
    policy = egress_policy.set_lockdown(db, organization_id=context.organization_id,
                                        enabled=payload.lockdown_enabled, actor_id=context.user_id)
    db.commit()
    return EgressPolicyOut(**policy)


@router.post("/organizations/{organization_id}/egress/rules", response_model=EgressRuleOut,
             status_code=status.HTTP_201_CREATED)
def add_egress_rule(organization_id: uuid.UUID, payload: EgressRuleIn, db: Session = Depends(get_db),
                    context: OrganizationContext = Depends(RequireOrgOwner)) -> EgressRuleOut:
    _gate(db, context, "egress.rule_add")
    rule = egress_policy.add_rule(db, organization_id=context.organization_id, channel=payload.channel,
                                  host_pattern=payload.host_pattern, port=payload.port, note=payload.note,
                                  actor_id=context.user_id)
    db.commit()
    return EgressRuleOut(**rule)


@router.delete("/organizations/{organization_id}/egress/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT,
               response_model=None)
def delete_egress_rule(organization_id: uuid.UUID, rule_id: uuid.UUID, db: Session = Depends(get_db),
                       context: OrganizationContext = Depends(RequireOrgOwner)) -> Response:
    _gate(db, context, "egress.rule_remove")
    egress_policy.delete_rule(db, organization_id=context.organization_id, rule_id=rule_id, actor_id=context.user_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/organizations/{organization_id}/egress/refusals", response_model=EgressRefusalList)
def list_egress_refusals(organization_id: uuid.UUID, days: int = Query(7, ge=1, le=90),
                         db: Session = Depends(get_db),
                         context: OrganizationContext = Depends(RequireOrgAdmin)) -> EgressRefusalList:
    _gate(db, context, "egress.refusals")
    rows = egress_policy.list_refusals(db, organization_id=context.organization_id, days=days)
    return EgressRefusalList(days=days, refusals=[EgressRefusalOut(**r) for r in rows])


@router.post("/organizations/{organization_id}/egress/test", response_model=EgressDecisionOut)
def test_egress_destination(organization_id: uuid.UUID, payload: EgressTestIn, db: Session = Depends(get_db),
                            context: OrganizationContext = Depends(RequireOrgAdmin)) -> EgressDecisionOut:
    _gate(db, context, "egress.test")
    return EgressDecisionOut(**egress_policy.test_destination(db, organization_id=context.organization_id,
                                                              channel=payload.channel,
                                                              destination=payload.destination))
