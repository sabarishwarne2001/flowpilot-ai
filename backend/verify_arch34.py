"""ARCH-34 — the gates.

    python verify_arch34.py                    offline only
    python verify_arch34.py --db               + live Postgres invariants
    python verify_arch34.py --mutate           + mutation kills
    python verify_arch34.py --db --mutate      everything
    python verify_arch34.py --regressions      + ARCH-33/32/31/31_step0

WHY THE OFFLINE GATES LOAD MODULES BY FILE PATH
===============================================

`app/services/__init__.py` eagerly imports service modules, which reach
`app.core.config` and from there pydantic and SQLAlchemy. A plain
`import app.services.radar.fingerprint` cannot complete without a configured
environment — which would make the offline gates require exactly the setup
they exist to work without.

So `_stub_packages()` seeds `sys.modules` with namespace stubs carrying a
`__path__`. Python finds the parent packages already present, never executes
their `__init__.py`, and resolves the submodules from `root`. Pointing `root`
at a mutated COPY is what makes `--mutate` reach an engine's own imports
rather than only its top-level module. This is the same arrangement
`verify_arch33.py` uses, and it is copied rather than shared because a shared
harness would make one phase's gate fail when another phase moved a file.

WHY THE MINHASH GATE COMPARES AGAINST EXACT JACCARD
===================================================

A gate that compared one MinHash signature to another MinHash signature would
pass with a broken permutation family, a broken shingler, and a hash function
that returned a constant. `fingerprint.exact_jaccard` materialises both sets
and computes the truth; the estimate is checked against THAT.

WHY PURITY IS CHECKED THROUGH THE AST
=====================================

Not by grepping for "Session". Every pure module in this package has a
docstring that says it holds no Session, and a text search would fail the gate
on its own explanation. `_imports_of()` walks the parse tree and reads the
actual import statements.

THE CONTROL MUTANT
==================

`--mutate` includes a mutant that MUST SURVIVE: a comment edit. A mutation
suite where every mutant dies is a suite that cannot distinguish "the gates
are strong" from "the harness reports a kill whenever anything changes", and
the second failure mode is invisible without a control.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent

PASSED = 0
FAILED = 0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name}{(': ' + detail) if detail else ''}")
        print(f"  FAIL  {name}{(': ' + detail) if detail else ''}")


def section(title: str) -> None:
    print(f"\n--- {title} ---")


# ===========================================================================
# Loader
# ===========================================================================


def _stub_packages(root: Path) -> None:
    for cached in [
        name
        for name in list(sys.modules)
        if name.startswith("app.services.radar")
        or name.startswith("app.services.assertions")
        or name in ("app.core.normalize",)
    ]:
        sys.modules.pop(cached, None)
    for parent in ("app.services", "app.core", "app"):
        existing = sys.modules.get(parent)
        if existing is not None and getattr(existing, "_arch34_stub", False):
            sys.modules.pop(parent, None)

    for dotted, path in (
        ("app", root / "app"),
        ("app.core", root / "app" / "core"),
        ("app.services", root / "app" / "services"),
    ):
        existing = sys.modules.get(dotted)
        if existing is not None and getattr(existing, "_arch34_stub", False):
            existing.__path__ = [str(path)]  # type: ignore[attr-defined]
            continue
        if existing is not None and dotted != "app":
            continue
        stub = types.ModuleType(dotted)
        stub.__path__ = [str(path)]  # type: ignore[attr-defined]
        stub._arch34_stub = True  # type: ignore[attr-defined]
        sys.modules[dotted] = stub


def _engines(root: Path) -> dict[str, Any]:
    _stub_packages(root)
    import importlib

    return {
        "vocab": importlib.import_module("app.services.radar.vocabulary"),
        "fp": importlib.import_module("app.services.radar.fingerprint"),
        "layers": importlib.import_module("app.services.radar.layers"),
        "surge": importlib.import_module("app.services.radar.price_surge"),
        "drift": importlib.import_module("app.services.radar.drift"),
    }


# ===========================================================================
# Fixtures
# ===========================================================================

LINES = [
    ("toner cartridge tn2 black", 2, 2_100_000),
    ("a4 paper ream 80gsm", 10, 32_000),
    ("delivery charge express", 1, 15_000),
    ("installation service onsite", 1, 450_000),
    ("annual maintenance contract", 1, 1_200_000),
    ("stapler heavy duty metal", 3, 45_000),
    ("envelopes dl white box", 500, 8_000),
    ("whiteboard markers assorted pack", 4, 22_000),
    ("desk organiser mesh black", 2, 68_000),
    ("laptop stand aluminium adjustable", 1, 240_000),
    ("monitor arm dual gas spring", 1, 310_000),
    ("cable tray under desk", 2, 55_000),
    ("keyboard wireless compact", 1, 190_000),
    ("mouse ergonomic vertical", 1, 145_000),
    ("headset usb noise cancelling", 2, 420_000),
    ("power strip six socket surge", 3, 98_000),
]

MSA_PAY = (
    "5.2 Payment. Customer shall pay each undisputed invoice payable within "
    "thirty (30) days of receipt of a correct invoice. Invoices are issued "
    "monthly in arrears."
)
SOW_PAY = (
    "4.1 Payment. Customer shall pay each undisputed invoice payable within "
    "sixty (60) days of receipt of a correct invoice. Invoices are issued "
    "monthly in arrears."
)
MSA_LAW = (
    "12.1 Governing law. This Agreement shall be governed by the laws of "
    "India and the courts of Bengaluru shall have exclusive jurisdiction."
)
SOW_LAW = (
    "9.1 Governing law. This Agreement shall be governed by the laws of "
    "India and the courts of Bengaluru shall have exclusive jurisdiction."
)
MSA_TERM = (
    "8.1 Termination. Either party may terminate this Agreement for "
    "convenience upon ninety (90) days prior written notice to the other party."
)
SOW_TERM = (
    "7.1 Termination. Either party may terminate this Agreement for "
    "convenience upon thirty (30) days prior written notice to the other party."
)


def _source(fp: Any, lines: list[tuple[str, int, int]]) -> str:
    return fp.shingle_source_from_lines(
        [(d, Decimal(q), p) for d, q, p in lines]
    )


def _candidate(
    eng: dict[str, Any],
    wid: str,
    sha: str,
    vendor: Optional[str],
    number: Optional[str],
    text: str,
    vector: list[float],
    total: Optional[int] = None,
    when: Optional[date] = None,
) -> Any:
    fp, layers = eng["fp"], eng["layers"]
    shingles = fp.shingles(text)
    return layers.Candidate(
        fingerprint=fp.DocumentFingerprint(
            work_item_id=wid,
            content_sha256=sha,
            vendor_key=vendor,
            document_number=number,
            minhash=fp.minhash_signature(shingles),
            embedding=fp.l2_normalise(vector),
            shingle_count=len(shingles),
            line_count=len(LINES),
            total_micros=total,
            currency="INR",
            document_date=when,
            embedding_model="bge-small",
        ),
        shingles=shingles,
        chunks=(
            fp.ChunkVector(
                chunk_id=f"{wid}-c0", chunk_index=0, text=text[:200], vector=vector
            ),
        ),
    )


def _history(surge: Any, prices: list[int], dates: list[date]) -> list[Any]:
    return [
        surge.PriceObservation(f"h{i}", d, p, "INR", "acme", "TN2")
        for i, (d, p) in enumerate(zip(dates, prices))
    ]


MONTHS = [
    date(2025, 10, 5),
    date(2025, 11, 5),
    date(2025, 12, 5),
    date(2026, 1, 5),
    date(2026, 2, 5),
    date(2026, 3, 5),
    date(2026, 4, 5),
    date(2026, 5, 5),
    date(2026, 6, 5),
    date(2026, 7, 5),
    date(2026, 8, 5),
]
STEADY = [
    2_100_000, 2_100_000, 2_050_000, 2_150_000, 2_100_000, 2_120_000,
    2_080_000, 2_100_000, 2_110_000, 2_100_000, 2_090_000,
]


# ===========================================================================
# Offline gates
# ===========================================================================


def gate_vocabulary(eng: dict[str, Any]) -> None:
    section("Vocabulary and migration agreement")
    vocab = eng["vocab"]

    migration = (HERE / "alembic" / "versions" / "arch34_step1_radar.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(migration)
    consts: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            try:
                consts[node.target.id] = ast.literal_eval(node.value)  # type: ignore[arg-type]
            except (ValueError, TypeError):
                pass

    check(
        "migration KINDS equals vocabulary KINDS",
        consts.get("KINDS") == vocab.KINDS,
        f"{consts.get('KINDS')} vs {vocab.KINDS}",
    )
    check(
        "migration LAYERS equals vocabulary LAYERS",
        consts.get("LAYERS") == vocab.LAYERS,
    )
    check(
        "migration SEVERITIES equals vocabulary SEVERITIES",
        consts.get("SEVERITIES") == vocab.SEVERITIES,
    )
    check(
        "migration STATUSES equals vocabulary STATUSES",
        consts.get("STATUSES") == vocab.STATUSES,
    )
    check(
        "migration UNPAIRED_LAYERS equals vocabulary UNPAIRED_LAYERS",
        set(consts.get("UNPAIRED_LAYERS", ())) == set(vocab.UNPAIRED_LAYERS),
    )
    check(
        "migration MINHASH_PERMUTATIONS is 128",
        consts.get("MINHASH_PERMUTATIONS") == vocab.MINHASH_PERMUTATIONS == 128,
    )
    check(
        "migration EMBEDDING_DIMENSION equals vocabulary",
        consts.get("EMBEDDING_DIMENSION") == vocab.EMBEDDING_DIMENSION,
    )
    check(
        "every layer maps to exactly one kind",
        set(vocab.KIND_FOR_LAYER) == set(vocab.LAYERS)
        and set(vocab.KIND_FOR_LAYER.values()) == set(vocab.KINDS),
    )
    check(
        "duplicate layer order is strongest-first",
        vocab.DUPLICATE_LAYER_ORDER == ("L0", "L1", "L2", "L3")
        and vocab.LAYER_STRENGTH["L0"] < vocab.LAYER_STRENGTH["L3"],
    )
    check(
        "PRICE_SURGE is the only unpaired layer",
        vocab.UNPAIRED_LAYERS == frozenset({"PRICE_SURGE"}),
    )
    check(
        "capability key is capability.anomaly_radar",
        vocab.CAPABILITY_ANOMALY_RADAR == "capability.anomaly_radar",
    )
    check(
        "the sweep meter and the job types are different strings",
        vocab.USAGE_EVENT_RADAR_SWEEP
        not in (vocab.JOB_ANOMALY_NIGHTLY, vocab.JOB_ANOMALY_SCAN_DOCUMENT),
    )
    check(
        "MINHASH_SEED is pinned",
        vocab.MINHASH_SEED == 0x464C4F57,
        "changing the seed makes every stored signature incomparable",
    )


def gate_purity(root: Path) -> None:
    section("Purity (AST, not grep)")
    forbidden = ("sqlalchemy", "app.models", "app.db", "requests", "httpx")
    impure = {"candidates.py", "sweep.py", "findings.py", "suppressions.py"}

    radar = root / "app" / "services" / "radar"
    for path in sorted(radar.rglob("*.py")):
        if path.name in impure or path.name == "__init__.py" and path.parent == radar:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
        bad = [
            name
            for name in names
            if any(name == f or name.startswith(f + ".") for f in forbidden)
        ]
        check(f"{path.name} imports no Session or ORM", not bad, str(bad))

    check(
        "MinHash uses no third-party library",
        "datasketch"
        not in (radar / "fingerprint.py").read_text(encoding="utf-8").replace(
            "`datasketch`", ""
        ).split("from __future__")[1],
        "the docstring may name it; the code may not import it",
    )


def gate_minhash(eng: dict[str, Any]) -> None:
    section("MinHash against EXACT Jaccard")
    fp = eng["fp"]

    base = _source(fp, LINES)
    rescan = list(LINES)
    rescan[5] = ("stapler heavyduty metal", 3, 45_000)
    half = LINES[:8] + [
        ("completely different goods", 1, 1),
        ("unrelated services rendered", 2, 2),
    ]
    unrelated = [
        ("master services agreement", 1, 1),
        ("governing law karnataka arbitration", 1, 2),
    ]

    for label, other in (
        ("identical", LINES),
        ("one word rescanned", rescan),
        ("half rewritten", half),
        ("unrelated", unrelated),
    ):
        left = fp.shingles(base)
        right = fp.shingles(_source(fp, other))
        exact = fp.exact_jaccard(left, right)
        estimate = fp.jaccard_estimate(
            fp.minhash_signature(left),
            fp.minhash_signature(right),
            left_shingles=len(left),
            right_shingles=len(right),
        )
        error = abs(exact - (estimate or Decimal("0")))
        check(
            f"estimate within 0.05 of exact ({label})",
            error <= Decimal("0.05"),
            f"exact={exact} estimate={estimate} error={error}",
        )

    signature = fp.minhash_signature(fp.shingles(base))
    check(
        "signature is deterministic across calls",
        signature == fp.minhash_signature(fp.shingles(base)),
    )
    check("signature has exactly 128 values", len(signature) == 128)
    check(
        "signature values fit in 32 unsigned bits",
        all(0 <= value <= 0xFFFFFFFF for value in signature),
    )
    # bigint[] is required because of SHORT documents, not long ones. Over a
    # few hundred shingles every one of the 128 minima lands near the bottom
    # of the 32-bit range; over ONE shingle each minimum is that shingle's own
    # hash, uniform across the whole range, and about half of them exceed
    # signed int4. A one-line delivery note is a real document, and it is the
    # one that would fail an `integer[]` INSERT.
    short_values: list[int] = []
    for index in range(40):
        short_values.extend(fp.minhash_signature({f"delivery note {index}"}))
    check(
        "short documents produce values above signed int4 (bigint[] required)",
        max(short_values) > 2_147_483_647,
        f"max={max(short_values)}",
    )
    check(
        "no value ever exceeds unsigned 32 bits",
        max(short_values) <= 0xFFFFFFFF and max(signature) <= 0xFFFFFFFF,
    )
    check(
        "empty documents are never reported as similar",
        fp.jaccard_estimate(
            fp.EMPTY_SIGNATURE, fp.EMPTY_SIGNATURE, left_shingles=0, right_shingles=0
        )
        is None,
    )


def gate_layers(eng: dict[str, Any]) -> None:
    section("Layer ordering and early exit")
    fp, layers, vocab = eng["fp"], eng["layers"], eng["vocab"]

    base = _source(fp, LINES)
    rescan_lines = list(LINES)
    rescan_lines[5] = ("stapler heavyduty metal", 3, 45_000)
    rescan = _source(fp, rescan_lines)
    sha = fp.content_sha256(b"identical bytes")

    a = _candidate(eng, "A", sha, "acme", "INV-982", base, [0.5, 0.4, 0.3, 0.2],
                   5_000_000_000, date(2026, 3, 1))
    identical = _candidate(eng, "B", sha, "acme", "INV-982", base,
                           [0.5, 0.4, 0.3, 0.2], 5_000_000_000, date(2026, 3, 1))

    every = [hit.layer for hit in layers.all_hits(a, identical)]
    one = layers.strongest(a, identical)
    check(
        "a byte-identical pair WOULD fire at every layer",
        set(every) == {"L0", "L1", "L2", "L3"},
        str(every),
    )
    check(
        "a byte-identical pair reports L0 ONLY",
        one is not None and one.layer == vocab.LAYER_L0,
        "the early exit is the whole point; without it one pair becomes four rows",
    )

    # L1 without L0: different bytes, same vendor and number.
    l1 = _candidate(eng, "C", fp.content_sha256(b"rescan"), "acme", "INV-982",
                    rescan, [0.5, 0.4, 0.3, 0.2001], 5_002_000_000, date(2026, 3, 20))
    hit = layers.strongest(a, l1)
    check("L1 fires on vendor + number with different bytes",
          hit is not None and hit.layer == vocab.LAYER_L1)

    # L2 without L0 or L1: no document number on the counterpart.
    l2 = _candidate(eng, "D", fp.content_sha256(b"rescan2"), "acme", None, rescan,
                    [0.5, 0.4, 0.3, 0.2001], 5_002_000_000, date(2026, 3, 20))
    hit = layers.strongest(a, l2)
    check(
        "L2 fires on line-item overlap alone",
        hit is not None and hit.layer == vocab.LAYER_L2,
        "" if hit is None else hit.layer,
    )
    check(
        "L2 score is the Jaccard estimate, at or above 0.85",
        hit is not None and hit.score >= vocab.L2_JACCARD_MIN,
    )

    # The L2 guards.
    far = _candidate(eng, "E", fp.content_sha256(b"far"), "acme", None, rescan,
                     [0.5, 0.4, 0.3, 0.2001], 5_002_000_000, date(2026, 7, 1))
    check(
        "L2 declines a pair dated outside the 45-day window",
        vocab.LAYER_L2 not in [h.layer for h in layers.all_hits(a, far)],
    )
    pricey = _candidate(eng, "F", fp.content_sha256(b"pricey"), "acme", None, rescan,
                        [0.5, 0.4, 0.3, 0.2001], 5_150_000_000, date(2026, 3, 20))
    check(
        "L2 declines a pair whose totals differ by more than 0.5%",
        vocab.LAYER_L2 not in [h.layer for h in layers.all_hits(a, pricey)],
    )

    # L3 alone.
    l3 = _candidate(eng, "G", fp.content_sha256(b"other"), None, None,
                    _source(fp, LINES[:2] + [("rewritten entirely", 1, 9)]),
                    [0.5, 0.4, 0.3, 0.2])
    hit = layers.strongest(a, l3)
    check("L3 fires on embedding cosine alone",
          hit is not None and hit.layer == vocab.LAYER_L3)
    check(
        "an uncorroborated L3 finding is capped at MEDIUM",
        hit is not None
        and vocab.severity_for(hit.layer, score=hit.score, corroborated=False)
        == vocab.SEVERITY_MEDIUM,
        "§5.9: embedding similarity is evidence, not proof",
    )

    # A pair whose vendors agree but whose geometry does not. Without this,
    # a mutant that removes the cosine threshold survives every other gate:
    # every case above either fires at a stronger layer or is excluded on the
    # vendor, so nothing anywhere would notice L3 saying yes to everything.
    near_miss = _candidate(
        eng, "N", fp.content_sha256(b"nearmiss"), "acme", None,
        _source(fp, unrelated_lines()), [0.62, 0.55, -0.40, 0.38],
    )
    cosine = fp.cosine(a.fingerprint.embedding, near_miss.fingerprint.embedding)
    check(
        "the near-miss fixture really is below the L3 threshold",
        cosine < vocab.L3_COSINE_MIN,
        f"cosine={cosine}",
    )
    check(
        "L3 declines a same-vendor pair below the cosine threshold",
        layers.evaluate_layer(
            vocab.LAYER_L3, a, near_miss, settings=layers.DEFAULT_SETTINGS
        )
        is None,
    )

    # Nothing fires.
    nothing = _candidate(eng, "H", fp.content_sha256(b"nothing"), "other", "X-1",
                         _source(fp, unrelated_lines()), [-0.9, 0.1, 0.0, 0.05])
    check("unrelated documents produce no finding", layers.strongest(a, nothing) is None)
    check("a document is never a duplicate of itself", layers.strongest(a, a) is None)

    # Evidence.
    for layer_hit in layers.all_hits(a, identical):
        check(
            f"{layer_hit.layer} attaches evidence",
            len(layer_hit.evidence) > 0
            and all("kind" in item for item in layer_hit.evidence),
        )
    l2_hit = layers.evaluate_layer(vocab.LAYER_L2, a, l2, settings=layers.DEFAULT_SETTINGS)
    check(
        "L2 evidence names the shingles that matched",
        l2_hit is not None
        and any(item.get("samples") for item in l2_hit.evidence),
    )
    l3_hit = layers.evaluate_layer(vocab.LAYER_L3, a, l3, settings=layers.DEFAULT_SETTINGS)
    check(
        "L3 evidence names the closest chunk pair with its text",
        l3_hit is not None
        and any(item.get("kind") == vocab.EVIDENCE_CHUNK_PAIR for item in l3_hit.evidence),
    )


def unrelated_lines() -> list[tuple[str, int, int]]:
    return [
        ("master services agreement", 1, 1),
        ("governing law karnataka arbitration", 1, 2),
        ("limitation of liability annual value", 1, 3),
    ]


def gate_price_surge(eng: dict[str, Any]) -> None:
    section("Robust z-score")
    surge, vocab = eng["surge"], eng["vocab"]
    import statistics

    history = _history(surge, STEADY, MONTHS)

    # Hand-computed: sorted STEADY has median 2_100_000; absolute deviations
    # sorted give a MAD of 10_000.
    prices = [item.unit_price_micros for item in history]
    check("median is hand-computed 2,100,000",
          surge.median(prices) == Decimal(2_100_000))
    check("MAD is hand-computed 10,000",
          surge.median_absolute_deviation(prices) == Decimal(10_000))
    check(
        "robust z is 0.6745 * (x - median) / MAD",
        surge.robust_z(2_249_000, Decimal(2_100_000), Decimal(10_000))
        == (Decimal("0.6745") * Decimal(149_000) / Decimal(10_000)).quantize(
            Decimal("0.00001")
        ),
    )

    # Minimum observations.
    few = surge.evaluate(
        surge.PriceObservation("n", date(2026, 9, 1), 2_600_000, "INR", "acme", "TN2"),
        history[:3],
        as_of=date(2026, 9, 1),
    )
    check("fewer than 5 observations produces nothing", not few.fired)

    # Minimum relative change: z is large, change is 7.1%.
    small = surge.evaluate(
        surge.PriceObservation("n", date(2026, 9, 1), 2_249_000, "INR", "acme", "TN2"),
        history,
        as_of=date(2026, 9, 1),
    )
    check(
        "a 7.1% change below the 10% floor does not fire despite |z| > 3.5",
        not small.fired and small.z is not None and abs(small.z) > Decimal("3.5"),
        f"z={small.z} change={small.relative_change}",
    )

    # The outlier argument.
    with_outlier = history + [
        surge.PriceObservation("spike", date(2026, 1, 15), 9_000_000, "INR", "acme", "TN2")
    ]
    robust = surge.evaluate(
        surge.PriceObservation("n", date(2026, 9, 1), 2_700_000, "INR", "acme", "TN2"),
        with_outlier,
        as_of=date(2026, 9, 1),
    )
    values = [item.unit_price_micros for item in with_outlier]
    classic = abs(
        (2_700_000 - statistics.mean(values)) / statistics.pstdev(values)
    )
    check(
        "median/MAD sees a surge that mean/stdev hides after one outlier",
        robust.fired and classic < 3.5,
        f"robust z={robust.z}, classic z={classic:.3f}",
    )

    # MAD = 0.
    flat = [
        surge.PriceObservation(f"f{i}", date(2026, i + 1, 1), 50_000_000_000,
                               "INR", "acme", "RET")
        for i in range(9)
    ]
    zero = surge.evaluate(
        surge.PriceObservation("n", date(2026, 10, 1), 56_000_000_000,
                               "INR", "acme", "RET"),
        flat,
        as_of=date(2026, 10, 1),
    )
    check("MAD = 0 is detected", zero.mad_micros == 0)
    check("MAD = 0 produces z of None, never inf", zero.z is None)
    check("MAD = 0 falls back to RELATIVE_ONLY",
          zero.basis == vocab.BASIS_RELATIVE_ONLY)
    check("MAD = 0 still fires on a 12% change", zero.fired)
    check(
        "MAD = 0 says so in the evidence",
        any("relative change alone" in str(item.get("note", ""))
            for item in zero.evidence),
    )
    check(
        "a RELATIVE_ONLY finding cannot reach HIGH severity",
        vocab.severity_for(vocab.LAYER_PRICE_SURGE, score=zero.score)
        != vocab.SEVERITY_HIGH,
        f"score={zero.score}",
    )
    tiny = surge.evaluate(
        surge.PriceObservation("n", date(2026, 10, 1), 50_100_000_000,
                               "INR", "acme", "RET"),
        flat,
        as_of=date(2026, 10, 1),
    )
    check("MAD = 0 with a 0.2% change does not fire", not tiny.fired)

    check(
        "the trailing window clamps day-of-month",
        surge.trailing_window_start(date(2026, 3, 31), 1) == date(2026, 2, 28),
    )
    check(
        "the trailing window is twelve calendar months",
        surge.trailing_window_start(date(2026, 3, 15), 12) == date(2025, 3, 15),
    )


def gate_drift(eng: dict[str, Any]) -> None:
    section("Contract drift via the ARCH-33 family parsers")
    fp, drift, vocab = eng["fp"], eng["drift"], eng["vocab"]

    def clause(index: int, text: str, vector: list[float]) -> Any:
        return fp.ChunkVector(
            chunk_id=f"c{index}", chunk_index=index, text=text,
            vector=vector, page_number=1 + index,
        )

    left = [
        clause(0, MSA_PAY, [1.0, 0.0, 0.0]),
        clause(1, MSA_LAW, [0.0, 1.0, 0.0]),
        clause(2, MSA_TERM, [0.0, 0.0, 1.0]),
    ]
    right = [
        clause(0, SOW_PAY, [0.99, 0.05, 0.0]),
        clause(1, SOW_LAW, [0.02, 0.99, 0.0]),
        clause(2, SOW_TERM, [0.0, 0.03, 0.99]),
    ]

    pairs = drift.align(left, right)
    check("alignment pairs each clause with its counterpart", len(pairs) == 3)
    check(
        "alignment is mutual-best (no left clause reused)",
        len({pair.right.chunk_id for pair in pairs}) == 3,
    )

    readings = drift.evaluate(left, right)
    changed = [r for r in readings if r.status == vocab.DRIFT_CHANGED]
    families = {r.family for r in changed}
    check(
        "payment terms drift is detected by the ARCH-33 parser",
        "duration_bound" in families,
    )
    check("notice period drift is detected", "notice_period" in families)
    check(
        "an unchanged governing-law clause is SAME, not a finding",
        any(r.family == "enumerated" and r.status == vocab.DRIFT_SAME
            for r in readings),
    )
    payment = next(r for r in changed if r.family == "duration_bound")
    check(
        "the drift finding carries both verbatim quotes",
        payment.evidence
        and "thirty" in payment.evidence[0]["subject"]["quote"]
        and "sixty" in payment.evidence[0]["counterpart"]["quote"],
    )
    check(
        "the parsed values are 30 and 60 days",
        str(payment.left.value) == "30" and str(payment.right.value) == "60"
        and payment.left.unit == "days",
    )

    unreadable = [
        clause(0, "5.2 Payment. Terms are as set out in the rate card annexed "
                  "hereto.", [1.0, 0.0, 0.0])
    ]
    out = drift.evaluate([left[0]], unreadable)
    check(
        "a side the family cannot read is UNDETERMINED, never a finding",
        all(r.status != vocab.DRIFT_CHANGED for r in out)
        and any(r.status == vocab.DRIFT_UNDETERMINED for r in out),
    )

    far_apart = [clause(0, SOW_TERM, [0.0, 0.0, 1.0])]
    orthogonal = [clause(0, SOW_PAY, [1.0, 0.0, 0.0])]
    check(
        "clauses below the 0.80 alignment threshold are not compared",
        drift.align(far_apart, orthogonal) == (),
    )
    check(
        "presence and absence are excluded from drift",
        "presence" not in drift.DRIFT_FAMILIES
        and "absence" not in drift.DRIFT_FAMILIES,
    )


def gate_findings_contract() -> None:
    section("Finding identity and the service-level mirror")
    sys.path.insert(0, str(HERE))
    source = (HERE / "app" / "services" / "radar" / "findings.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    for required in ("dedupe_key_for", "headline_for", "upsert", "emit_detected",
                     "FindingDraft", "UpsertResult"):
        check(f"findings.{required} exists", required in names)

    # dedupe_key stability and pair symmetry, computed the same way the module
    # does, without importing the ORM.
    import hashlib

    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    forward = hashlib.sha256("|".join(["L2", *sorted([a, b])]).encode()).hexdigest()
    reverse = hashlib.sha256("|".join(["L2", *sorted([b, a])]).encode()).hexdigest()
    check("dedupe key is symmetric in the pair", forward == reverse)
    check("dedupe key is 64 lowercase hex", len(forward) == 64)

    check(
        "upsert leaves a resolved finding's status alone",
        "STATUS IS NOT TOUCHED" in source,
        "reopening a dismissed finding is how a reviewer stops trusting the queue",
    )
    check(
        "upsert short-circuits on an unchanged input_digest",
        "if existing.input_digest == draft.input_digest" in source,
    )

    sweep = (HERE / "app" / "services" / "radar" / "sweep.py").read_text(
        encoding="utf-8"
    )
    check(
        "the sweep meters only when something was created or updated",
        "if not outcome.billable" in sweep and "(self.created + self.updated) > 0" in sweep,
    )
    check(
        "the sweep asks the suppression service before writing",
        sweep.count("suppressions_module.is_suppressed") >= 3,
        "duplicates, price surge and drift each need the lookup",
    )


def gate_three_places() -> None:
    section("The three-place rule")
    handlers = (HERE / "app" / "workers" / "handlers" / "__init__.py").read_text(
        encoding="utf-8"
    )
    profiles = (HERE / "app" / "workers" / "profiles.py").read_text(encoding="utf-8")
    scheduler = (HERE / "app" / "workers" / "scheduler.py").read_text(encoding="utf-8")

    for job in ("anomaly.scan_document", "anomaly.nightly"):
        check(f"{job} is in the handler map", f'"{job}":' in handlers)
        check(f"{job} is claimed by the LIGHT profile", f'"{job}",' in profiles)
    check(
        "anomaly.nightly is scheduled",
        'job_type="anomaly.nightly"' in scheduler,
    )
    check(
        "anomaly.scan_document is NOT scheduled",
        'job_type="anomaly.scan_document"' not in scheduler,
        "it is enqueued at ingest; a schedule would sweep with no document",
    )

    # Read through the AST rather than by slicing the text: `CAPABILITY_KEYS`
    # also appears in `__all__`, so a `.split()` lands on the wrong occurrence
    # and the gate reports a failure that is entirely its own.
    entitle_tree = ast.parse(
        (HERE / "app" / "core" / "entitlements.py").read_text(encoding="utf-8")
    )
    tuples: dict[str, list[str]] = {}
    for node in entitle_tree.body:
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
            node.targets[0], ast.Name
        ):
            target = node.targets[0].id
        if target in ("CAPABILITY_KEYS", "ADDON_KEYS") and isinstance(
            node.value, ast.Tuple
        ):
            tuples[target] = [
                element.id
                for element in node.value.elts
                if isinstance(element, ast.Name)
            ]
    check(
        "the capability is in CAPABILITY_KEYS",
        "ANOMALY_RADAR_CAPABILITY" in tuples.get("CAPABILITY_KEYS", []),
        str(tuples.get("CAPABILITY_KEYS")),
    )
    check(
        "the capability is NOT in ADDON_KEYS",
        "ANOMALY_RADAR_CAPABILITY" not in tuples.get("ADDON_KEYS", []),
        "entitlement_service asserts ADDON_KEYS equals its priced catalog at import",
    )
    gate_file = (HERE / "app" / "api" / "capability_gate.py").read_text(encoding="utf-8")
    check(
        "the capability has a display name",
        "ANOMALY_RADAR_CAPABILITY:" in gate_file,
        "without it the 402 body shows a key name to a customer",
    )
    usage = (HERE / "app" / "core" / "usage_events.py").read_text(encoding="utf-8")
    check('radar.sweep is a REQUEST meter',
          'name="radar.sweep"' in usage and "UsageUnit.REQUEST" in usage)
    webhooks = (HERE / "app" / "core" / "webhook_events.py").read_text(encoding="utf-8")
    check("anomaly.detected is publishable", '"anomaly.detected",' in webhooks)
    router = (HERE / "app" / "api" / "v1" / "router.py").read_text(encoding="utf-8")
    check("the anomalies router is mounted",
          "api_router.include_router(anomalies.router)" in router)


# ===========================================================================
# Live database gates
# ===========================================================================


def gate_db() -> None:
    section("Live Postgres invariants")
    try:
        from sqlalchemy import text as sql

        from app.db.session import SessionLocal
    except Exception as exc:  # noqa: BLE001
        check("database import", False, str(exc))
        return

    with SessionLocal() as db:
        head = db.execute(
            sql("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        # ARCH35-S1:head-widened-34. ARCH-35 moves the head forward; this gate
        # certifies that ARCH-34's schema is present at or after its own head.
        check(
            "alembic head is arch34_step1_radar or later",
            # ARCH39-S1:head-widened-34
            head in ("arch34_step1_radar", "arch35_step1_calibration", "arch39_step1_conversations", "arch37_step1_flow_builder"),  # ARCH37-S1:head-widened-34
            str(head),
        )

        cols = dict(
            db.execute(
                sql(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name = 'anomaly_findings'"
                )
            ).all()
        )
        check("pair_lo and pair_hi exist", "pair_lo" in cols and "pair_hi" in cols)

        generated = db.execute(
            sql(
                "SELECT is_generated FROM information_schema.columns "
                "WHERE table_name='anomaly_findings' AND column_name='pair_lo'"
            )
        ).scalar_one_or_none()
        check("pair_lo is GENERATED ALWAYS", generated == "ALWAYS")

        mh = db.execute(
            sql(
                "SELECT udt_name FROM information_schema.columns "
                "WHERE table_name='document_fingerprints' AND column_name='minhash'"
            )
        ).scalar_one_or_none()
        check("minhash is bigint[]", mh == "_int8", str(mh))

        # The two constraints that carry the phase, driven inside a rolled-back
        # transaction.
        def refuses(statement: str, name: str, why: str) -> None:
            savepoint = db.begin_nested()
            try:
                db.execute(sql(statement))
                savepoint.rollback()
                check(name, False, "the INSERT was accepted")
            except Exception as exc:  # noqa: BLE001
                savepoint.rollback()
                check(name, why in str(exc), str(exc)[:120])

        org = db.execute(sql("SELECT id FROM organizations LIMIT 1")).scalar_one_or_none()
        ws = db.execute(sql("SELECT id FROM workspaces LIMIT 1")).scalar_one_or_none()
        items = db.execute(sql("SELECT id FROM work_items LIMIT 2")).scalars().all()
        if org is None or ws is None or len(items) < 2:
            check("db fixtures present", False, "need an org, a workspace and 2 work items")
            return

        base = (
            "INSERT INTO anomaly_findings (organization_id, workspace_id, kind, "
            "layer, severity, subject_work_item_id, counterpart_work_item_id, "
            "score, headline, metrics, evidence, dedupe_key, input_digest, "
            "engine_version) VALUES "
        )
        digest = "a" * 64

        refuses(
            base
            + f"('{org}','{ws}','DUPLICATE_DOCUMENT','L2','HIGH','{items[0]}',NULL,"
            f"0.9,'x','{{}}'::jsonb,'[{{\"kind\":\"identifiers\"}}]'::jsonb,"
            f"'{digest}','{digest}','arch34.1')",
            "SQL refuses a duplicate finding with a null counterpart",
            "ck_af_pairwise_has_counterpart",
        )
        refuses(
            base
            + f"('{org}','{ws}','DUPLICATE_DOCUMENT','L2','HIGH','{items[0]}',"
            f"'{items[1]}',0.9,'x','{{}}'::jsonb,'[]'::jsonb,'{digest}','{digest}',"
            "'arch34.1')",
            "SQL refuses a finding with empty evidence",
            "ck_af_evidence_present",
        )
        refuses(
            base
            + f"('{org}','{ws}','PRICE_SURGE','L2','HIGH','{items[0]}','{items[1]}',"
            f"0.9,'x','{{}}'::jsonb,'[{{\"kind\":\"identifiers\"}}]'::jsonb,"
            f"'{digest}','{digest}','arch34.1')",
            "SQL refuses a kind that disagrees with its layer",
            "ck_af_kind_matches_layer",
        )
        refuses(
            base
            + f"('{org}','{ws}','DUPLICATE_DOCUMENT','L0','HIGH','{items[0]}',"
            f"'{items[0]}',1.0,'x','{{}}'::jsonb,'[{{\"kind\":\"identifiers\"}}]'::jsonb,"
            f"'{digest}','{digest}','arch34.1')",
            "SQL refuses a document as a duplicate of itself",
            "ck_af_counterpart_is_not_subject",
        )

        # The canonical pair ordering: (A,B) then (B,A) at the same layer.
        savepoint = db.begin_nested()
        try:
            row = (
                f"('{org}','{ws}','DUPLICATE_DOCUMENT','L0','HIGH',%s,%s,1.0,'x',"
                f"'{{}}'::jsonb,'[{{\"kind\":\"identifiers\"}}]'::jsonb,"
                f"'{digest}','{digest}','arch34.1')"
            )
            db.execute(sql(base + row % (f"'{items[0]}'", f"'{items[1]}'")))
            try:
                db.execute(sql(base + row % (f"'{items[1]}'", f"'{items[0]}'")))
                check("(A,B) and (B,A) cannot both be stored", False,
                      "the reversed pair was accepted")
            except Exception as exc:  # noqa: BLE001
                check(
                    "(A,B) and (B,A) cannot both be stored",
                    "uq_af_pair_layer" in str(exc),
                    str(exc)[:120],
                )
        finally:
            savepoint.rollback()


# ===========================================================================
# Mutation gates
# ===========================================================================


MUTANTS: list[tuple[str, str, str, str, bool]] = [
    (
        "L2 threshold comparison inverted",
        "app/services/radar/layers/l2_minhash.py",
        "if estimate < settings.l2_jaccard_min:",
        "if estimate > settings.l2_jaccard_min:",
        True,
    ),
    (
        "z-score uses mean and standard deviation",
        "app/services/radar/price_surge.py",
        "    ordered = sorted(Decimal(value) for value in values)\n"
        "    middle = len(ordered) // 2\n"
        "    if len(ordered) % 2 == 1:\n"
        "        return ordered[middle]\n"
        "    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)",
        "    total = sum(Decimal(value) for value in values)\n"
        "    return total / Decimal(len(values))",
        True,
    ),
    (
        "the strongest-layer rule removed (every layer reports)",
        "app/services/radar/layers/__init__.py",
        "        hit = evaluate_layer(layer, subject, counterpart, settings=settings)\n"
        "        if hit is not None:\n"
        "            return hit\n"
        "    return None",
        "        hit = evaluate_layer(layer, subject, counterpart, settings=settings)\n"
        "        if hit is not None:\n"
        "            weakest = hit\n"
        "    return locals().get('weakest')",
        True,
    ),
    (
        "evidence assembled but not attached",
        "app/services/radar/layers/l2_minhash.py",
        "        evidence=tuple(evidence),",
        "        evidence=(evidence[0],) if evidence else (),",
        False,  # CONTROL-adjacent: still non-empty, so it must NOT be a kill
    ),
    (
        "L3 cosine threshold removed",
        "app/services/radar/layers/l3_embedding.py",
        "    if similarity < settings.l3_cosine_min:",
        "    if similarity < Decimal('0'):",
        True,
    ),
    (
        "MAD = 0 returns infinity instead of None",
        "app/services/radar/price_surge.py",
        "    if mad == 0:\n        return None",
        "    if mad == 0:\n        mad = Decimal('0.000001')",
        True,
    ),
    (
        "CONTROL — a comment reworded",
        "app/services/radar/price_surge.py",
        "WHY MEDIAN AND MAD RATHER THAN MEAN AND STANDARD DEVIATION",
        "WHY THE MEDIAN AND THE MAD, RATHER THAN MEAN AND STANDARD DEVIATION",
        False,
    ),
]


def gate_mutations() -> None:
    section("Mutation kills")
    for name, relpath, old, new, must_die in MUTANTS:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tree"
            shutil.copytree(HERE / "app", root / "app")
            target = root / relpath
            source = target.read_text(encoding="utf-8")
            if old not in source:
                check(f"mutant applies: {name}", False, "anchor not found")
                continue
            target.write_text(source.replace(old, new, 1), encoding="utf-8")

            died = _mutant_dies(root)
            if must_die:
                check(f"mutant DIES: {name}", died, "it survived every gate")
            else:
                check(f"CONTROL survives: {name}", not died,
                      "a control that dies means the harness reports noise")


def _mutant_dies(root: Path) -> bool:
    """Run the offline gates against a mutated tree; True if any fails."""
    global PASSED, FAILED, FAILURES
    saved = (PASSED, FAILED, list(FAILURES))
    devnull = open(os.devnull, "w", encoding="utf-8")
    original = sys.stdout
    sys.stdout = devnull
    try:
        PASSED, FAILED, FAILURES = 0, 0, []
        try:
            eng = _engines(root)
            gate_minhash(eng)
            gate_layers(eng)
            gate_price_surge(eng)
            gate_drift(eng)
        except Exception:  # noqa: BLE001 - a mutant that raises is a mutant that died
            return True
        return FAILED > 0
    finally:
        sys.stdout = original
        devnull.close()
        PASSED, FAILED, FAILURES = saved[0], saved[1], saved[2]
        _engines(HERE)


# ===========================================================================
# Regressions
# ===========================================================================


def gate_regressions() -> None:
    section("Regressions")
    for script in (
        "verify_arch33.py",
        "verify_arch32.py",
        "verify_arch31.py",
        "verify_arch31_step0.py",
    ):
        path = HERE / script
        if not path.exists():
            check(f"{script} present", False, "not found")
            continue
        result = subprocess.run(
            [sys.executable, str(path)], capture_output=True, text=True, cwd=str(HERE)
        )
        check(f"{script} passes", result.returncode == 0,
              (result.stdout or result.stderr)[-200:])


# ===========================================================================
# Entry point
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-34 gates")
    parser.add_argument("--db", action="store_true", help="run live Postgres gates")
    parser.add_argument("--mutate", action="store_true", help="run mutation kills")
    parser.add_argument(
        "--regressions", action="store_true", help="run prior phases' gates"
    )
    args = parser.parse_args()

    print("ARCH-34 — Cross-Document Anomaly & Duplicate Ingestion Radar")
    print(f"tree: {HERE}")

    engines = _engines(HERE)
    gate_vocabulary(engines)
    gate_purity(HERE)
    gate_minhash(engines)
    gate_layers(engines)
    gate_price_surge(engines)
    gate_drift(engines)
    gate_findings_contract()
    gate_three_places()

    if args.db:
        gate_db()
    if args.mutate:
        gate_mutations()
    if args.regressions:
        gate_regressions()

    print(f"\n{PASSED} passed, {FAILED} failed")
    if FAILURES:
        print("\nFailures:")
        for failure in FAILURES:
            print(f"  - {failure}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
