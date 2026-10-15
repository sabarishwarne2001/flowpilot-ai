"""The effects of Helm's actions that reach beyond a settlement: tasks, notices, emails, requests.

Each function reuses the underlying service the rest of the product uses (never a copy of its
logic): mail climbs the organization's TRANSACTIONAL sender ladder (`email_resolution`) and leaves
through `email_service` (which enforces egress); notices go through
`organization_notification_service.emit`; tasks are `helm_tasks` rows the review hub lists.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.helm import HelmAction, HelmBook, HelmTask
from app.services.helm import money
from app.services.helm import vocabulary as v

logger = logging.getLogger("app.services.helm.hands")


class HandError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _identity(db: Session, organization_id: uuid.UUID, workspace_id: Optional[uuid.UUID]):
    from app.services.email_resolution import MessageKind, resolve_email_identity

    return resolve_email_identity(db, organization_id=organization_id, workspace_id=workspace_id,
                                  message_kind=MessageKind.TRANSACTIONAL)


def email_destination(db: Session, *, organization_id: uuid.UUID, workspace_id: Optional[uuid.UUID],
                      to: Optional[str]) -> tuple[bool, str]:
    """May Helm email `to` for this organization? A known address, and egress allows its mail path."""
    if not to or "@" not in to:
        return False, "No known email contact for this counterparty; add one in Helm settings."
    try:
        identity = _identity(db, organization_id, workspace_id)
    except Exception as exc:  # noqa: BLE001 - PlatformEmailNotConfigured and friends
        return False, f"No mail server is configured ({type(exc).__name__})."
    from app.core import egress
    from app.services.email_resolution import LAYER_PLATFORM

    channel = egress.SMTP_PLATFORM if identity.transport_layer == LAYER_PLATFORM else egress.SMTP_TENANT
    decision = egress.decide(channel, identity.smtp.smtp_host, identity.smtp.smtp_port,
                             organization_id=organization_id, db=db)
    if not decision.allowed:
        return False, f"Egress does not allow the organization's mail server: {decision.explanation}."
    return True, f"Email to {to} from {identity.from_address}."


def send_mail(db: Session, *, organization_id: uuid.UUID, workspace_id: Optional[uuid.UUID], to: str, subject: str,
              html_body: str, text_body: str) -> dict[str, Any]:
    from app.services.email_service import email_service

    identity = _identity(db, organization_id, workspace_id)
    config = identity.smtp.model_copy(update={"from_email": identity.from_address,
                                              "sender_name": identity.sender_name})
    ok, detail = email_service.send_html_email(settings=config, recipient=to, subject=subject, html_body=html_body,
                                               text_body=text_body, reply_to=identity.reply_to)
    if not ok:
        raise HandError("HELM_MAIL_FAILED", f"The mail server did not accept the message: {detail}")
    return {"sent_to": to, "from": identity.from_address, "subject": subject,
            "sent_at": datetime.now(timezone.utc).isoformat()}


def _organization_name(db: Session, organization_id: uuid.UUID) -> str:
    from app.models.organization import Organization

    org = db.get(Organization, organization_id)
    return (getattr(org, "legal_name", None) or getattr(org, "name", None) or "Our company") if org else "Our company"


def draft_dispute(db: Session, *, action: HelmAction, book: HelmBook, snapshot) -> dict[str, Any]:
    from app.templates.emails import helm as templates

    params = dict(action.params or {})
    counterparty = snapshot.counterparties.get(action.counterparty_key or "") if snapshot else None
    payable = snapshot.payable(str(action.promise_id)) if (snapshot and action.promise_id) else None
    subject, html_body, text_body = templates.render_dispute(
        organization_name=_organization_name(db, action.organization_id),
        supplier_name=(counterparty.name if counterparty else None) or (payable.counterparty_name if payable else None)
        or "Supplier",
        invoice_number=(payable.document_number if payable else None) or params.get("document_number") or "",
        currency=action.currency or "", lines=params.get("lines") or [],
        overcharge_micros=int(action.amount_micros or 0))
    draft = {"subject": subject, "text": text_body, "to": counterparty.contact_email if counterparty else None}
    if action.autonomy == v.AUTONOMY_ACT and draft["to"] and book.kind != v.BOOK_SAMPLE:
        sent = send_mail(db, organization_id=action.organization_id, workspace_id=action.workspace_id,
                         to=draft["to"], subject=subject, html_body=html_body, text_body=text_body)
        return {"draft": draft, "sent": sent}
    return {"draft": draft, "sent": None,
            "note": "A draft: copy it into your email, or switch the mandate to Act mode with a known contact."}


def create_task(db: Session, *, action: HelmAction, book: HelmBook, snapshot=None) -> dict[str, Any]:
    params = dict(action.params or {})
    workspace_id = action.workspace_id or (book.workspace_id if book.workspace_id else None)
    if workspace_id is None:
        raise HandError("HELM_TASK_NO_WORKSPACE", "A task needs a workspace to appear in the review hub.")
    task = HelmTask(organization_id=action.organization_id, workspace_id=workspace_id, book_id=action.book_id,
                    action_id=action.id, kind=params.get("task_kind") or v.TASK_GENERAL,
                    work_item_id=action.work_item_id,
                    obligation_id=uuid.UUID(params["obligation_id"]) if params.get("obligation_id") else None,
                    title=action.title[:500], body=action.explanation, severity=params.get("severity") or "MEDIUM",
                    status=v.TASK_OPEN)
    db.add(task)
    db.flush()
    assignee = params.get("assignee_user_id")
    if assignee:
        _assign(db, task=task, assignee_user_id=uuid.UUID(assignee))
    return {"task_id": str(task.id), "workspace_id": str(workspace_id)}


def _assign(db: Session, *, task: HelmTask, assignee_user_id: uuid.UUID) -> None:
    from app.services.review import resolution

    try:
        if resolution.can_hold_review_items(db, workspace_id=task.workspace_id, user_id=assignee_user_id):
            from app.models.review import ReviewAssignment

            db.add(ReviewAssignment(kind="HELM", item_id=task.id, workspace_id=task.workspace_id,
                                    assignee_user_id=assignee_user_id, assigned_by_user_id=None))
            db.flush()
    except Exception:  # noqa: BLE001 - an unassigned task still sits in the queue
        logger.exception("helm.task_assign_failed", extra={"task_id": str(task.id)})


def notify(db: Session, *, action: HelmAction, book: HelmBook, snapshot=None) -> dict[str, Any]:
    from app.models.notification import NotificationPriority, NotificationType
    from app.models.organization import OrganizationRole
    from app.services import organization_notification_service as notices

    if book.kind == v.BOOK_SAMPLE:
        return {"notified": 0, "note": "The sample company sends no notices."}
    count = notices.emit_to_roles(
        db, organization_id=action.organization_id, roles=(OrganizationRole.OWNER, OrganizationRole.ADMIN),
        title=action.title[:150], message=action.explanation[:1900], notification_type=NotificationType.SYSTEM,
        priority=NotificationPriority.WARNING, category=v.NOTIFICATION_CATEGORY, action="Open Helm")
    return {"notified": count}


def say(micros: Optional[int], currency: Optional[str]) -> str:
    return money.say(micros, currency)


__all__ = ["HandError", "create_task", "draft_dispute", "email_destination", "notify", "send_mail"]
