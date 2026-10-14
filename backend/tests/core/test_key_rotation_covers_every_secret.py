"""Rotating the encryption key re-encrypts every stored secret, not only the SMTP passwords
(campaign session 4, F-370).

`EMAIL_ENCRYPTION_KEYS` encrypts every secret the app keeps for a customer: mail server passwords,
two-factor secrets, provider API keys, identity-provider client secrets, webhook signing secrets,
warehouse and ERP credentials, entity identifiers, extraction memory. The re-encryption sweep
(`scripts/reencrypt_smtp_passwords.py`) rewrote two SMTP tables only, one of them read by nothing,
and `--verify` then said "every row is under the head key". An operator who trusted it and dropped
the old key locked every person out of two-factor sign-in and broke each workspace's own mail
server, every bring-your-own-key provider and every OIDC connection.

    pytest tests/core/test_key_rotation_covers_every_secret.py -q
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import text

import app.models  # noqa: F401 - every table on the metadata
from app.core import encryption
from app.core.config import settings
from app.db.base import Base
from app.models.byok import TenantProviderCredential
from app.models.identity import EnterpriseIdpConfig, VerifiedDomain
from app.models.user_mfa import UserMfaFactor
from app.models.workspace_email_override import WorkspaceEmailOverride
from app.services import encryption_rotation_service as rotation


@pytest.fixture
def two_keys(monkeypatch):
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()

    def configure(*keys: str) -> None:
        monkeypatch.setattr(settings, "_encryption_key_list", list(keys), raising=False)
        encryption.reset_encryption()

    configure(old)
    yield old, new, configure
    encryption.reset_encryption()


def _idp_with_client_secret(db, organization_id: uuid.UUID, secret: str) -> EnterpriseIdpConfig:
    domain = VerifiedDomain(
        organization_id=organization_id, domain=f"rot-{uuid.uuid4().hex[:8]}.example", status="VERIFIED",
        challenge_token="tok", challenge_expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        first_verified_at=datetime.now(timezone.utc), is_sso_binding=True,
    )
    db.add(domain)
    db.flush()
    config = EnterpriseIdpConfig(
        organization_id=organization_id, verified_domain_id=domain.id, protocol="OIDC", display_name="OIDC",
        is_active=False, oidc_issuer="https://idp.example", oidc_client_id="client",
        oidc_client_secret_encrypted=encryption.encrypt_password(secret).encode("utf-8"),
        jit_default_org_role="MEMBER", jit_provisioning_mode="INVITE_ONLY",
    )
    db.add(config)
    db.flush()
    return config


def test_after_the_sweep_every_kind_of_secret_reads_under_the_new_key_alone(db_session, tenant, two_keys) -> None:
    old, new, configure = two_keys
    db = db_session

    db.add(WorkspaceEmailOverride(
        workspace_id=tenant.workspace.id, organization_id=tenant.organization.id, is_enabled=False,
        smtp_password_encrypted=encryption.encrypt_password("workspace-relay-password"),
    ))
    db.add(UserMfaFactor(
        user_id=tenant.owner.user.id, secret_ciphertext=encryption.encrypt_secret("JBSWY3DPEHPK3PXP"),
        key_fingerprint=encryption.head_key_fingerprint(), recovery_code_hashes=[],
    ))
    credential = TenantProviderCredential(
        organization_id=tenant.organization.id, provider="MISTRAL",
        encrypted_api_key=encryption.encrypt_password("tenant-mistral-key"), key_fingerprint="abc123", key_last_four="-key",
    )
    db.add(credential)
    idp = _idp_with_client_secret(db, tenant.organization.id, "oidc-client-secret")
    db.commit()

    # The new key goes first; the old one stays to read what is not rotated yet.
    configure(new, old)
    reports = rotation.reencrypt_all_smtp_passwords(db, dry_run=False)
    assert sum(r.failed for r in reports) == 0, [str(r) for r in reports]
    db.expire_all()

    # The old key is dropped, as the operator does once --verify passes.
    configure(new)
    override = db.get(WorkspaceEmailOverride, tenant.workspace.id)
    factor = db.get(UserMfaFactor, tenant.owner.user.id)
    stored = db.get(TenantProviderCredential, credential.id)
    connection = db.get(EnterpriseIdpConfig, idp.id)
    assert encryption.decrypt_password(override.smtp_password_encrypted) == "workspace-relay-password"
    assert encryption.decrypt_secret(factor.secret_ciphertext) == "JBSWY3DPEHPK3PXP"
    assert encryption.decrypt_password(stored.encrypted_api_key) == "tenant-mistral-key"
    assert encryption.decrypt_password(bytes(connection.oidc_client_secret_encrypted).decode("ascii")) == "oidc-client-secret"
    # The two-factor row records the key it is now under.
    assert factor.key_fingerprint == encryption.head_key_fingerprint()


def test_verify_finds_a_secret_still_under_the_old_key(db_session, tenant, two_keys) -> None:
    old, new, configure = two_keys
    db_session.add(UserMfaFactor(
        user_id=tenant.owner.user.id, secret_ciphertext=encryption.encrypt_secret("JBSWY3DPEHPK3PXP"),
        key_fingerprint=encryption.head_key_fingerprint(), recovery_code_hashes=[],
    ))
    db_session.commit()
    configure(new, old)

    pending = rotation.reencrypt_all_smtp_passwords(db_session, dry_run=True)
    assert sum(r.rotated for r in pending) >= 1, "a dry run (what --verify reads) must count the two-factor secret"
    db_session.expire_all()
    still_old = db_session.get(UserMfaFactor, tenant.owner.user.id)
    assert encryption.decrypting_key_index(still_old.secret_ciphertext) == 1, "a dry run changes nothing"


_ENCRYPTED_NAME = re.compile(r"(encrypted|ciphertext)")
#: Columns whose name matches but which hold no ciphertext.
_NOT_CIPHERTEXT = {("enterprise_idp_configs", "want_assertions_encrypted")}


def test_every_encrypted_column_in_the_schema_is_swept() -> None:
    """A new encrypted column cannot be forgotten: name it `*_encrypted` or `*_ciphertext` and list it."""
    in_schema = {
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if _ENCRYPTED_NAME.search(column.name) and (table.name, column.name) not in _NOT_CIPHERTEXT
    }
    swept = {(target.table, target.column) for target in rotation.ENCRYPTED_COLUMNS}
    assert in_schema - swept == set(), f"encrypted columns the key rotation would leave behind: {sorted(in_schema - swept)}"
    assert swept - in_schema == set(), f"the sweep names columns that do not exist: {sorted(swept - in_schema)}"


def test_the_sweep_reads_each_table_by_its_own_key(db_session) -> None:
    """Two of the tables are keyed by something other than `id` (a workspace, a person)."""
    for target in rotation.ENCRYPTED_COLUMNS:
        row = db_session.execute(text(
            "SELECT count(*) FROM information_schema.columns WHERE table_name = :t AND column_name IN (:k, :c)"
        ), {"t": target.table, "k": target.key, "c": target.column}).scalar_one()
        assert row == 2, f"{target.table}: no column {target.key!r} or {target.column!r}"
