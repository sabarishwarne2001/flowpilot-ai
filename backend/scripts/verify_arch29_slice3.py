from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "frontend" / "src"
BACKEND = REPO_ROOT / "backend"

AUTOMATION_SCHEMA = BACKEND / "app" / "schemas" / "automation.py"
AUTOMATION_TYPES = SRC / "types" / "automation.ts"
AUTOMATION_PAGE = SRC / "pages" / "Automation" / "Automation.tsx"
AUDIT_INSPECTOR = SRC / "components" / "organization" / "AuditDetailInspector.tsx"
AUDIT_PAGE = SRC / "pages" / "admin" / "AuditExplorer.tsx"
GRANTS_PANEL = SRC / "pages" / "Settings" / "MyWorkspaceGrantsPanel.tsx"
PROFILE_PAGE = SRC / "pages" / "Settings" / "ProfileSettings.tsx"
ORG_DETAIL_PANEL = SRC / "components" / "organization" / "OrganizationDetailPanel.tsx"
ORG_GENERAL_PAGE = SRC / "pages" / "organization" / "OrganizationGeneral.tsx"

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?<![:/])//[^\n]*")

def strip_comments(source: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))

def read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8-sig")

def class_fields(source: str, class_name: str) -> list[str]:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign)
                and isinstance(item.target, ast.Name)
            ]
    raise LookupError(f"class not found: {class_name}")

def extract_balanced(source: str, start_pattern: str) -> str:
    match = re.search(start_pattern, source)
    if match is None:
        raise LookupError(f"region not found: {start_pattern}")
    start = match.start()
    depth = 0
    seen = False
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
            seen = True
        elif char == "}":
            depth -= 1
            if seen and depth == 0:
                return source[start : index + 1]
    return source[start:]

def extract_declaration(source: str, name: str) -> str:
    match = re.search(rf"^const {re.escape(name)}\b", source, re.M)
    if match is None:
        raise LookupError(f"declaration not found: {name}")
    end = source.find("\n};", match.start())
    return source[match.start() : end + 3] if end != -1 else source[match.start() :]

@dataclass
class Result:
    check: str
    passed: bool
    detail: str

def ok(name: str, condition: bool, good: str, bad: str) -> Result:
    return Result(name, condition, good if condition else bad)

def check_g1_response_exposes_both() -> Result:
    fields = class_fields(read(AUTOMATION_SCHEMA), "AutomationRuleResponse")
    present = {"graph_version", "on_error"} <= set(fields)
    return ok("G1  response exposes graph_version and on_error", present, "both declared on AutomationRuleResponse", f"missing from AutomationRuleResponse; has {fields}")

def check_g2_base_does_not_expose_either() -> Result:
    fields = set(class_fields(read(AUTOMATION_SCHEMA), "AutomationRuleBase"))
    leaked = fields & {"graph_version", "on_error"}
    return ok("G2  request base does NOT expose them", not leaked, "AutomationRuleBase unchanged", f"{sorted(leaked)} leaked onto AutomationRuleBase")

def check_g3_frontend_type_matches() -> Result:
    code = strip_comments(read(AUTOMATION_TYPES))
    try:
        rule = extract_balanced(code, r"export interface AutomationRule\b")
    except LookupError as exc:
        return Result("G3  frontend DTO carries both fields", False, str(exc))
    both = "graph_version" in rule and "on_error" in rule
    return ok("G3  frontend DTO carries both fields", both, "AutomationRule declares both", "frontend type missing fields")

def check_g4_fields_are_optional() -> Result:
    code = strip_comments(read(AUTOMATION_TYPES))
    try:
        rule = extract_balanced(code, r"export interface AutomationRule\b")
    except LookupError as exc:
        return Result("G4  both fields are optional", False, str(exc))
    optional = (
        re.search(r"graph_version\?\s*:", rule) is not None
        and re.search(r"on_error\?\s*:", rule) is not None
    )
    return ok("G4  both fields are optional", optional, "both fields optional", "fields declared required")

def check_g5_undefined_policy_renders_nothing() -> Result:
    code = strip_comments(read(AUTOMATION_PAGE))
    try:
        badge = extract_declaration(code, "RuleErrorPolicyBadge")
    except LookupError as exc:
        return Result("G5  unknown policy renders nothing", False, str(exc))
    guarded = re.search(r"if\s*\(\s*!policy\s*\)\s*\{\s*return null", badge) is not None
    return ok("G5  unknown policy renders nothing", guarded, "unreported policy renders no badge", "unreported policy defaults to badge")

def check_g6_continue_is_distinguished() -> Result:
    code = strip_comments(read(AUTOMATION_PAGE))
    try:
        badge = extract_declaration(code, "RuleErrorPolicyBadge")
    except LookupError as exc:
        return Result("G6  CONTINUE is visually distinct", False, str(exc))
    distinct = re.search(r'policy\s*===\s*"CONTINUE"', badge) is not None and "amber" in badge
    return ok("G6  CONTINUE is visually distinct", distinct, "continue-on-failure called out in amber", "policies render identically")

def check_g7_badge_is_mounted() -> Result:
    code = strip_comments(read(AUTOMATION_PAGE))
    return ok("G7  policy badge is mounted", "<RuleErrorPolicyBadge" in code, "rendered on rule card", "not mounted")

def check_g8_inspector_makes_no_request() -> Result:
    code = strip_comments(read(AUDIT_INSPECTOR))
    fetches = "getAuditLog" in code or "useQuery" in code
    return ok("G8  audit inspector makes no network call", not fetches, "renders from row in hand", "makes network request")

def check_g9_inspector_renders_all_three() -> Result:
    code = strip_comments(read(AUDIT_INSPECTOR))
    missing = [
        field
        for field in ("details", "ip_address", "user_agent")
        if re.search(rf"entry\.{field}\s*(?:&&|\?)", code) is None
    ]
    return ok("G9  inspector renders metadata, IP and user agent", not missing, "all three fields shown", f"still missing: {missing}")

def check_g10_inspector_mounted() -> Result:
    code = strip_comments(read(AUDIT_PAGE))
    return ok("G10 audit inspector is mounted", "<AuditDetailInspector" in code, "mounted in AuditExplorer", "not mounted")

def check_g11_empty_grants_explained() -> Result:
    code = strip_comments(read(GRANTS_PANEL))
    try:
        empty_branch = re.search(r"items\.length === 0 \?(.*?):\s*\(", code, re.S)
    except LookupError as exc:
        return Result("G11 empty grants explain themselves", False, str(exc))
    if empty_branch is None:
        return Result("G11 empty grants explain themselves", False, "no empty-state branch")
    explains = re.search(r"reachable\s*!==\s*null", empty_branch.group(1)) is not None
    return ok("G11 empty grants explain themselves", explains, "empty state names reachable count", "bare empty state shown")

def check_g12_legal_name_not_defaulted() -> Result:
    code = strip_comments(read(ORG_DETAIL_PANEL))
    offenders = re.findall(r"legal_name\s*(?:\?\?|\|\|)\s*[\w.]*name", code)
    mounted = "<OrganizationDetailPanel" in strip_comments(read(ORG_GENERAL_PAGE))
    condition = not offenders and mounted
    return ok("G12 legal_name is not defaulted, and the panel is mounted", condition, "unset legal name renders as unset; panel mounted", f"defaulting found: {offenders}")

CHECKS = [
    check_g1_response_exposes_both,
    check_g2_base_does_not_expose_either,
    check_g3_frontend_type_matches,
    check_g4_fields_are_optional,
    check_g5_undefined_policy_renders_nothing,
    check_g6_continue_is_distinguished,
    check_g7_badge_is_mounted,
    check_g8_inspector_makes_no_request,
    check_g9_inspector_renders_all_three,
    check_g10_inspector_mounted,
    check_g11_empty_grants_explained,
    check_g12_legal_name_not_defaulted,
]

def run() -> int:
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:
            results.append(Result(check.__name__, False, f"check raised: {exc!r}"))
    width = max(len(r.check) for r in results)
    for r in results:
        print(f"[{'PASS' if r.passed else 'FAIL'}] {r.check.ljust(width)}  {r.detail}")
    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutate", action="store_true", help="self-test")
    args = parser.parse_args()
    return run()

if __name__ == "__main__":
    raise SystemExit(main())
