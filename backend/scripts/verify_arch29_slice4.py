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

CSS = SRC / "styles" / "index.css"
PRIMITIVES = SRC / "components" / "ui" / "primitives.ts"
STATUS_PILL = SRC / "components" / "ui" / "StatusPill.tsx"
COMPLIANCE = SRC / "pages" / "organization" / "OrganizationCompliance.tsx"
INVOICES = SRC / "components" / "billing" / "SupplierInvoicePanels.tsx"
INVITATION = SRC / "pages" / "Auth" / "InvitationAcceptPage.tsx"

def read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8-sig")

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?<![:/])//[^\n]*")

def strip_comments(source: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))

def theme_block(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{", css)
    if match is None:
        raise LookupError(f"theme block not found: {selector}")
    start = match.end()
    depth = 1
    for index in range(start, len(css)):
        if css[index] == "{":
            depth += 1
        elif css[index] == "}":
            depth -= 1
            if depth == 0:
                return css[start:index]
    return css[start:]

def lightness(block: str, variable: str) -> float:
    match = re.search(rf"--{variable}:\s*([\d.]+)\s+([\d.]+)%\s+([\d.]+)%", block)
    if match is None:
        raise LookupError(f"--{variable} not found or not in H S% L% form")
    return float(match.group(3))

@dataclass
class Result:
    check: str
    passed: bool
    detail: str

def ok(name: str, condition: bool, good: str, bad: str) -> Result:
    return Result(name, condition, good if condition else bad)

def _ladder(selector: str, label: str) -> Result:
    css = strip_comments(read(CSS))
    block = theme_block(css, selector)
    background = lightness(block, "background")
    card = lightness(block, "card")
    popover = lightness(block, "popover")

    distinct = len({background, card, popover}) >= 2 and card != background
    monotonic = (
        (card > background and popover >= card)
        if card > background
        else (card < background and popover <= card)
    )
    return ok(
        f"{label} elevation ladder is a ladder",
        distinct and monotonic,
        f"background {background}% -> card {card}% -> popover {popover}%",
        f"FLAT: background {background}%, card {card}%, popover {popover}%",
    )

def check_g1_dark_ladder() -> Result:
    return _ladder(".dark", "G1  dark")

def check_g2_light_ladder() -> Result:
    return _ladder(":root", "G2  light")

def check_g3_dark_is_desaturated() -> Result:
    block = theme_block(strip_comments(read(CSS)), ".dark")
    match = re.search(r"--background:\s*[\d.]+\s+([\d.]+)%", block)
    if match is None:
        return Result("G3  dark surfaces are desaturated", False, "unparseable")
    saturation = float(match.group(1))
    return ok("G3  dark surfaces are desaturated", saturation <= 30, f"background saturation {saturation}%", f"saturation {saturation}%")

def check_g4_destructive_is_legible() -> Result:
    block = theme_block(strip_comments(read(CSS)), ".dark")
    value = lightness(block, "destructive")
    return ok("G4  dark destructive is legible", value >= 45, f"lightness {value}%", f"lightness {value}% too dark")

def check_g5_focus_ring_is_global() -> Result:
    css = strip_comments(read(CSS))
    has_rule = re.search(r"input:focus-visible", css) is not None and re.search(r"ring-primary/20", css) is not None
    return ok("G5  focus ring is defined at the base layer", has_rule, "focus ring global rule present", "no global focus rule")

def check_g6_focus_is_visible_only() -> Result:
    css = strip_comments(read(CSS))
    bare_focus = re.search(r"\binput:focus\s*[,{]", css) is not None
    return ok("G6  focus ring uses :focus-visible", not bare_focus, "uses :focus-visible", "bare :focus used")

def check_g7_pill_derives_tone() -> Result:
    code = strip_comments(read(STATUS_PILL))
    derives = "TONE_BY_STATUS" in code and re.search(r"tone\s*\?\?\s*toneForStatus\(", code) is not None
    return ok("G7  StatusPill derives tone from status", derives, "tone derived automatically", "tone must be passed manually")

def check_g8_toneblind_consoles_converted() -> Result:
    blind = re.compile(r'className="rounded border border-border px-1\.5 py-0\.5 text-\[11px\]"')
    offenders = [path.name for path in (COMPLIANCE, INVOICES) if blind.search(strip_comments(read(path)))]
    return ok("G8  tone-blind status markup is gone", not offenders, "compliance and invoices use StatusPill", f"offenders: {offenders}")

def check_g9_unknown_status_not_guessed() -> Result:
    code = strip_comments(read(STATUS_PILL))
    return ok("G9  an unknown status is neutral, not guessed", re.search(r'\?\?\s*"neutral"', code) is not None, "unknown status defaults to neutral", "unknown status guessed")

def check_g10_overlays_contain_scroll() -> Result:
    css = strip_comments(read(CSS))
    defined = ".fp-overlay" in css and "overscroll-behavior: contain" in css
    return ok("G10 modal overlays contain their scroll", defined, ".fp-overlay defined with overscroll-behavior", "missing overscroll containment")

def check_g11_no_unthemeable_colours() -> Result:
    palette = re.compile(r"\b(?:bg|text|border|ring|divide)-(?:slate|zinc|gray|neutral|stone)-\d{2,3}\b")
    class_attr = re.compile(r'className=(?:"([^"]*)"|\{`([^`]*)`\})', re.S)
    offenders: list[str] = []

    for path in list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.ts")):
        code = strip_comments(read(path))
        flagged = any(
            palette.search(literal)
            for literal in re.findall(r'"([^"\n]*)"|`([^`]*)`', code)
            for literal in ([literal[0], literal[1]] if isinstance(literal, tuple) else [literal])
            if literal
        )
        if not flagged and path.suffix == ".tsx":
            flagged = any(
                re.search(r"#[0-9a-fA-F]{6}\b", (m.group(1) or m.group(2) or ""))
                for m in class_attr.finditer(code)
            )
        if flagged:
            offenders.append(path.relative_to(SRC).as_posix())
    return ok("G11 no un-themeable colours in components", not offenders, "every colour resolves through theme properties", f"hardcoded colours found in {len(offenders)} files")

def check_g12_lint_baseline_is_clean() -> Result:
    code = read(INVITATION)
    empty_catch = re.search(r"catch\s*\{\s*\}", code) is not None
    braceless = re.search(r"^\s*if \([^)]*\) [a-z]", code, re.M) is not None
    return ok("G12 InvitationAcceptPage lint baseline is clean", not empty_catch and not braceless, "InvitationAcceptPage lint clean", "pre-existing lint errors found")

CHECKS = [
    check_g1_dark_ladder,
    check_g2_light_ladder,
    check_g3_dark_is_desaturated,
    check_g4_destructive_is_legible,
    check_g5_focus_ring_is_global,
    check_g6_focus_is_visible_only,
    check_g7_pill_derives_tone,
    check_g8_toneblind_consoles_converted,
    check_g9_unknown_status_not_guessed,
    check_g10_overlays_contain_scroll,
    check_g11_no_unthemeable_colours,
    check_g12_lint_baseline_is_clean,
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
    parser.add_argument("--mutate", action="store_true")
    args = parser.parse_args()
    return run()

if __name__ == "__main__":
    raise SystemExit(main())
