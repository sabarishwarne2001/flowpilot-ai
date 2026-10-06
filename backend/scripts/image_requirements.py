#!/usr/bin/env python3
"""Write the per-image requirement files from requirements.txt (F-045).

    python scripts/image_requirements.py           # rewrite both files
    python scripts/image_requirements.py --check   # exit 1 if either is stale

`requirements.txt` stays the full, pinned set that development and CI install.
Every container image used to install all of it, so the API image carried
PaddleOCR (about 1.2 GB with OpenCV and PaddleX), a test mocking library and
an unused Kubernetes client. This derives two subsets from the dependency
graph of the installed packages (extras included), so a pin can never differ
from requirements.txt:

- requirements-web.txt: the API, the light worker and the enrich worker.
  Everything except the OCR engine's own tree, the development tools and
  the packages nothing imports. torch and sentence-transformers stay: the API
  embeds search queries itself (hybrid_search_service, chunk_retrieval_service).
- requirements-ocr.txt: what the OCR image adds on top of the web set.

Run it in the backend virtualenv after any change to requirements.txt.
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata as metadata
import re
import sys
import warnings
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

BACKEND = Path(__file__).resolve().parents[1]
FULL = BACKEND / "requirements.txt"
WEB = BACKEND / "requirements-web.txt"
OCR = BACKEND / "requirements-ocr.txt"

# The OCR engine. Only the `ocr` worker profile may import it (app/workers/profiles.py).
OCR_ROOTS = frozenset({"paddlepaddle", "paddleocr"})
# Development and test tools; requirements.txt carries them for CI.
DEV_ROOTS = frozenset({"moto", "py-partiql-parser", "pytest-env", "git-filter-repo", "build"})
# Left behind by a removed dependency (ChromaDB); no code imports it.
UNUSED_ROOTS = frozenset({"kubernetes"})


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        text = line.split("#", 1)[0].strip()
        match = re.match(r"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*==", text)
        if match:
            pins[canonicalize_name(match.group(1))] = text
    return pins


def _graph(pins: dict[str, str]) -> dict[str, set[str]]:
    """name -> pinned dependencies, honouring every extra some parent asks for."""
    extras = {name: {""} for name in pins}
    graph: dict[str, set[str]] = {name: set() for name in pins}
    changed = True
    while changed:
        changed = False
        for name in pins:
            try:
                requires = metadata.distribution(name).requires or []
            except metadata.PackageNotFoundError as exc:
                raise SystemExit(f"{name} is pinned but not installed; run in the backend venv") from exc
            for line in requires:
                req = Requirement(line)
                if req.marker and not any(req.marker.evaluate({"extra": e}) for e in extras[name]):
                    continue
                dep = canonicalize_name(req.name)
                if dep not in pins:
                    continue
                if dep not in graph[name]:
                    graph[name].add(dep)
                    changed = True
                for extra in req.extras:
                    if extra not in extras[dep]:
                        extras[dep].add(extra)
                        changed = True
    return graph


def _closure(graph: dict[str, set[str]], start: set[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(start)
    while stack:
        name = stack.pop()
        if name not in seen:
            seen.add(name)
            stack.extend(graph[name])
    return seen


# Historical scripts are not read (CLAUDE.md); the images do not run them.
_NOT_SCANNED = re.compile(r"^(apply_|verify_|arch07_|arch08_)")


def _imported_distributions(pins: dict[str, str]) -> set[str]:
    """Pinned distributions that the code shipped in the images imports by name."""
    owners = metadata.packages_distributions()
    found: set[str] = set()
    for folder in ("app", "alembic", "scripts"):
        for path in sorted((BACKEND / folder).rglob("*.py")):
            if _NOT_SCANNED.match(path.name):
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(path.read_text(encoding="utf-8-sig"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    modules = [node.module]
                else:
                    continue
                for module in modules:
                    for dist in owners.get(module.split(".")[0], ()):
                        if canonicalize_name(dist) in pins:
                            found.add(canonicalize_name(dist))
    return found


def compute() -> tuple[str, str]:
    pins = _pins(FULL)
    graph = _graph(pins)
    required = {dep for deps in graph.values() for dep in deps}
    roots = {name for name in pins if name not in required}
    unknown = (OCR_ROOTS | DEV_ROOTS | UNUSED_ROOTS) - roots
    if unknown:
        raise SystemExit(f"no longer top-level packages in requirements.txt: {sorted(unknown)}; update this script")
    excluded = OCR_ROOTS | DEV_ROOTS | UNUSED_ROOTS
    # A package the code imports is needed even when only an excluded package declares it
    # (pypdfium2 is declared by paddlex, boto3 by moto).
    web = _closure(graph, (roots | _imported_distributions(pins)) - excluded)
    ocr = _closure(graph, set(OCR_ROOTS)) - web
    header = "# Generated by scripts/image_requirements.py from requirements.txt (F-045); do not edit.\n"
    web_text = header + "# The web, worker and enrich images.\n" + "".join(f"{pins[n]}\n" for n in sorted(web))
    ocr_text = header + "# Added on top of requirements-web.txt in the ocr image.\n" + "".join(
        f"{pins[n]}\n" for n in sorted(ocr))
    return web_text, ocr_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a file differs from requirements.txt")
    args = parser.parse_args(argv)
    web_text, ocr_text = compute()
    stale = [p.name for p, text in ((WEB, web_text), (OCR, ocr_text))
             if not p.exists() or p.read_text(encoding="utf-8") != text]
    if args.check:
        if stale:
            print(f"stale: {', '.join(stale)}; run python scripts/image_requirements.py")
            return 1
        return 0
    WEB.write_text(web_text, encoding="utf-8")
    OCR.write_text(ocr_text, encoding="utf-8")
    print(f"requirements-web.txt: {web_text.count(chr(10)) - 2} packages; "
          f"requirements-ocr.txt: {ocr_text.count(chr(10)) - 2} packages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
