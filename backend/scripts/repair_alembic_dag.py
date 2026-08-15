#!/usr/bin/env python3
"""
scripts/repair_alembic_dag.py
Repairs Alembic migration graph by re-linking down_revision from bb57122ca97e to b13c7b21bec9.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import re
import sys
from pathlib import Path

# Safeguard Windows stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")


def main() -> None:
    root_dir = Path(__file__).parent.parent.resolve()
    migration_dirs = [
        root_dir / "alembic" / "versions",
        root_dir / "app" / "db" / "migrations" / "versions",
        root_dir / "migrations" / "versions",
    ]

    repaired_files = 0
    for m_dir in migration_dirs:
        if not m_dir.exists():
            continue

        for p in m_dir.rglob("*.py"):
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                if "bb57122ca97e" in content:
                    print(f"[REPAIR] Found reference to bb57122ca97e in: {p.name}")
                    # Replace down_revision referencing bb57122ca97e with b13c7b21bec9
                    new_content = re.sub(
                        r"down_revision\s*=\s*['\"]bb57122ca97e['\"]",
                        "down_revision = 'b13c7b21bec9'",
                        content,
                    )
                    if new_content != content:
                        p.write_text(new_content, encoding="utf-8")
                        print(f"[REPAIR] Successfully re-linked {p.name} down_revision to 'b13c7b21bec9'")
                        repaired_files += 1
            except Exception as e:
                print(f"[ERROR] Could not inspect file {p}: {e}")

    if repaired_files == 0:
        print("[REPAIR] No direct string matches found for down_revision = 'bb57122ca97e'. Check manually.")
    else:
        print(f"[REPAIR] Re-linked {repaired_files} file(s).")


if __name__ == "__main__":
    main()