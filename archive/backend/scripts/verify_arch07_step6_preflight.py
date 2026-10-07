#!/usr/bin/env python
"""ARCH-07 Step 6 pre-flight. Populates the logo adoption staging table.

Usage:
    python scripts/verify_arch07_step6_preflight.py
    python scripts/verify_arch07_step6_preflight.py --write
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path, PurePosixPath

from sqlalchemy import text

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from app.core.storage import LocalStorageDriver
from app.db.session import SessionLocal


STAGING_DDL = """
CREATE TABLE IF NOT EXISTS arch07_logo_adoption_staging (
    storage_key      text PRIMARY KEY,
    workspace_id     uuid NOT NULL,
    organization_id  uuid NOT NULL,
    legacy_url       text NOT NULL,
    mime_type        text NOT NULL,
    size_bytes       bigint NOT NULL,
    checksum_sha256  text NOT NULL,
    captured_at      timestamptz NOT NULL DEFAULT now()
);
"""


EXTENSION_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


LEGACY_PREFIX = "/uploads/"


def _key_from_legacy_url(url: str | None) -> str | None:
    if not url or not url.startswith(LEGACY_PREFIX):
        return None

    return url[len(LEGACY_PREFIX):]


def main() -> int:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--write",
        action="store_true",
        help="Populate the staging table. Without it, report only.",
    )

    args = parser.parse_args()

    driver = LocalStorageDriver(
        root=Path(settings.UPLOAD_DIR)
    )

    session = SessionLocal()

    try:
        referenced = session.execute(
            text(
                """
                SELECT
                    w.id AS workspace_id,
                    w.organization_id,
                    w.company_logo_url
                FROM workspaces w
                WHERE w.company_logo_url IS NOT NULL
                """
            )
        ).all()

        tracked_keys = {
            row.file_path.removeprefix(LEGACY_PREFIX)
            for row in session.execute(
                text(
                    """
                    SELECT file_path
                    FROM uploaded_files
                    WHERE deleted_at IS NULL
                    """
                )
            ).all()
        }

        on_disk = set(
            driver.iter_keys("logos")
        )

        adoptions: list[dict[str, object]] = []
        missing: list[str] = []

        for row in referenced:

            key = _key_from_legacy_url(
                row.company_logo_url
            )

            if key is None:
                continue

            if key in tracked_keys:
                continue

            if key not in on_disk:

                missing.append(
                    f"{row.workspace_id} -> {key}"
                )

                continue

            extension = (
                PurePosixPath(key)
                .suffix
                .lstrip(".")
                .lower()
            )

            mime = EXTENSION_TO_MIME.get(
                extension
            )

            if mime is None:

                print(
                    f"[WARN] Unknown logo extension "
                    f"{extension!r} for key {key}. Skipping."
                )

                continue

            data = driver.get(key)

            adoptions.append(
                {
                    "storage_key": key,
                    "workspace_id": str(row.workspace_id),
                    "organization_id": str(
                        row.organization_id
                    ),
                    "legacy_url": row.company_logo_url,
                    "mime_type": mime,
                    "size_bytes": len(data),
                    "checksum_sha256": hashlib.sha256(
                        data
                    ).hexdigest(),
                }
            )

        adopt_keys = {
            item["storage_key"]
            for item in adoptions
        }

        orphans = sorted(
            on_disk - adopt_keys - tracked_keys
        )

        print(
            f"logo objects on disk      : {len(on_disk)}"
        )

        print(
            f"referenced by a workspace : {len(referenced)}"
        )

        print(
            f"already tracked           : "
            f"{len(tracked_keys & on_disk)}"
        )

        print(
            f"to adopt                  : {len(adoptions)}"
        )

        print(
            f"orphans (quarantine)      : {len(orphans)}"
        )

        print(
            f"MISSING from disk         : {len(missing)}"
        )

        for entry in missing:

            print(
                f"  [WARN MISSING ON DISK] {entry}"
            )

        if args.write:

            session.execute(
                text(STAGING_DDL)
            )

            session.execute(
                text(
                    "TRUNCATE TABLE arch07_logo_adoption_staging"
                )
            )

            if adoptions:

                session.execute(
                    text(
                        """
                        INSERT INTO arch07_logo_adoption_staging
                        (
                            storage_key,
                            workspace_id,
                            organization_id,
                            legacy_url,
                            mime_type,
                            size_bytes,
                            checksum_sha256
                        )
                        VALUES
                        (
                            :storage_key,
                            CAST(:workspace_id AS uuid),
                            CAST(:organization_id AS uuid),
                            :legacy_url,
                            :mime_type,
                            :size_bytes,
                            :checksum_sha256
                        )
                        """
                    ),
                    adoptions,
                )

            session.commit()

            print(
                f"\n[OK] staged {len(adoptions)} adoptions."
            )

        else:

            print(
                "\n[DRY RUN] pass --write to populate "
                "the staging table."
            )

        print(
            "[PASS] Step 6 pre-flight"
        )

        return 0

    except Exception as exc:

        session.rollback()

        print(
            f"\n[FAIL] Step 6 pre-flight failed: {exc}"
        )

        return 1

    finally:

        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
