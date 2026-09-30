"""Heuristic cross-check: frontend API path literals vs backend endpoint inventory."""
import csv
import re
import sys
from pathlib import Path

SRC = Path(sys.argv[1])
ENDPOINTS = sys.argv[2]

backend = []
for r in csv.DictReader(open(ENDPOINTS)):
    p = r["path"]
    if p.startswith("/api/v1"):
        p = p[len("/api/v1"):]
    backend.append((r["method"], p, r["module"]))
first_segments = {p.split("/")[1] for _, p, _ in backend if p.count("/") >= 1 and len(p) > 1}

TPL = re.compile(r"`([^`]*)`|\"(/[^\"\s]*)\"|'(/[^'\s]*)'")
HELPER = re.compile(
    r"(?:const|let)\s+(\w+)\s*=\s*\([^)]*\)\s*(?::\s*string)?\s*=>\s*(?:`([^`]*)`|\{[^}]*?return\s+`([^`]*)`|\{[^}]*?return\s+encodeURIComponent)",
    re.S,
)
PROP_HELPER = re.compile(r"(\w+)\s*:\s*\([^)]*\)\s*(?::\s*string)?\s*=>\s*`([^`]*)`")


def expand(t, helpers, depth=0):
    if depth > 5:
        return t
    def rep(m):
        inner = m.group(1).strip()
        hm = re.match(r"(\w+)\(", inner)
        if hm and hm.group(1) in helpers and helpers[hm.group(1)] is not None:
            return expand(helpers[hm.group(1)], helpers, depth + 1)
        return "{}"
    return re.sub(r"\$\{((?:[^{}]|\{[^{}]*\})*)\}", rep, t)


def norm(p):
    p = p.split("?")[0].rstrip("/")
    p = re.sub(r"\{[^}]*\}", "{}", p)
    return p


def matches(fp, bp):
    fs, bs = fp.split("/"), bp.split("/")
    if len(fs) != len(bs):
        return False
    for a, b in zip(fs, bs):
        if a == b or a == "{}" or re.fullmatch(r"\{[^}]*\}", b):
            continue
        # e.g. "{}.ics" vs "{token}.ics", "{}.png"
        if re.fullmatch(r"\{[^}]*\}\.\w+", b) and a.endswith(b.split(".")[-1]):
            continue
        return False
    return True


unmatched = {}
total = 0
for f in sorted(SRC.rglob("*.ts*")):
    rel = str(f.relative_to(SRC))
    if rel.startswith(("routes/", "constants/")) or "test" in rel or "navigation" in rel:
        continue
    text = f.read_text(encoding="utf-8-sig", errors="replace")
    helpers = {}
    for m in HELPER.finditer(text):
        helpers[m.group(1)] = m.group(2) or m.group(3) or "{}"
    for m in PROP_HELPER.finditer(text):
        helpers.setdefault(m.group(1), m.group(2))
    for m in TPL.finditer(text):
        lit = m.group(1) if m.group(1) is not None else (m.group(2) or m.group(3))
        if lit is None:
            continue
        e = expand(lit, helpers)
        if not e.startswith("/"):
            continue
        segs = e.split("/")
        if len(segs) < 2 or segs[1] not in first_segments:
            continue
        n = norm(e)
        if n.count("/") < 2 and n not in ("/me", "/partners", "/organizations"):
            # single-segment strings like "/organizations" are usually route paths too
            pass
        total += 1
        if not any(matches(n, norm(bp)) or any(matches(n + suffix, norm(bp)) for suffix in ()) for _, bp, _ in backend):
            # allow prefix-only literals (a base later extended): match if some backend path starts with it
            if any(norm(bp).startswith(n + "/") or matches(n, "/".join(norm(bp).split("/")[: n.count("/") + 1])) for _, bp, _ in backend):
                continue
            unmatched.setdefault(n, set()).add(rel)

print(f"checked {total} path literals; {len(unmatched)} unmatched")
for k, v in sorted(unmatched.items()):
    print(k, "<-", ", ".join(sorted(v))[:150])
