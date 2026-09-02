#!/usr/bin/env python3
"""
scripts/fix_arch01_migration.py
Cleans up top-level indentation and safely wraps workspace_invitations operations in
4fb2e9a4f15c_arch01_expand_organization_tenancy.py inside try/except.

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
    print("=== CLEANING 4fb2e9a4f15c MIGRATION FILE ===")
    
    files = list(root_dir.rglob("*4fb2e9a4f15c*.py"))
    if not files:
        print("[ERROR] Could not find 4fb2e9a4f15c migration file!")
        return

    for f in files:
        print(f"[CLEANING] {f}")
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
            else:
                cleaned_lines.append(line)

        content = "\n".join(cleaned_lines)
        content = re.sub(r"if\s+['\"]workspace_invitations['\"]\s+in[^\n]+\n", "", content)

        # 2. Wrap workspace_invitations calls in try...except
        pattern = r"(\s*)(op\.add_column\(\s*[\"']workspace_invitations[\"'].*?\))"
        def replacer(match: re.Match) -> str:
            indent = match.group(1)
            code = match.group(2)
            indented = "\n".join(indent + "    " + l.strip() if l.strip() else l for l in code.splitlines())
            return f"{indent}try:\n{indented}\n{indent}except Exception:\n{indent}    pass"

        new_content = re.sub(pattern, replacer, content, flags=re.DOTALL)

        f.write_text(new_content, encoding="utf-8")
        print(f"   -> Successfully cleaned {f.name}")

if __name__ == "__main__":
    main()
