"""A workspace's "Reply-to" reaches the mail it sends (campaign session 3, F-306).

`Settings > Email` offers "Reply-to (optional)" for a workspace's own mail server, and the
resolution trail shows it. Every workspace send path kept only the server settings
(`identity.smtp`), so automation emails and workspace notices went out with no Reply-To and
replies landed in the sending mailbox. The resolved configuration now carries it, and a message
that names its own Reply-To (an invitation answers to the inviter) still wins.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from sqlalchemy.orm import Session

from app.core.smtp import resolve_smtp_config
from app.models.email_settings import EmailEncryption
from app.models.workspace_email_override import WorkspaceEmailOverride
from app.services import organization_email_settings_service as org_smtp
from app.services.email_service import email_service
from tests.conftest import Fixture


@pytest.fixture
def sent(monkeypatch) -> list[EmailMessage]:
    captured: list[EmailMessage] = []

    def capture(config, message):
        captured.append(message)
        return True, "ok"

    monkeypatch.setattr(email_service, "_send_message", capture)
    return captured


def _override(db: Session, tenant: Fixture, reply_to: str | None) -> None:
    db.add(WorkspaceEmailOverride(
        workspace_id=tenant.workspace.id, organization_id=tenant.organization.id,
        smtp_host="smtp.workspace.test", smtp_port=25, smtp_username="team@workspace-mail.com",
        smtp_password_encrypted=org_smtp.encrypt_password("workspace-pw"), sender_name="Team",
        encryption=EmailEncryption.TLS.value, is_enabled=True, reply_to_address=reply_to,
    ))
    db.commit()


def test_workspace_mail_answers_to_the_reply_to_address(
    db_session: Session, tenant: Fixture, sent: list[EmailMessage]
) -> None:
    _override(db_session, tenant, "accounts@acme.example")
    config = resolve_smtp_config(db_session, workspace_id=tenant.workspace.id)
    email_service.send_email(settings=config, recipient="a@b.example", subject="Rent due", body="...")
    email_service.send_html_email(settings=config, recipient="a@b.example", subject="Rent due",
                                  html_body="<p>...</p>", text_body="...")
    assert [message["Reply-To"] for message in sent] == ["accounts@acme.example"] * 2


def test_a_message_that_names_its_own_reply_to_keeps_it(
    db_session: Session, tenant: Fixture, sent: list[EmailMessage]
) -> None:
    _override(db_session, tenant, "accounts@acme.example")
    config = resolve_smtp_config(db_session, workspace_id=tenant.workspace.id)
    email_service.send_html_email(settings=config, recipient="a@b.example", subject="Join us",
                                  html_body="<p>...</p>", text_body="...", reply_to="inviter@acme.example")
    assert sent[0]["Reply-To"] == "inviter@acme.example"


def test_no_reply_to_set_means_no_header(db_session: Session, tenant: Fixture, sent: list[EmailMessage]) -> None:
    _override(db_session, tenant, None)
    config = resolve_smtp_config(db_session, workspace_id=tenant.workspace.id)
    email_service.send_email(settings=config, recipient="a@b.example", subject="Rent due", body="...")
    assert sent[0]["Reply-To"] is None
