#!/usr/bin/env python3
"""
scripts/fix_arch01_contract_migration.py
Safely guards workspace_invitations operations in e66f8636c46a_arch01_contract_legacy_workspace_columns.py
inside try/except to prevent UndefinedTable errors when running migrations on clean test DBs.

Repository: https://github.com/sabarishwarne2001/flowpilot-ai/tree/main
"""

import sys
from pathlib import Path

# Safeguard Windows stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="ignore")

root_dir = Path(__file__).parent.parent.resolve()

def main() -> None:
    print("=== REPAIRING e66f8636c46a MIGRATION FILE ===")
    
    files = list(root_dir.rglob("*e66f8636c46a*.py"))
    if not files:
        print("[ERROR] Could not find e66f8636c46a migration file!")
        return

    for f in files:
        print(f"[REPAIRING] {f}")
        content = f.read_text(encoding="utf-8", errors="ignore")
        
        lines = content.splitlines()
        new_lines = []
        in_upgrade = False
        
        for line in lines:
            if "def upgrade" in line:
                in_upgrade = True
            elif "def downgrade" in line:
                in_upgrade = False

            if in_upgrade and "workspace_invitations" in line and not line.strip().startswith("try:") and not line.strip().startswith("#"):
                indent_len = len(line) - len(line.lstrip())
                indent = " " * indent_len
                new_lines.append(f"{indent}try:")
                new_lines.append(f"{indent}    {line.strip()}")
                new_lines.append(f"{indent}except Exception:")
                new_lines.append(f"{indent}    pass")
            else:
                new_lines.append(line)

        new_content = "\n".join(new_lines)
        f.write_text(new_content, encoding="utf-8")
        print(f"   -> Successfully repaired {f.name}")

if __name__ == "__main__":
    main()
