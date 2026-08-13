#!/usr/bin/env python
"""ARCH-07 Step 9 — re-encryption sweeper CLI.

Usage:
    python scripts/reencrypt_smtp_passwords.py --dry-run
    python scripts/reencrypt_smtp_passwords.py --apply --i-have-a-verified-backup
    python scripts/reencrypt_smtp_passwords.py --verify
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.core.encryption import configured_key_count, head_key_fingerprint
from app.db.session import SessionLocal
from app.services.encryption_rotation_service import (
    TARGET_TABLES,
    reencrypt_all_smtp_passwords,
    reencrypt_table,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    group.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--i-have-a-verified-backup", action="store_true",
        help="Required with --apply. Asserts §G sign-off 4 has been met.",
    )
    args = parser.parse_args()

    try:
        keys = configured_key_count()
        fingerprint = head_key_fingerprint()
    except Exception as exc:
        print(f"[FAIL] Encryption is not configured: {exc}")
        return 2

    print(f"configured keys      : {keys}")
    print(f"head key fingerprint : {fingerprint}")

    if args.apply:
        if keys < 2:
            print(
                "[FAIL] Only one key is configured, so there is nothing to "
                "rotate FROM. Prepend the new key to EMAIL_ENCRYPTION_KEYS "
                "and deploy before running --apply."
            )
            return 2
        if not args.i_have_a_verified_backup:
            print(
                "[FAIL] --apply requires --i-have-a-verified-backup."
            )
            return 2

    session = SessionLocal()
    try:
        if args.verify:
            reports = [
                reencrypt_table(session, table=table, dry_run=True)
                for table in TARGET_TABLES
            ]
            for report in reports:
                print(report)
            pending = sum(r.rotated for r in reports)
            failed = sum(r.failed for r in reports)
            if failed:
                print(f"\n[FAIL] E16: {failed} rows decrypt under NO key.")
                return 1
            if pending:
                print(
                    f"\n[FAIL] E17: {pending} rows are still under a non-head key."
                )
                return 1
            print("\n[PASS] E16 + E17: every row is under the head key.")
            return 0

        reports = reencrypt_all_smtp_passwords(session, dry_run=args.dry_run)
        for report in reports:
            print(report)

        failed = sum(r.failed for r in reports)
        rotated = sum(r.rotated for r in reports)

        if failed:
            print(f"\n[FAIL] {failed} rows could not be decrypted:")
            for report in reports:
                for row_id in report.failed_ids:
                    print(f"  {report.table} id={row_id}")
            return 1

        if args.dry_run:
            print(f"\n[DRY RUN] {rotated} rows would be re-encrypted.")
        else:
            print(f"\n[OK] {rotated} rows re-encrypted under key {fingerprint}.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())