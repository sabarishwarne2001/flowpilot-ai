#!/usr/bin/env python
"""Quarantine orphaned upload objects (ARCH-07 Step 6, R7).

Usage:
    python scripts/quarantine_orphan_uploads.py --dry-run
    python scripts/quarantine_orphan_uploads.py --apply
    python scripts/quarantine_orphan_uploads.py --restore <manifest.json>
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.config import settings
from app.core.storage import LocalStorageDriver
from app.db.session import SessionLocal

LEGACY_PREFIX = "/uploads/"
MANIFEST_DIR = Path("arch07_quarantine_manifests")


def _protected_keys(session) -> set[str]:
    protected = {
        row.file_path.removeprefix(LEGACY_PREFIX)
        for row in session.execute(
            text("SELECT file_path FROM uploaded_files WHERE deleted_at IS NULL")
        ).all()
    }
    protected |= {
        row.company_logo_url.removeprefix(LEGACY_PREFIX)
        for row in session.execute(
            text(
                "SELECT company_logo_url FROM workspaces "
                "WHERE company_logo_url IS NOT NULL"
            )
        ).all()
        if row.company_logo_url.startswith(LEGACY_PREFIX)
    }
    protected |= {
        row.file_path.removeprefix(LEGACY_PREFIX)
        for row in session.execute(
            text("SELECT file_path FROM uploaded_files WHERE deleted_at IS NOT NULL")
        ).all()
    }
    return protected


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--restore", metavar="MANIFEST")
    parser.add_argument("--prefix", default="logos")
    args = parser.parse_args()

    driver = LocalStorageDriver(root=Path(settings.UPLOAD_DIR))
    quarantine_root = Path(settings.STORAGE_QUARANTINE_DIR).resolve()

    if args.restore:
        manifest = json.loads(Path(args.restore).read_text())
        restored = 0
        for entry in manifest["moved"]:
            source = Path(entry["quarantined_to"])
            target = Path(entry["original_path"])
            if source.exists() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                restored += 1
        print(f"[OK] restored {restored} of {len(manifest['moved'])} objects.")
        return 0

    session = SessionLocal()
    try:
        protected = _protected_keys(session)
    finally:
        session.close()

    on_disk = set(driver.iter_keys(args.prefix))
    orphans = sorted(on_disk - protected)

    print(f"objects under '{args.prefix}/' : {len(on_disk)}")
    print(f"protected                     : {len(on_disk & protected)}")
    print(f"orphans to quarantine         : {len(orphans)}")

    if args.dry_run:
        for key in orphans:
            print(f"  [WOULD MOVE] {key}")
        print("\n[DRY RUN] nothing moved. Re-run with --apply.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination_root = quarantine_root / stamp
    moved: list[dict] = []

    for key in orphans:
        source = driver.root / key
        target = destination_root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved.append(
            {
                "storage_key": key,
                "original_path": str(source),
                "quarantined_to": str(target),
            }
        )

    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = MANIFEST_DIR / f"quarantine-{stamp}.json"
    manifest_path.write_text(
        json.dumps(
            {
                "quarantined_at": stamp,
                "prefix": args.prefix,
                "protected_count": len(on_disk & protected),
                "moved": moved,
            },
            indent=2,
        )
    )

    print(f"\n[OK] quarantined {len(moved)} objects.")
    print(f"manifest: {manifest_path}")
    print(f"restore : python {sys.argv[0]} --restore {manifest_path}")
    print("\nE12: 0 objects deleted. Every one is recoverable from the manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
