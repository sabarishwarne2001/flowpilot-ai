from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "frontend" / "src"

COMPLIANCE_PAGE = SRC / "pages" / "organization" / "OrganizationCompliance.tsx"
PREVIEW_PANEL = SRC / "components" / "organization" / "ErasureImpactPreview.tsx"
DASHBOARD_API = SRC / "services" / "api" / "dashboard.ts"
BRANDING_API = SRC / "services" / "api" / "branding.ts"
ENDPOINTS = SRC / "services" / "api" / "endpoints.ts"
QUERY_KEYS = SRC / "services" / "api" / "queryKeys.ts"

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?<![:/])//[^\n]*")

def strip_comments(source: str) -> str:
    without_blocks = _BLOCK_COMMENT.sub("", source)
    return _LINE_COMMENT.sub("", without_blocks)

def read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")

def extract_binding(source: str, name: str) -> str:
    match = re.search(rf"\bconst\s+{re.escape(name)}\b\s*=", source)
    if match is None:
        raise LookupError(f"binding not found: {name}")
    start = match.end()
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
        elif char == ";" and depth <= 0:
            return source[start:index]
    return source[start:]

@dataclass
class Result:
    check: str
    passed: bool
    detail: str

def check_g1_health_stub_gone() -> Result:
    code = strip_comments(read(DASHBOARD_API))
    present = "getDashboardHealth" in code
    return Result(
        "G1  A-2: getDashboardHealth stub removed",
        not present,
        "symbol still present" if present else "absent from code; tombstone comment retained",
    )

def check_g2_manifest_client_gone() -> Result:
    code = strip_comments(read(BRANDING_API))
    present = "getBrandingManifest" in code
    return Result(
        "G2  B-1: getBrandingManifest removed",
        not present,
        "symbol still present" if present else "absent from code; tombstone comment retained",
    )

def check_g3_manifest_contract_survives() -> Result:
    endpoints = strip_comments(read(ENDPOINTS))
    try:
        branding = extract_binding(endpoints, "BRANDING_ENDPOINTS")
    except LookupError as exc:
        return Result("G3  B-1 scope: BRANDING_ENDPOINTS.manifest survives", False, str(exc))
    ok = re.search(r"\bmanifest\s*:", branding) is not None
    return Result(
        "G3  B-1 scope: BRANDING_ENDPOINTS.manifest survives",
        ok,
        "endpoint constant intact" if ok else "the route contract was deleted",
    )

def check_g4_ready_gate_binds_preview_to_subject() -> Result:
    code = strip_comments(read(COMPLIANCE_PAGE))
    try:
        ready = extract_binding(code, "ready")
    except LookupError as exc:
        return Result("G4  erasure gate binds preview to subject", False, str(exc))

    if "previewIsCurrent" not in ready:
        return Result("G4  erasure gate binds preview to subject", False, "`ready` omits previewIsCurrent")

    try:
        current = extract_binding(code, "previewIsCurrent")
    except LookupError as exc:
        return Result("G4  erasure gate binds preview to subject", False, str(exc))

    compares = "previewedSubject" in current and "subjectUserId" in current
    return Result(
        "G4  erasure gate binds preview to subject",
        compares,
        "previewIsCurrent compares previewedSubject against subjectUserId" if compares else "comparison missing",
    )

def check_g5_preview_key_includes_subject() -> Result:
    code = strip_comments(read(QUERY_KEYS))
    match = re.search(r"erasurePreview:\s*\([^)]*\)\s*=>(.*?)as const", code, re.S)
    if match is None:
        return Result("G5  erasurePreview key is subject-scoped", False, "complianceKeys.erasurePreview missing")
    body = match.group(1)
    ok = "subjectUserId" in body
    return Result("G5  erasurePreview key is subject-scoped", ok, "subject id is part of cache key" if ok else "omits subject")

def check_g6_preview_not_retained() -> Result:
    code = strip_comments(read(PREVIEW_PANEL))
    ok = re.search(r"gcTime:\s*0\b", code) is not None
    return Result("G6  preview counts are not retained in cache", ok, "gcTime: 0" if ok else "preview persists in cache")

def check_g7_preview_resets_on_subject_change() -> Result:
    code = strip_comments(read(PREVIEW_PANEL))
    ok = re.search(r"useEffect\(\s*\(\)\s*=>\s*\{\s*setRequested\(false\);?\s*\}\s*,\s*\[\s*trimmed\s*\]", code) is not None
    return Result("G7  editing the subject demotes the preview", ok, "requested resets on subject change" if ok else "no reset")

def check_g8_unknown_table_not_dropped() -> Result:
    code = strip_comments(read(PREVIEW_PANEL))
    try:
        label_for = extract_binding(code, "labelFor")
    except LookupError as exc:
        return Result("G8  unknown tables still render", False, str(exc))
    ok = "??" in label_for
    return Result("G8  unknown tables still render", ok, "labelFor has fallback" if ok else "no fallback")

def check_g9_preview_is_wired() -> Result:
    code = strip_comments(read(COMPLIANCE_PAGE))
    ok = "<ErasureImpactPreview" in code and "ErasureImpactPreview" in code
    return Result("G9  preview panel is mounted", ok, "mounted in OrganizationCompliance" if ok else "not mounted")

def check_g10_no_zero_default_on_counts() -> Result:
    code = strip_comments(read(PREVIEW_PANEL))
    offenders = re.findall(r"counts\[[^\]]+\]\s*\?\?\s*0", code) + re.findall(r"\bcount\s*\?\?\s*0", code)
    return Result("G10 no zero-defaulting of individual counts", not offenders, "none found" if not offenders else f"found: {offenders}")

CHECKS = [
    check_g1_health_stub_gone,
    check_g2_manifest_client_gone,
    check_g3_manifest_contract_survives,
    check_g4_ready_gate_binds_preview_to_subject,
    check_g5_preview_key_includes_subject,
    check_g6_preview_not_retained,
    check_g7_preview_resets_on_subject_change,
    check_g8_unknown_table_not_dropped,
    check_g9_preview_is_wired,
    check_g10_no_zero_default_on_counts,
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
        mark = "PASS" if r.passed else "FAIL"
        print(f"[{mark}] {r.check.ljust(width)}  {r.detail}")
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
