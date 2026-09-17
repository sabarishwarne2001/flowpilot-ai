"""ARCH-37 action `notify.role` — tell everyone holding an organization role.

Recipients are resolved when the rule runs, from active organization
memberships, so a rule written a year ago reaches today's Finance team.

Who is reachable: OWNER and ADMIN hold implicit ADMIN on every workspace
(`organization_permissions.IMPLICIT_WORKSPACE_ADMIN_ROLES`). BILLING and
MEMBER do not, so they are notified only if they are active members of this
workspace — otherwise the notification would link to a page they cannot open.

Delivery is `notification.outbox_dispatcher.dispatch`: in-app immediately,
email for WARNING and ERROR, content passed through the stream's output
filter before it is stored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    render,
    variables_for,
)

ACTION_TYPE = "notify.role"
ROLE_VALUES = ("OWNER", "ADMIN", "BILLING", "MEMBER")
MAX_RECIPIENTS = 200


class NotifyRoleConfig(ActionConfig):
    roles: list[str] = Field(min_length=1, max_length=4)
    title: str = Field(default="{{rule.name}}: {{document.filename}}", min_length=1, max_length=150)
    message: str = Field(
        default="{{trigger.label}} — {{document.filename}}", min_length=1, max_length=500
    )
    priority: Literal["INFO", "SUCCESS", "WARNING", "ERROR"] = "INFO"

    @field_validator("roles")
    @classmethod
    def _roles(cls, value: list[str]) -> list[str]:
        normalised = [v.upper() for v in value]
        unknown = sorted(set(normalised) - set(ROLE_VALUES))
        if unknown:
            raise ValueError(f"Unknown organization role(s): {unknown}.")
        if len(set(normalised)) != len(normalised):
            raise ValueError("roles must not repeat.")
        return normalised


def recipients(db: Any, *, organization_id: Any, workspace_id: Any, roles: list[str]) -> list[Any]:
    from sqlalchemy import select

    from app.core.organization_permissions import IMPLICIT_WORKSPACE_ADMIN_ROLES
    from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
    from app.models.user import User
    from app.models.workspace import WorkspaceMember

    wanted = [OrganizationRole(role) for role in roles]
    rows = db.execute(
        select(User, OrganizationMember.role)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.status == MembershipStatus.ACTIVE,
            OrganizationMember.role.in_(wanted),
        )
        .order_by(User.id)
        .limit(MAX_RECIPIENTS)
    ).all()
    implicit = set(IMPLICIT_WORKSPACE_ADMIN_ROLES)
    needs_membership = [user.id for user, role in rows if role not in implicit]
    members: set[Any] = set()
    if needs_membership:
        members = set(
            db.execute(
                select(WorkspaceMember.user_id).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.status == MembershipStatus.ACTIVE,
                    WorkspaceMember.user_id.in_(needs_membership),
                )
            ).scalars()
        )
    return [user for user, role in rows if role in implicit or user.id in members]


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.models.notification import (
        Notification,
        NotificationChannel,
        NotificationPriority,
        NotificationStatus,
        NotificationType,
    )
    from app.services.notification import outbox_dispatcher

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, NotifyRoleConfig)
    users = recipients(
        state.db,
        organization_id=state.execution.organization_id,
        workspace_id=state.execution.workspace_id,
        roles=config.roles,
    )
    if not users:
        raise ActionFailure(
            f"Nobody in this workspace holds {', '.join(config.roles)}.", recoverable=True
        )

    values = variables_for(state)
    title = render(config.title, values)[:150] or state.rule.name[:150]
    message = render(config.message, values)[:500] or title
    work_item_id = state.work_item.id if state.work_item is not None else None

    for user in users:
        notification = Notification(
            workspace_id=state.execution.workspace_id,
            user_id=user.id,
            work_item_id=work_item_id,
            title=title,
            message=message,
            notification_type=NotificationType.AUTOMATION,
            priority=NotificationPriority(config.priority),
            delivery_channel=NotificationChannel.IN_APP,
            delivery_status=NotificationStatus.PENDING,
            retry_count=0,
            is_read=False,
        )
        state.db.add(notification)
        state.db.flush([notification])
        outbox_dispatcher.dispatch(
            state.db,
            notification=notification,
            user=user,
            organization_id=state.execution.organization_id,
            workspace_id=state.execution.workspace_id,
            idempotency_prefix=f"automation:{state.execution.id}",
        )
    return ActionOutcome(
        summary=f"notified {len(users)} {'person' if len(users) == 1 else 'people'}",
        external_ref=f"recipients:{len(users)}",
        details={"roles": list(config.roles), "recipients": len(users)},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Notify a role",
    description="Send an in-app notification (and email for warnings) to everyone holding a role.",
    category="Notifications",
    config_model=NotifyRoleConfig,
    selector="automation.flow.notify_role",
    perform=perform,
    capability=None,
    minimum_role=ROLE_WORKSPACE_ADMIN,
    template_fields=("title", "message"),
)
