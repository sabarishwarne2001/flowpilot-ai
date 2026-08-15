#!/usr/bin/env python3
"""
scripts/fix_migration_workspace_invitations.py
Safely guards workspace_invitations table alterations in early migrations
to prevent UndefinedTable errors when running alembic upgrade head against clean or test databases.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import sys
import re
from pathlib import Path

# Safeguard Windows stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

root_dir = Path(__file__).parent.parent.resolve()

def main() -> None:
    print("=== REPAIRING EARLY MIGRATION WORKSPACE_INVITATIONS REFERENCES ===")
    
    migration_files = list(root_dir.rglob("*4fb2e9a4f15c*.py"))
    if not migration_files:
        migration_files = list(root_dir.rglob("*.py"))

    repaired = 0
    for f in migration_files:
        if "alembic" not in str(f) and "migrations" not in str(f):
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
            if "workspace_invitations" in content and "op.add_column('workspace_invitations'" in content:
                print(f"[INSPECTING] {f.name}")
                old_str1 = "op.add_column('workspace_invitations'"
                new_str1 = "if 'workspace_invitations' in sa.inspect(op.get_bind()).get_table_names():\n        op.add_column('workspace_invitations'"
                
                old_str2 = 'op.add_column("workspace_invitations"'
                new_str2 = 'if "workspace_invitations" in sa.inspect(op.get_bind()).get_table_names():\n        op.add_column("workspace_invitations"'

                new_content = content.replace(old_str1, new_str1).replace(old_str2, new_str2)
                if new_content != content:
                    f.write_text(new_content, encoding="utf-8")
                    print(f"   -> Successfully guarded workspace_invitations in {f.name}")
                    repaired += 1
        except Exception as e:
            print(f"[ERROR] Could not repair {f}: {e}")

    print(f"\nRepaired {repaired} migration file(s).")

if __name__ == "__main__":
    main()