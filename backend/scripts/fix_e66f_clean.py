#!/usr/bin/env python3
"""
scripts/fix_e66f_clean.py
Guards workspace_invitations operations in e66f8636c46a_arch01_contract_legacy_workspace_columns.py
with explicit table inspection so PostgreSQL transactions never abort.

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
        print("[ERROR] Could not find e66f8636c46a file!")
        return

    repaired = 0
    for f in files:
        print(f"[REPAIRING] {f}")
        content = f.read_text(encoding="utf-8", errors="ignore")
        
        # Replace try/except wrappers around workspace_invitations with explicit inspection check
        lines = content.splitlines()
        new_lines = []
        in_upgrade = False
        
        for line in lines:
            if "def upgrade" in line:
                in_upgrade = True
            elif "def downgrade" in line:
                in_upgrade = False

            if in_upgrade and "workspace_invitations" in line and ("op.drop_" in line or "op.alter_" in line or "op.add_" in line):
                indent_len = len(line) - len(line.lstrip())
                indent = " " * indent_len
                new_lines.append(f"{indent}if 'workspace_invitations' in sa.inspect(op.get_bind()).get_table_names():")
                new_lines.append(f"{indent}    {line.strip()}")
            elif "try:" in line or "except Exception:" in line or (line.strip() == "pass" and "workspace_invitations" in content):
                continue
            else:
                new_lines.append(line)

        new_content = "\n".join(new_lines)
        f.write_text(new_content, encoding="utf-8")
        print(f"   -> Successfully updated {f.name}")
        repaired += 1

    print(f"\nRepaired {repaired} file(s).")

if __name__ == "__main__":
    main()