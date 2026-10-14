"""Re-encryption of every stored secret under the head key (ARCH-07 Step 9, §B.5).

Re-encrypts every stored credential under the head key so an old key can be
dropped from EMAIL_ENCRYPTION_KEYS.

F-370 (campaign session 4). `EMAIL_ENCRYPTION_KEYS` is the one key ring for every
secret the app keeps for a customer, not for mail passwords only; the sweep used
to rewrite two SMTP tables (one of them read by nothing), so after a rotation the
old key could not be dropped without locking people out of two-factor sign-in and
breaking each workspace's own mail server, every bring-your-own-key provider,
every OIDC connection, webhook signatures and warehouse and ERP credentials.
`ENCRYPTED_COLUMNS` lists them all, and
tests/core/test_key_rotation_covers_every_secret.py fails when a new
`*_encrypted` or `*_ciphertext` column is not listed here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.encryption import (
    DecryptionError,
    decrypting_key_index,
    head_key_fingerprint,
    rotate_ciphertext,
)

logger = logging.getLogger("app.services.encryption_rotation")

BATCH_SIZE = 100


@dataclass(frozen=True)
class EncryptedColumn:
    """One column holding a Fernet token under EMAIL_ENCRYPTION_KEYS."""

    table: str
    column: str
    #: The table's primary key (one column).
    key: str = "id"
    #: Stored as bytes (LargeBinary) holding the token's ASCII.
    binary: bool = False
    #: A String(512) column: a rotated token must still fit it.
    bounded: bool = False
    #: A column recording which head key wrote the row, kept truthful on rotation.
    fingerprint_column: Optional[str] = None


ENCRYPTED_COLUMNS: Sequence[EncryptedColumn] = (
    EncryptedColumn("organization_email_settings", "encrypted_password", bounded=True),
    EncryptedColumn("workspace_email_overrides", "smtp_password_encrypted", key="workspace_id", bounded=True),
    EncryptedColumn("user_mfa_factors", "secret_ciphertext", key="user_id", fingerprint_column="key_fingerprint"),
    EncryptedColumn("tenant_provider_credentials", "encrypted_api_key", bounded=True),
    EncryptedColumn("enterprise_idp_configs", "oidc_client_secret_encrypted", binary=True),
    EncryptedColumn("idp_signing_certificates", "private_key_encrypted", binary=True),
    EncryptedColumn("sso_auth_requests", "code_verifier_encrypted", binary=True),
    EncryptedColumn("webhook_endpoints", "secret_encrypted"),
    EncryptedColumn("webhook_endpoints", "previous_secret_encrypted"),
    EncryptedColumn("warehouse_destinations", "encrypted_credential"),
    EncryptedColumn("erp_targets", "credential_ciphertext"),
    EncryptedColumn("entity_identifiers", "value_ciphertext"),
    EncryptedColumn("entity_identifiers", "display_ciphertext"),
    EncryptedColumn("extraction_exemplars", "value_ciphertext", fingerprint_column="key_fingerprint"),
    EncryptedColumn("extraction_exemplars", "evidence_ciphertext", fingerprint_column="key_fingerprint"),
)

#: The tables the sweep covers (the CLI's --verify and older callers read this).
TARGET_TABLES: Sequence[str] = tuple(dict.fromkeys(target.table for target in ENCRYPTED_COLUMNS))


@dataclass
class RotationReport:
    table: str
    scanned: int = 0
    already_head: int = 0
    rotated: int = 0
    failed: int = 0
    failed_ids: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"{self.table:32s} scanned={self.scanned:5d} "
            f"already_head={self.already_head:5d} rotated={self.rotated:5d} "
            f"failed={self.failed:5d}"
        )


def _token(value: object, *, binary: bool) -> str:
    if binary:
        return bytes(value).decode("ascii")  # type: ignore[arg-type]
    return str(value)


def reencrypt_column(
    db: Session, *, target: EncryptedColumn, dry_run: bool = True
) -> RotationReport:
    report = RotationReport(table=f"{target.table}.{target.column}")
    offset = 0
    fingerprint = head_key_fingerprint() if target.fingerprint_column else None

    while True:
        rows = db.execute(
            text(
                f"""
                SELECT {target.key} AS row_id, {target.column} AS token
                  FROM {target.table}
                 WHERE {target.column} IS NOT NULL
                   AND octet_length({target.column}) > 0
                 ORDER BY {target.key}
                 LIMIT :limit OFFSET :offset
                 FOR UPDATE
                """
            ),
            {"limit": BATCH_SIZE, "offset": offset},
        ).all()

        if not rows:
            break

        for row in rows:
            report.scanned += 1
            try:
                token = _token(row.token, binary=target.binary)
                index = decrypting_key_index(token)
            except Exception:
                logger.exception("%s id=%s: key index probe failed", report.table, row.row_id)
                report.failed += 1
                report.failed_ids.append(str(row.row_id))
                continue

            if index is None:
                logger.error(
                    "ARCH07_UNDECRYPTABLE | table=%s id=%s — no configured key "
                    "decrypts this ciphertext. Row left untouched.",
                    report.table, row.row_id,
                )
                report.failed += 1
                report.failed_ids.append(str(row.row_id))
                continue

            if index == 0:
                report.already_head += 1
                continue

            if dry_run:
                report.rotated += 1
                continue

            try:
                new_token = rotate_ciphertext(token, bounded=target.bounded)
            except (DecryptionError, ValueError):
                logger.exception("%s id=%s: rotation failed", report.table, row.row_id)
                report.failed += 1
                report.failed_ids.append(str(row.row_id))
                continue

            assignments = f"{target.column} = :token"
            params: dict[str, object] = {
                "token": new_token.encode("ascii") if target.binary else new_token,
                "row_id": row.row_id,
            }
            if target.fingerprint_column:
                assignments += f", {target.fingerprint_column} = :fingerprint"
                params["fingerprint"] = fingerprint
            db.execute(
                text(f"UPDATE {target.table} SET {assignments} WHERE {target.key} = :row_id"),
                params,
            )
            report.rotated += 1

        if dry_run:
            db.rollback()
        else:
            db.commit()

        offset += BATCH_SIZE

    return report


def reencrypt_table(
    db: Session, *, table: str, dry_run: bool = True
) -> RotationReport:
    """Every encrypted column of one table, as one report."""
    report = RotationReport(table=table)
    for target in ENCRYPTED_COLUMNS:
        if target.table != table:
            continue
        part = reencrypt_column(db, target=target, dry_run=dry_run)
        report.scanned += part.scanned
        report.already_head += part.already_head
        report.rotated += part.rotated
        report.failed += part.failed
        report.failed_ids.extend(f"{target.column}:{row_id}" for row_id in part.failed_ids)
    return report


def reencrypt_all(
    db: Session, *, dry_run: bool = True
) -> list[RotationReport]:
    logger.info(
        "ARCH07_ROTATION_START | head_key_fingerprint=%s dry_run=%s",
        head_key_fingerprint(), dry_run,
    )
    reports = [
        reencrypt_column(db, target=target, dry_run=dry_run)
        for target in ENCRYPTED_COLUMNS
    ]
    logger.info(
        "ARCH07_ROTATION_END | rotated=%d failed=%d",
        sum(r.rotated for r in reports),
        sum(r.failed for r in reports),
    )
    return reports


#: The name the CLI and the ARCH-07 tests were written against; it now covers every secret.
reencrypt_all_smtp_passwords = reencrypt_all
