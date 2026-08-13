"""SMTP credential re-encryption (ARCH-07 Step 9, §B.5).

Re-encrypts every stored credential under the head key so an old key can be
dropped from EMAIL_ENCRYPTION_KEYS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.encryption import (
    DecryptionError,
    decrypting_key_index,
    head_key_fingerprint,
    rotate_ciphertext,
)

logger = logging.getLogger("app.services.encryption_rotation")

TARGET_TABLES: Sequence[str] = ("email_settings", "organization_email_settings")
BATCH_SIZE = 100


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


def reencrypt_table(
    db: Session, *, table: str, dry_run: bool = True
) -> RotationReport:
    report = RotationReport(table=table)
    offset = 0

    while True:
        rows = db.execute(
            text(
                f"""
                SELECT id, encrypted_password
                  FROM {table}
                 WHERE encrypted_password IS NOT NULL
                   AND encrypted_password <> ''
                 ORDER BY id
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
                index = decrypting_key_index(row.encrypted_password)
            except Exception:
                logger.exception("%s id=%s: key index probe failed", table, row.id)
                report.failed += 1
                report.failed_ids.append(str(row.id))
                continue

            if index is None:
                logger.error(
                    "ARCH07_UNDECRYPTABLE | table=%s id=%s — no configured key "
                    "decrypts this ciphertext. Row left untouched.",
                    table, row.id,
                )
                report.failed += 1
                report.failed_ids.append(str(row.id))
                continue

            if index == 0:
                report.already_head += 1
                continue

            if dry_run:
                report.rotated += 1
                continue

            try:
                new_token = rotate_ciphertext(row.encrypted_password)
            except (DecryptionError, ValueError):
                logger.exception("%s id=%s: rotation failed", table, row.id)
                report.failed += 1
                report.failed_ids.append(str(row.id))
                continue

            db.execute(
                text(
                    f"UPDATE {table} SET encrypted_password = :token "
                    f"WHERE id = :row_id"
                ),
                {"token": new_token, "row_id": row.id},
            )
            report.rotated += 1

        if dry_run:
            db.rollback()
        else:
            db.commit()

        offset += BATCH_SIZE

    return report


def reencrypt_all_smtp_passwords(
    db: Session, *, dry_run: bool = True
) -> list[RotationReport]:
    logger.info(
        "ARCH07_ROTATION_START | head_key_fingerprint=%s dry_run=%s",
        head_key_fingerprint(), dry_run,
    )
    reports = [
        reencrypt_table(db, table=table, dry_run=dry_run)
        for table in TARGET_TABLES
    ]
    logger.info(
        "ARCH07_ROTATION_END | rotated=%d failed=%d",
        sum(r.rotated for r in reports),
        sum(r.failed for r in reports),
    )
    return reports