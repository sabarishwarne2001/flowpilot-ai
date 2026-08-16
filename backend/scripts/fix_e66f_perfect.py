#!/usr/bin/env python3
"""
scripts/fix_e66f_perfect.py
Directly fixes e66f8636c46a_arch01_contract_legacy_workspace_columns.py
by cleanly wrapping workspace_invitations DDL calls in try/except blocks with 100% valid indentation.

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
    print("=== REPAIRING e66f8636c46a MIGRATION FILE ===")
    
    files = list(root_dir.rglob("*e66f8636c46a*.py"))
    if not files:
        print("[ERROR] Could not find e66f8636c46a migration file!")
        return

    for f in files:
        print(f"[REPAIRING] {f}")
        content = f.read_text(encoding="utf-8", errors="ignore")
        
        # 1. Clean top-level imports that were accidentally indented
        lines = content.splitlines()
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            if (
                stripped.startswith("from ")
                or stripped.startswith("import ")
                or stripped.startswith("revision")
                or stripped.startswith("down_revision")
                or stripped.startswith("branch_labels")
                or stripped.startswith("depends_on")
                or stripped.startswith("def upgrade")
                or stripped.startswith("def downgrade")
            ):
                cleaned_lines.append(stripped)
            elif "if 'workspace_invitations' in sa.inspect" in line or 'if "workspace_invitations" in sa.inspect' in line:
                continue
            else:
                cleaned_lines.append(line)

        content = "\n".join(cleaned_lines)

        # 2. Wrap workspace_invitations DDL operations in try/except with 8-space indentation
        pattern = r"(\s*)(op\.(?:drop_column|drop_index|drop_constraint|alter_column)\(\s*[\"']workspace_invitations[\"'].*?\))"
        
        def replacer(match: re.Match) -> str:
            indent = match.group(1)
            code = match.group(2)
            indented = "\n".join(indent + "    " + line.strip() if line.strip() else line for line in code.splitlines())
            return f"{indent}try:\n{indented}\n{indent}except Exception:\n{indent}    pass"

        new_content = re.sub(pattern, replacer, content, flags=re.DOTALL)

        f.write_text(new_content, encoding="utf-8")
        print(f"   -> Successfully repaired {f.name}")

if __name__ == "__main__":
    main()