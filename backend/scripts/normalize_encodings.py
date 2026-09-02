#!/usr/bin/env python
"""ARCH-0V Tranche 2 — encoding normalisation, and the guard that keeps it.

WHAT WAS WRONG
--------------

At ARCH-22 completion, a repository-wide scan found:

    backend/requirements.txt       UTF-16LE + BOM   (7,002 bytes, 3,500 NULs)
    backend/requirements-dev.txt   UTF-16LE + BOM
    backend/app/schemas/usage.py   UTF-8 BOM

All three are PowerShell redirection artifacts. `pip freeze > file` on Windows
writes UTF-16LE. The consequences were real, not cosmetic:

  * Every SCA scanner opens requirements.txt as UTF-8 and sees garbage. A
    platform with 177 pinned dependencies reporting none is a procurement
    finding during an enterprise security review.
  * `ast.parse(open(p, encoding="utf-8"))` raises SyntaxError on a BOM'd
    source file. Every static gate in this repository has carried an
    `encoding="utf-8-sig"` workaround since ARCH-19 because of one file.

TWO MODES
---------

    python scripts/normalize_encodings.py --check   # CI: report, exit 1
    python scripts/normalize_encodings.py --apply   # one-shot repair

`--check` is the permanent guard and runs first in CI. `--apply` is idempotent:
running it on a clean tree changes nothing and exits 0.

WHAT IT REFUSES TO DO
---------------------

It never rewrites a file it cannot decode losslessly. A file that is neither
valid UTF-8 nor valid UTF-16 is reported and left alone — silently transcoding
a file whose encoding you guessed wrong is how you corrupt a migration.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

UTF8_BOM = b"\xef\xbb\xbf"
UTF16_LE_BOM = b"\xff\xfe"
UTF16_BE_BOM = b"\xfe\xff"
CRLF = b"\r\n"

#: Extensions treated as text. Anything else is left alone entirely.
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".json", ".md", ".yml", ".yaml", ".toml", ".cfg", ".ini",
        ".sql", ".css", ".html", ".sh", ".txt", ".env", ".example",
    }
)

#: Files with no suffix that are still text.
TEXT_NAMES: frozenset[str] = frozenset(
    {"Dockerfile", "Makefile", ".gitignore", ".gitattributes", ".dockerignore"}
)

#: Paths excluded from the CRLF check only. Nothing is excluded from the BOM
#: or UTF-16 checks — there is no legitimate reason for either in this tree.
CRLF_EXEMPT_PREFIXES: tuple[str, ...] = (
    "frontend/package-lock.json",
)


class Finding:
    __slots__ = ("path", "kind", "detail", "fixable")

    def __init__(self, path: str, kind: str, detail: str, fixable: bool) -> None:
        self.path = path
        self.kind = kind
        self.detail = detail
        self.fixable = fixable

    def __str__(self) -> str:
        mark = "fixable" if self.fixable else "MANUAL"
        return f"  [{self.kind:<10}] {self.path}\n               {self.detail} ({mark})"


def tracked_files() -> list[str]:
    """Every file Git tracks. Untracked scratch files are not our problem."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=str(REPO_ROOT), text=False
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit(
            f"Could not enumerate tracked files via git: {exc}. "
            f"Run this from inside the repository."
        ) from exc
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


def is_text(rel_path: str) -> bool:
    path = Path(rel_path)
    if path.name in TEXT_NAMES:
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def _decode(raw: bytes) -> Optional[str]:
    """Decode losslessly, or return None. Never guesses."""
    if raw.startswith(UTF16_LE_BOM) or raw.startswith(UTF16_BE_BOM):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if raw.startswith(UTF8_BOM):
        raw = raw[len(UTF8_BOM):]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def inspect(rel_paths: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []

    for rel in rel_paths:
        if not is_text(rel):
            continue

        absolute = REPO_ROOT / rel
        try:
            raw = absolute.read_bytes()
        except (OSError, FileNotFoundError):
            continue

        if not raw:
            continue

        if raw.startswith(UTF16_LE_BOM) or raw.startswith(UTF16_BE_BOM):
            decodable = _decode(raw) is not None
            findings.append(
                Finding(
                    rel,
                    "UTF-16",
                    f"UTF-16 byte-order mark; {raw.count(0)} NUL bytes. "
                    f"SCA scanners and pip read this as UTF-8.",
                    fixable=decodable,
                )
            )
            continue

        if raw.startswith(UTF8_BOM):
            findings.append(
                Finding(
                    rel,
                    "UTF-8 BOM",
                    "Leading EF BB BF. ast.parse(encoding='utf-8') raises "
                    "SyntaxError on this file.",
                    fixable=_decode(raw) is not None,
                )
            )
            continue

        if b"\r\n" in raw and not rel.startswith(CRLF_EXEMPT_PREFIXES):
            findings.append(
                Finding(
                    rel,
                    "CRLF",
                    f"{raw.count(CRLF)} CRLF line ending(s); .gitattributes "
                    f"declares this tree LF-only.",
                    fixable=True,
                )
            )
            continue

        if _decode(raw) is None:
            findings.append(
                Finding(
                    rel,
                    "UNDECODED",
                    "Neither valid UTF-8 nor valid UTF-16. Left untouched.",
                    fixable=False,
                )
            )

    return findings


def repair(findings: list[Finding]) -> tuple[int, int]:
    fixed = 0
    refused = 0
    for finding in findings:
        if not finding.fixable:
            refused += 1
            continue

        absolute = REPO_ROOT / finding.path
        raw = absolute.read_bytes()
        text = _decode(raw)
        if text is None:
            refused += 1
            continue

        normalised = text.replace("\r\n", "\n").replace("\r", "\n")
        if normalised and not normalised.endswith("\n"):
            normalised += "\n"

        absolute.write_bytes(normalised.encode("utf-8"))
        print(f"  fixed  {finding.path}  ({finding.kind})")
        fixed += 1

    return fixed, refused


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalise text file encodings to UTF-8 / LF / no BOM."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--check", action="store_true", help="Report and exit non-zero. CI mode."
    )
    group.add_argument(
        "--apply", action="store_true", help="Repair in place. Idempotent."
    )
    args = parser.parse_args()

    files = tracked_files()
    findings = inspect(files)

    if not findings:
        print(f"Encoding clean: {len(files)} tracked files, 0 findings.")
        return 0

    print(f"\n{len(findings)} encoding finding(s) across {len(files)} tracked files:\n")
    for finding in findings:
        print(finding)
    print()

    if args.check:
        print(
            "FAIL — see .gitattributes for why this is enforced rather than "
            "fixed once. Run: python scripts/normalize_encodings.py --apply"
        )
        return 1

    fixed, refused = repair(findings)
    print(f"\n{fixed} file(s) normalised, {refused} refused (manual review).")
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main())