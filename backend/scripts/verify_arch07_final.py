#!/usr/bin/env python
"""ARCH-07 final phase gate. Exit 0 = Phase ARCH-07 is 100% complete and closeable.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))

from app.db.session import SessionLocal

REPO_ROOT = backend_dir.parent
APP_ROOT = backend_dir / "app"

EXPECTED_HEAD = "b6e1d94f07ca"
BASELINE_AUDIT_SITES = 33
EXPECTED_RESIDUAL_SITES = 6
MAX_CIPHERTEXT = 512

failures: list[str] = []
notes: list[str] = []


def check(criterion: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [PASS] {criterion}")
    else:
        print(f"  [FAIL] {criterion}: {detail}")
        failures.append(f"{criterion}: {detail}")


def main() -> int:
    session = SessionLocal()
    try:
        # ==================================================================
        print("\nA. Import decoupling (E1, §B.10)")
        # ==================================================================
        script = (
            "import sys, app.main; "
            "mods = [m for m in sys.modules if m.split('.')[0] in "
            "{'paddleocr','paddle','sentence_transformers','chromadb'}]; "
            "print('MODS:' + ','.join(sorted(mods)))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=180, check=False,
        )
        stdout_lines = result.stdout.strip().splitlines()
        mods_line = [l for l in stdout_lines if l.startswith("MODS:")]
        loaded = mods_line[0].removeprefix("MODS:") if mods_line else ""

        check("E1  import app.main pulls no heavy ML modules",
              result.returncode == 0 and not loaded, loaded or result.stderr[:200])

        # ==================================================================
        print("\nB. Audit conversion (E6, §B.1/§B.6)")
        # ==================================================================
        marker = re.compile(r"AUDIT\s*\|")
        dynamic_free = True
        residual: list[str] = []
        for path in sorted(APP_ROOT.rglob("*.py")):
            if str(path.relative_to(APP_ROOT.parent)).replace("\\", "/") == "app/models/audit_log.py":
                continue
            source = path.read_text(encoding="utf-8", errors="ignore")
            if "AUDIT" not in source:
                continue
            for index, line in enumerate(source.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("*") or stripped.startswith('"""'):
                    continue
                if marker.search(line):
                    residual.append(f"{path.name}:{index}")
                    if not re.search(r"AUDIT\s*\|\s*[A-Z0-9_]+\s*\|", line):
                        dynamic_free = False

        check("E6  residual AUDIT sites accounted for",
              len(residual) <= EXPECTED_RESIDUAL_SITES,
              f"found {len(residual)}: {residual}")
        check("E6  no dynamic audit event names survive", dynamic_free,
              "an event name is still interpolated")

        # ==================================================================
        print("\nC. Audit table shape (E2, §B.4)")
        # ==================================================================
        org_nullable = session.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name='audit_logs' AND column_name='organization_id'"
            )
        ).scalar_one_or_none()
        check("E2  audit_logs.organization_id is NOT NULL",
              org_nullable == "NO", f"is_nullable={org_nullable}")

        actor_ondelete = session.execute(
            text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conrelid='audit_logs'::regclass AND contype='f' "
                "AND conname LIKE '%actor_id%'"
            )
        ).scalar_one_or_none()
        check("E2  audit_logs.actor_id is ON DELETE SET NULL",
              actor_ondelete == "n", f"confdeltype={actor_ondelete!r}")

        # ==================================================================
        print("\nD. Objects autogenerate cannot see (E7, E22, §B.3)")
        # ==================================================================
        triggers = set(session.execute(
            text(
                "SELECT tgname FROM pg_trigger "
                "WHERE tgrelid='audit_logs'::regclass AND NOT tgisinternal"
            )
        ).scalars().all())
        for name in ("trg_audit_logs_immutable", "trg_audit_logs_no_truncate"):
            check(f"E7  trigger {name} present", name in triggers,
                  f"present: {sorted(triggers)}")

        malformed = session.execute(
            text(r"SELECT count(*) FROM pg_constraint "
                 r"WHERE contype='c' AND conname LIKE 'ck\_%\_ck\_%'")
        ).scalar_one()
        check("A.1.6 no malformed ck_%_ck_% constraint names", malformed == 0,
              f"{malformed} found")

        # ==================================================================
        print("\nE. Storage & Encryption (E10-E17, §B.5, §B.8, §B.9)")
        # ==================================================================
        check("E13 legacy key shim deleted",
              not (APP_ROOT / "core" / "storage" / "keys.py").exists())

        from app.main import app as fastapi_app
        mounted = [
            getattr(route, "path", "") for route in fastapi_app.routes
            if getattr(route, "path", "").startswith("/uploads")
        ]
        check("E13 /uploads is not mounted", not mounted, str(mounted))

        from app.core.encryption import (
            configured_key_count,
            decrypt_password,
            head_key_fingerprint,
        )

        undecryptable = []
        for table in ("email_settings", "organization_email_settings"):
            rows = session.execute(
                text(
                    f"SELECT id, encrypted_password FROM {table} "
                    f"WHERE encrypted_password IS NOT NULL "
                    f"AND encrypted_password <> ''"
                )
            ).all()
            for row in rows:
                try:
                    decrypt_password(row.encrypted_password)
                except Exception:
                    undecryptable.append(f"{table}:{row.id}")

        check("E16 zero undecryptable ciphertexts", not undecryptable,
              str(undecryptable))

        # ==================================================================
        print("\nF. Phase hygiene")
        # ==================================================================
        staging = session.execute(
            text("SELECT to_regclass('public.arch07_logo_adoption_staging')")
        ).scalar()
        check("Step 12 staging table dropped", staging is None,
              "run scripts/archive_logo_adoption_staging.py then "
              "`alembic -x archived=1 upgrade head`")

        head = session.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        check(f"alembic head == {EXPECTED_HEAD}", head == EXPECTED_HEAD,
              f"head={head}")

    finally:
        session.close()

    print("\n" + "=" * 75)
    if failures:
        print(f"\n[FAIL] ARCH-07 phase gate: {len(failures)} criteria unmet.\n")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("\n[PASS] ARCH-07 final phase gate. All machine-checkable criteria met!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
