#!/usr/bin/env python3
"""ARCH-35 — Calibrated Autonomy & Conformal Risk Control: the gates.

    python verify_arch35.py                   offline
    python verify_arch35.py --db              + live Postgres invariants and an
                                                end-to-end lifecycle, rolled back
    python verify_arch35.py --mutate          + mutation kills
    python verify_arch35.py --db --mutate     everything
    python verify_arch35.py --skip-regressions

Regressions run by default: verify_arch34, verify_arch33 (unmodified),
verify_arch32, verify_arch31, verify_arch31_step0 — with --db when --db is set.

EXIT 0 pass | 1 a gate failed | 2 harness could not run

WHAT THE STATISTICAL GATES CLAIM, AND WHAT THEY DO NOT
======================================================

Split conformal risk control bounds the EXPECTED share of documents that are
approved automatically and wrong. It does not promise that bound for every
individual calibration set with 95% probability. So:

  * the conformal gate checks the MEAN true risk over 100 seeds against α,
    with the true risk computed analytically from the generating process;
  * the 95%-confidence claim is gated where it lives — on the Clopper-Pearson
    upper bound — as coverage of the true conditional error over 100 seeds,
    with a binomial tolerance: a procedure that covers 95% of the time covers
    fewer than 90 of 100 seeds with probability below 3%.

The fraction of seeds whose realized risk exceeds α is PRINTED, not asserted.
It is typically 10-20%, and a gate demanding "at most 5 of 100" would fail a
correct implementation.

WHY THE OFFLINE GATES LOAD MODULES THROUGH NAMESPACE STUBS
==========================================================

Same arrangement as verify_arch33 and verify_arch34: `app/services/__init__.py`
imports the world. `_stub_packages(root)` seeds `sys.modules` with packages
whose `__path__` points at `root`, so the pure engines load without settings,
and pointing `root` at a mutated copy is what makes `--mutate` reach them.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import importlib.util
import inspect
import math
import os
import re
import shutil
import subprocess
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import tempfile
import traceback
import types
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

MIGRATION = "alembic/versions/arch35_step1_calibration.py"
MODEL = "app/models/calibration.py"
CAL_PKG = "app/services/calibration"
PURE_MODULES = (
    "vocabulary.py",
    "estimators.py",
    "fit.py",
    "risk.py",
    "monitor.py",
    "sampling.py",
    "decision.py",
)
ASSERTION_CALIBRATION = "app/services/assertions/calibration.py"
ASSERTION_ROUTING = "app/services/assertions/routing.py"
ASSERTION_TRIAGE = "app/services/assertions/triage.py"
VERIFICATION_SERVICE = "app/services/document_verification_service.py"
APPLY_SCRIPT = "apply_arch35.py"
API_MODULE = "app/api/v1/autonomy.py"

#: sha256 of ARCH-33's routing.py with BOM stripped and CRLF folded to LF.
#: "routing.py in assertions remains untouched" is a byte-level claim.
ROUTING_SHA256 = "77ce6408fbf418eb1a8906372c21b4ae3df6e37eb6b66f44435ba6d970481681"

FE_PAGE = "src/pages/autonomy/AutonomySettings.tsx"
FE_LOCK = "src/components/autonomy/AutonomyLockCard.tsx"
FE_CHARTS = "src/components/autonomy/AutonomyCharts.tsx"
FE_API = "src/services/api/autonomy.ts"
FE_TYPES = "src/types/autonomy.ts"

REGRESSIONS = (
    "verify_arch34.py",
    "verify_arch33.py",
    "verify_arch32.py",
    "verify_arch31.py",
    "verify_arch31_step0.py",
)


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            return False
        except Exception as exc:  # noqa: BLE001
            self.results.append(
                (name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            )
            return False
        self.results.append((name, True, ""))
        return True

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)

    def report(self, title: str) -> None:
        print(f"\n--- {title} ---")
        for name, ok, detail in self.results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            if not ok and detail:
                for line in detail.splitlines()[:8]:
                    print(f"         {line}")
        print(f"  {len(self.results) - self.failed}/{len(self.results)} passed")


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing file: {path}")
    return path.read_text(encoding="utf-8-sig")


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _normalised_sha256(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


FORBIDDEN_IMPORTS: tuple[str, ...] = (
    "sqlalchemy",
    "redis",
    "requests",
    "httpx",
    "boto3",
    "app.core.config",
    "app.db",
    "app.models",
    "app.api",
    "app.services.byok",
    "app.services.llm_service",
    "app.services.calibration.labels",
    "app.services.calibration.refit",
    "app.services.calibration.apply",
    "app.services.calibration.overview",
)

FORBIDDEN_CALLS: tuple[str, ...] = (
    "datetime.now",
    "datetime.utcnow",
    "time.time",
    "random.random",
    "uuid.uuid4",
    "np.random.default_rng",
    "numpy.random.default_rng",
)


def assert_module_is_pure(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
            imported.extend(f"{node.module}.{a.name}" for a in node.names)
    for name in imported:
        for forbidden in FORBIDDEN_IMPORTS:
            assert not (name == forbidden or name.startswith(forbidden + ".")), (
                f"{path.name} imports {name!r}; a pure engine may not."
            )
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts: list[str] = []
        current: Any = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        dotted = ".".join(reversed(parts))
        assert dotted not in FORBIDDEN_CALLS, (
            f"{path.name} calls {dotted}(); a clock or random source in a pure "
            "engine makes the same inputs stop producing the same answer."
        )


def _stub_packages(root: Path) -> None:
    for cached in [
        name
        for name in list(sys.modules)
        if name.startswith("app.services.calibration")
        or name.startswith("app.services.assertions")
        or name in ("app.core.normalize",)
    ]:
        sys.modules.pop(cached, None)
    for parent in ("app.services", "app.core", "app"):
        existing = sys.modules.get(parent)
        if existing is not None and getattr(existing, "_arch35_stub", False):
            sys.modules.pop(parent, None)
    for dotted, path in (
        ("app", root / "app"),
        ("app.core", root / "app" / "core"),
        ("app.services", root / "app" / "services"),
    ):
        existing = sys.modules.get(dotted)
        if existing is not None and getattr(existing, "_arch35_stub", False):
            existing.__path__ = [str(path)]  # type: ignore[attr-defined]
            continue
        if existing is not None and dotted != "app":
            continue
        stub = types.ModuleType(dotted)
        stub.__path__ = [str(path)]  # type: ignore[attr-defined]
        stub._arch35_stub = True  # type: ignore[attr-defined]
        sys.modules[dotted] = stub


def _engines(root: Path) -> dict[str, Any]:
    _stub_packages(root)
    importlib.invalidate_caches()
    names = {
        "vocab": "app.services.calibration.vocabulary",
        "est": "app.services.calibration.estimators",
        "fit": "app.services.calibration.fit",
        "risk": "app.services.calibration.risk",
        "monitor": "app.services.calibration.monitor",
        "sampling": "app.services.calibration.sampling",
        "decision": "app.services.calibration.decision",
        "a_vocab": "app.services.assertions.vocabulary",
        "a_cal": "app.services.assertions.calibration",
        "a_routing": "app.services.assertions.routing",
    }
    return {key: importlib.import_module(dotted) for key, dotted in names.items()}


def _module_literals(source: str) -> dict[str, Any]:
    tree = ast.parse(source)
    found: dict[str, Any] = {}
    for node in tree.body:
        targets: list[str] = []
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        else:
            continue
        if value is None:
            continue
        try:
            found.update({name: ast.literal_eval(value) for name in targets})
        except (ValueError, SyntaxError):
            continue
    return found


# ===========================================================================
# Synthetic populations with KNOWN truth
# ===========================================================================


def _cubic_population(n: int, seed: int) -> tuple[list[float], list[bool]]:
    """P(correct | s) = s³ — a raw score that is badly over-confident."""
    import numpy as np

    rng = np.random.default_rng(seed)
    s = rng.uniform(0.0, 1.0, n)
    y = rng.uniform(0.0, 1.0, n) < s ** 3
    return [float(v) for v in s], [bool(v) for v in y]


def _cubic_truth(threshold_score: float) -> tuple[float, float]:
    """For documents with s >= s*: (share passed, error among them), exactly."""
    s = min(max(threshold_score, 0.0), 0.999999)
    share = 1.0 - s
    conditional = 1.0 - (1.0 - s ** 4) / (4.0 * (1.0 - s))
    return share, conditional


def _sigmoid_population(n: int, seed: int) -> tuple[list[float], list[bool]]:
    """P(correct | s) = σ(6s − 3) — exactly the family Platt fits."""
    import numpy as np

    rng = np.random.default_rng(seed)
    s = rng.uniform(0.0, 1.0, n)
    y = rng.uniform(0.0, 1.0, n) < 1.0 / (1.0 + np.exp(-(6.0 * s - 3.0)))
    return [float(v) for v in s], [bool(v) for v in y]


def _threshold_score(eng: dict[str, Any], fitted: Any, lam: float) -> float:
    """The smallest raw score whose calibrated probability reaches λ."""
    lo, hi = 0.0, 1.0
    if eng["est"].evaluate(fitted.method, fitted.parameters, 1.0) < lam:
        return 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if eng["est"].evaluate(fitted.method, fitted.parameters, mid) >= lam:
            hi = mid
        else:
            lo = mid
    return hi


# ===========================================================================
# Offline — vocabulary, schema text, purity
# ===========================================================================


def gates_vocabulary(rec: Recorder, eng: dict[str, Any], *, root: Path = BACKEND) -> None:
    vocab = eng["vocab"]
    migration = _read(root / MIGRATION)
    model = _read(root / MODEL)

    def revision_chain() -> None:
        assert 'revision = "arch35_step1_calibration"' in migration
        assert 'down_revision = "arch34_step1_radar"' in migration, (
            "the migration must chain off the certified head arch34_step1_radar"
        )

    rec.check("migration chains off arch34_step1_radar", revision_chain)

    def copies_equal() -> None:
        declared = _module_literals(migration)
        for name in (
            "METHODS",
            "STATUSES",
            "LIVE_STATUSES",
            "DECISION_TYPES",
            "SOURCE_TABLES",
            "MIN_LABELS_PLATT",
            "MIN_LABELS_ISOTONIC",
            "TARGET_ERROR_RATE_MAX",
            "DEFAULT_AUDIT_SAMPLE_RATE",
            "AUDIT_SAMPLE_RATE_MIN",
            "AUDIT_SAMPLE_RATE_MAX",
        ):
            assert name in declared, f"the migration does not declare {name}"
            mine = getattr(vocab, name)
            theirs = declared[name]
            if isinstance(mine, tuple):
                theirs = tuple(theirs)
            assert theirs == mine, f"migration {name} {theirs!r} != vocabulary {mine!r}"

    rec.check("every closed vocabulary equals the migration's copy", copies_equal)

    def arch33_copies_equal() -> None:
        a = eng["a_vocab"]
        assert vocab.ASSERTION_FAMILIES == a.FAMILIES, (
            f"{vocab.ASSERTION_FAMILIES} != ARCH-33 {a.FAMILIES}"
        )
        assert vocab.MIN_LABELS_PLATT == a.MIN_LABELS_FOR_CALIBRATION, (
            "ARCH-35's cold-start floor must equal ARCH-33's"
        )
        assert vocab.ASSERTION_FIELD_PATH_PREFIX == a.FIELD_PATH_PREFIX
        assert set(vocab.ASSERTION_DECISION_TYPES) <= set(vocab.DECISION_TYPES)
        assert set(vocab.AUTOMATED_DECISION_TYPES) <= set(vocab.DECISION_TYPES)

    rec.check("ARCH-33 families, cold-start floor and field prefix agree", arch33_copies_equal)

    def constraints_declared() -> None:
        for name in (
            "ck_cm_method_known",
            "ck_cm_status_known",
            "ck_cm_improves",
            "ck_cm_alpha_bounded",
            "ck_cm_method_needs_labels",
            "ck_cm_prior_iff_cold",
            "ck_cm_suspension_has_reason",
            "ck_cm_promise_backed",
            "ck_cm_audit_rate_bounded",
            "ck_cl_decision_known",
            "ck_cl_audit_was_automatic",
            "ck_cl_weight_is_audit_inverse",
            "uq_cm_active",
            "uq_cl_source",
        ):
            assert name in migration, f"{name} is not in the migration"
            assert name in model, f"{name} is not in the ORM model"
        assert "fk_ae_calibration_model" in migration
        assert 'ondelete="SET NULL"' in migration

    rec.check("every constraint is declared in both migration and model", constraints_declared)

    def no_name_collides_with_arch33_probe() -> None:
        # verify_arch33 --db takes the FIRST constraint LIKE '%type_known%'.
        names = re.findall(r'name="([a-z0-9_]+)"', migration)
        for name in names:
            assert "type_known" not in name and "branch_known" not in name, (
                f"{name} would be picked up by verify_arch33's LIKE probe"
            )

    rec.check("no constraint name matches ARCH-33's LIKE probes", no_name_collides_with_arch33_probe)

    def statistics_in_sql() -> None:
        body = " ".join(_strip_comments(migration).split())
        assert "status = 'REJECTED' OR ece_after <= ece_before" in body
        assert "target_error_rate > 0 AND target_error_rate <=" in body
        assert "(method = 'PRIOR') = (label_count <" in body

    rec.check("ECE, α and the label bands are enforced in SQL", statistics_in_sql)


def gates_purity(rec: Recorder, *, root: Path = BACKEND) -> None:
    def pure() -> None:
        for name in PURE_MODULES:
            assert_module_is_pure(root / CAL_PKG / name)
        assert_module_is_pure(root / ASSERTION_CALIBRATION)
        init = ast.parse(_read(root / CAL_PKG / "__init__.py"))
        assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(init)), (
            "app/services/calibration/__init__.py imports something; ARCH-33's "
            "offline loader executes it"
        )

    rec.check("the seven pure modules, ARCH-33's calibration and the package init stay pure", pure)


# ===========================================================================
# Offline — estimators
# ===========================================================================


def gates_estimators(rec: Recorder, eng: dict[str, Any]) -> None:
    est, fit, vocab = eng["est"], eng["fit"], eng["vocab"]

    def selection_by_count() -> None:
        expected = {
            0: "PRIOR", 49: "PRIOR", 50: "PLATT", 199: "PLATT", 200: "ISOTONIC",
        }
        for count, method in expected.items():
            got = est.select_method(count)
            assert got == method, f"{count} labels selected {got}, expected {method}"
            examples = [
                fit.Example(raw_score=(i % 97) / 96, correct=(i % 3 != 0))
                for i in range(count)
            ]
            outcome = fit.fit_decision(examples, target_error_rate=0.05)
            assert outcome.method == method, (
                f"fit_decision on {count} labels used {outcome.method}"
            )

    rec.check("0/49/50/199/200 labels select PRIOR/PRIOR/PLATT/PLATT/ISOTONIC", selection_by_count)

    def monotone_and_inside() -> None:
        grid = [i / 400 for i in range(401)]
        datasets = [
            _cubic_population(3000, 11),
            _sigmoid_population(120, 12),
            # Score and correctness INVERSELY related: an unconstrained Platt
            # fit would slope down. The slope must be clamped at zero.
            ([i / 99 for i in range(100)], [i < 60 for i in range(100)]),
            # Perfectly separable.
            ([0.9] * 60 + [0.1] * 20, [True] * 60 + [False] * 20),
            # Constant score.
            ([0.7] * 250, [i % 4 != 0 for i in range(250)]),
        ]
        for scores, labels in datasets:
            for fitted in (
                est.fit_isotonic(scores, labels),
                est.fit_platt(scores, labels),
            ):
                values = [est.evaluate(fitted.method, fitted.parameters, g) for g in grid]
                for a, b in zip(values, values[1:]):
                    assert b >= a - 1e-12, (
                        f"{fitted.method} decreased from {a} to {b}; "
                        f"parameters {dict(fitted.parameters)}"
                    )
                assert all(0.0 < v < 1.0 for v in values), (
                    f"{fitted.method} left the open unit interval"
                )
                assert est.parameters_are_valid(fitted.method, fitted.parameters)

    rec.check("every estimator is monotone and strictly inside (0, 1)", monotone_and_inside)

    def isotonic_matches_sklearn() -> None:
        from sklearn.isotonic import IsotonicRegression

        scores, labels = _cubic_population(4000, 21)
        fitted = est.fit_isotonic(scores, labels)
        reference = IsotonicRegression(
            y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip"
        ).fit(scores, labels)
        grid = [i / 1000 for i in range(-50, 1051)]
        predicted = reference.predict([min(1.0, max(0.0, g)) for g in grid])
        for g, p in zip(grid, predicted):
            mine = est.evaluate(fitted.method, fitted.parameters, g)
            assert abs(mine - est.clamp(float(p))) < 1e-5, (
                f"at {g}: stored-breakpoint evaluation {mine} != sklearn {p}"
            )

    rec.check("isotonic evaluation from stored breakpoints equals sklearn predict(clip)", isotonic_matches_sklearn)

    def ece_targets() -> None:
        scores, labels = _cubic_population(20000, 31)
        iso = est.fit_isotonic(scores, labels)
        test_s, test_y = _cubic_population(100000, 32)
        iso_ece = fit.expected_calibration_error(est.evaluate_many(iso, test_s), test_y)
        raw_ece = fit.expected_calibration_error(test_s, test_y)
        assert iso_ece < 0.02, f"isotonic ECE {iso_ece:.4f} is not below 0.02"
        assert raw_ece > 0.2, f"the synthetic miscalibration is too mild ({raw_ece})"

        scores, labels = _sigmoid_population(3000, 33)
        platt = est.fit_platt(scores, labels)
        test_s, test_y = _sigmoid_population(100000, 34)
        platt_ece = fit.expected_calibration_error(est.evaluate_many(platt, test_s), test_y)
        assert platt_ece < 0.04, f"Platt ECE {platt_ece:.4f} is not below 0.04"

    rec.check("on known miscalibration isotonic ECE < 0.02 and Platt ECE < 0.04", ece_targets)

    def refusal_rule() -> None:
        import numpy as np

        seen_rejected = False
        for seed in range(200):
            rng = np.random.default_rng(1000 + seed)
            s = rng.uniform(0.0, 1.0, 60)
            y = rng.uniform(0.0, 1.0, 60) < s  # already calibrated
            outcome = fit.fit_decision(
                [fit.Example(float(a), bool(b)) for a, b in zip(s, y)],
                target_error_rate=0.05,
            )
            improved = outcome.ece_after <= outcome.ece_before
            assert (outcome.status == vocab.STATUS_ACTIVE) == improved, (
                f"status {outcome.status} disagrees with ECE "
                f"{outcome.ece_after} vs {outcome.ece_before}"
            )
            if not improved:
                seen_rejected = True
                assert not outcome.usable and not outcome.auto_allowed
        assert seen_rejected, (
            "no already-calibrated sample produced a worse fit; the gate cannot "
            "show the refusal path is live"
        )

    rec.check("a fit that makes held-out ECE worse is REJECTED, never usable", refusal_rule)

    def split_is_deterministic() -> None:
        examples = [fit.Example((i * 37 % 101) / 100, i % 3 == 0) for i in range(250)]
        a_fit, a_hold = fit.split(examples)
        b_fit, b_hold = fit.split(list(reversed(examples)))
        assert a_hold == b_hold and a_fit == b_fit
        assert len(a_hold) == 250 // vocab.HOLDOUT_EVERY
        digest_a = fit.input_digest(examples, target_error_rate="0.05", audit_sample_rate="0.02")
        digest_b = fit.input_digest(list(reversed(examples)), target_error_rate="0.050", audit_sample_rate="0.0200")
        assert digest_a == digest_b and re.fullmatch(r"[0-9a-f]{64}", digest_a)
        assert digest_a != fit.input_digest(examples, target_error_rate="0.04", audit_sample_rate="0.02")

    rec.check("the split and the input digest depend on the examples alone", split_is_deterministic)


# ===========================================================================
# Offline — conformal risk control and Clopper-Pearson
# ===========================================================================

#: Nine held-out examples, scored and labelled by hand.
HAND_P = [0.95, 0.9, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]
HAND_WRONG = [False, False, True, False, True, False, True, True, False]

#: (α, achievable, λ, bound, auto share) computed on paper.
#:
#:   λ      k   (k+1)/(n+1)    k/n (no correction)
#:   0.95   0   0.1            0.000
#:   0.90   1   0.2            0.111
#:   0.80   1   0.2            0.111
#:   0.70   2   0.3            0.222
#:   0.60   2   0.3            0.222
#:   0.50   3   0.4            0.333
#:
#: At α = 0.25 the corrected bound stops at 0.80; the uncorrected one would
#: run on to 0.60. That row is the one the `+1` mutant cannot survive.
HAND_TABLE = (
    (0.25, True, 0.80, 0.2, 4 / 9),
    (0.30, True, 0.60, 0.3, 6 / 9),
    (0.10, True, 0.95, 0.1, 1 / 9),
    (0.09, False, 1.00, 0.1, 0.0),
)


def gates_risk(rec: Recorder, eng: dict[str, Any]) -> None:
    risk = eng["risk"]

    def hand_table() -> None:
        for alpha, achievable, lam, bound, share in HAND_TABLE:
            control = risk.conformal_threshold(HAND_P, HAND_WRONG, alpha)
            assert control.achievable is achievable, (alpha, control)
            assert math.isclose(control.threshold, lam), (
                f"α={alpha}: λ={control.threshold}, hand-computed {lam}"
            )
            assert math.isclose(control.conformal_bound, bound), (
                f"α={alpha}: bound={control.conformal_bound}, hand-computed {bound}"
            )
            assert math.isclose(control.auto_share, share), (
                f"α={alpha}: auto share {control.auto_share}, hand-computed {share}"
            )
        assert math.isclose(risk.conformal_bound(0, 9), 0.1)
        assert math.isclose(risk.conformal_bound(2, 9), 0.3)
        refused = risk.conformal_threshold(HAND_P, HAND_WRONG, 0.09)
        assert "not achievable" in refused.reason, refused.reason

    rec.check("conformal λ matches the hand-computed table, +1 correction included", hand_table)

    def eligibility_and_weights() -> None:
        # The wrong answer at 0.9 is an assertion FAIL: labelled, never auto-approvable.
        eligible = [True, True, False, True, True, True, True, True, True]
        control = risk.conformal_threshold(HAND_P, HAND_WRONG, 0.25, eligible=eligible)
        # k now counts only the 0.7 error: λ runs to 0.7 (k=1 → 0.2), stops at 0.5.
        assert math.isclose(control.threshold, 0.6), control
        assert math.isclose(control.auto_share, 5 / 9), control

        # Weight 2 on an example is the same as that example twice.
        weighted = risk.conformal_threshold(
            HAND_P, HAND_WRONG, 0.3, weights=[1, 1, 1, 1, 2, 1, 1, 1, 1]
        )
        duplicated = risk.conformal_threshold(
            HAND_P + [0.7], HAND_WRONG + [True], 0.3
        )
        assert math.isclose(weighted.threshold, duplicated.threshold)
        assert math.isclose(weighted.conformal_bound, duplicated.conformal_bound)
        assert math.isclose(weighted.auto_share, duplicated.auto_share)

    rec.check("ineligible examples count in n only; weight w equals w copies", eligibility_and_weights)

    def clopper_pearson_known_values() -> None:
        upper = risk.clopper_pearson_upper(0, 4)
        assert math.isclose(upper, 1 - 0.05 ** 0.25, rel_tol=1e-9), upper
        assert upper > 0.5, (
            f"0 errors over 4 trials reported an upper bound of {upper}. Four "
            "clean automatic passes are not evidence of a 0% error rate."
        )
        assert math.isclose(risk.clopper_pearson_upper(0, 300), 1 - 0.05 ** (1 / 300), rel_tol=1e-9)
        assert risk.clopper_pearson_upper(0, 0) == 1.0
        assert risk.clopper_pearson_upper(7, 7) == 1.0
        for k, m in ((1, 20), (5, 100), (12, 40), (0, 10)):
            u = risk.clopper_pearson_upper(k, m)
            tail = sum(math.comb(m, i) * u ** i * (1 - u) ** (m - i) for i in range(k + 1))
            assert abs(tail - 0.05) < 1e-7, (
                f"P(X <= {k} | {m}, {u}) = {tail}; the one-sided 95% upper bound "
                "must leave exactly 5% below"
            )
            assert u > k / m

    rec.check("Clopper-Pearson matches known values; 0 of 4 is not 0%", clopper_pearson_known_values)

    def curve_is_consistent() -> None:
        curve = risk.coverage_curve(HAND_P, HAND_WRONG)
        bounds = [p.conformal_bound for p in curve]
        shares = [p.auto_share for p in curve]
        assert bounds == sorted(bounds) and shares == sorted(shares)
        for alpha, achievable, lam, _bound, _share in HAND_TABLE:
            chosen = None
            for point in curve:
                if point.conformal_bound <= alpha:
                    chosen = point
                else:
                    break
            if achievable:
                assert chosen is not None and math.isclose(chosen.threshold, lam)
            else:
                assert chosen is None

    rec.check("the error-versus-coverage curve reproduces the threshold search", curve_is_consistent)


def gates_statistics(rec: Recorder, eng: dict[str, Any]) -> None:
    """Slow. Main run only."""
    fit = eng["fit"]
    alpha = 0.05
    seeds = range(100)
    outcomes: list[tuple[float, float, float]] = []

    for seed in seeds:
        scores, labels = _cubic_population(1500, 5000 + seed)
        result = fit.fit_decision(
            [fit.Example(s, y) for s, y in zip(scores, labels)],
            target_error_rate=alpha,
        )
        if not result.risk.achievable:
            outcomes.append((0.0, 0.0, 1.0))
            continue
        s_star = _threshold_score(eng, result.fitted, result.risk.threshold)
        share, conditional = _cubic_truth(s_star)
        outcomes.append((share * conditional, conditional, result.risk.clopper_pearson_upper))

    def conformal_expectation() -> None:
        mean = sum(o[0] for o in outcomes) / len(outcomes)
        above = sum(1 for o in outcomes if o[0] > alpha)
        print(
            f"         (info) mean true risk {mean:.4f} vs α {alpha}; "
            f"{above}/100 seeds individually above α"
        )
        assert mean <= alpha, (
            f"mean true risk over 100 seeds {mean:.4f} exceeds α={alpha}. "
            "Conformal risk control bounds the expectation; this is the claim."
        )

    rec.check("conformal: mean true risk over 100 seeds is at most α", conformal_expectation)

    def clopper_pearson_coverage() -> None:
        covered = sum(1 for _, truth, upper in outcomes if truth <= upper)
        print(f"         (info) Clopper-Pearson covered the true error in {covered}/100 seeds")
        assert covered >= 90, (
            f"the 95% upper bound covered the true conditional error in only "
            f"{covered}/100 seeds; below 90 is evidence the bound is not 95%"
        )

    rec.check("Clopper-Pearson covers the true error in at least 90 of 100 seeds (95% ± binomial)", clopper_pearson_coverage)


# ===========================================================================
# Offline — drift, staleness, audit sampling, the decision policy
# ===========================================================================


def gates_monitor(rec: Recorder, eng: dict[str, Any]) -> None:
    monitor, vocab = eng["monitor"], eng["vocab"]

    def psi_hand_example() -> None:
        value = monitor.psi([0.5, 0.5], [0.25, 0.75])
        # (0.25-0.5)·ln(0.5) + (0.75-0.5)·ln(1.5)
        hand = 0.25 * math.log(2) + 0.25 * math.log(1.5)
        assert math.isclose(value, hand, rel_tol=1e-12), (value, hand)
        assert math.isclose(value, 0.274653, abs_tol=1e-6)
        assert math.isclose(monitor.psi([0.25, 0.75], [0.5, 0.5]), value)
        assert monitor.psi([0.2] * 5, [0.2] * 5) == 0.0
        assert math.isfinite(monitor.psi([1.0, 0.0], [0.0, 1.0]))

    rec.check("PSI equals the hand-computed 0.274653 and is symmetric", psi_hand_example)

    def psi_suspends_on_shift_only() -> None:
        import numpy as np

        rng = np.random.default_rng(71)
        reference = [float(v) for v in rng.beta(8, 2, 5000)]
        resample = [float(v) for v in rng.beta(8, 2, 5000)]
        shifted = [float(v) for v in rng.beta(4, 4, 5000)]
        hist = monitor.histogram(reference)

        calm = monitor.drift_verdict(
            reference_histogram=hist, reference_count=len(reference),
            recent_scores=resample, realized_wrong=0, realized_total=0,
            bound=0.05, when="1 Jan 2026",
        )
        assert calm.psi_checked and not calm.suspend, calm
        assert calm.psi is not None and calm.psi < 0.05

        moved = monitor.drift_verdict(
            reference_histogram=hist, reference_count=len(reference),
            recent_scores=shifted, realized_wrong=0, realized_total=0,
            bound=0.05, when="1 Jan 2026",
        )
        assert moved.suspend and moved.psi is not None and moved.psi > vocab.PSI_THRESHOLD, moved
        assert moved.reason.startswith("Paused on 1 Jan 2026"), moved.reason

        few = monitor.drift_verdict(
            reference_histogram=hist, reference_count=len(reference),
            recent_scores=shifted[: vocab.MIN_PSI_SAMPLES - 1],
            realized_wrong=0, realized_total=0, bound=0.05, when="x",
        )
        assert not few.psi_checked and not few.suspend

    rec.check("PSI suspends on a shifted distribution and not on a resample", psi_suspends_on_shift_only)

    def realized_rate_test() -> None:
        assert monitor.realized_rate_breach(3, 10, 0.01)[0]
        assert not monitor.realized_rate_breach(1, 1, 0.2)[0], "one draw is not evidence"
        assert not monitor.realized_rate_breach(0, 100, 0.01)[0]
        assert not monitor.realized_rate_breach(5, 10, 1.0)[0]
        verdict = monitor.drift_verdict(
            reference_histogram=[0.05] * vocab.PSI_BINS, reference_count=0,
            recent_scores=[], realized_wrong=4, realized_total=20,
            bound=0.02, when="3 Oct 2026",
        )
        assert verdict.suspend and "4 of the last 20" in verdict.reason, verdict

    rec.check("realized error suspends only when significantly above the bound", realized_rate_test)

    def staleness() -> None:
        now = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
        assert monitor.is_stale(None, now)
        assert monitor.is_stale(now - timedelta(hours=vocab.STALE_AFTER_HOURS + 1), now)
        assert not monitor.is_stale(now - timedelta(hours=vocab.STALE_AFTER_HOURS - 1), now)

    rec.check("a model unchecked for longer than the window is stale", staleness)


def gates_sampling(rec: Recorder, eng: dict[str, Any]) -> None:
    sampling = eng["sampling"]

    def binomial_tolerance() -> None:
        n = 200_000
        hits = sum(1 for i in range(n) if sampling.is_audit_sample("model-a", f"doc-{i}", "0.02"))
        share = hits / n
        sigma = math.sqrt(0.02 * 0.98 / n)
        assert abs(share - 0.02) <= 4 * sigma, (
            f"audit share {share:.5f} is outside 2% ± {4 * sigma:.5f}"
        )

    rec.check("audit sampling lands within binomial tolerance of 2%", binomial_tolerance)

    def deterministic_and_floored() -> None:
        first = [sampling.is_audit_sample("m", k, "0.05") for k in range(5000)]
        second = [sampling.is_audit_sample("m", k, "0.05") for k in range(5000)]
        assert first == second, "the same decision drew a different audit answer"
        other = [sampling.is_audit_sample("m2", k, "0.05") for k in range(5000)]
        assert first != other, "a new model version must redraw the sample"
        floored = sum(sampling.is_audit_sample("m", k, "0") for k in range(100_000))
        assert 700 <= floored <= 1300, (
            f"a rate of 0 sampled {floored} of 100000; audits cannot be switched off"
        )
        assert sampling.audit_weight("0.02") == Decimal("50.000")

    rec.check("audit sampling is deterministic, redraws per version, and cannot be disabled", deterministic_and_floored)


def _snapshot(eng: dict[str, Any], **overrides: Any) -> Any:
    d = eng["decision"]
    now = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
    base = dict(
        model_id="00000000-0000-4000-8000-00000000aaaa",
        decision_type="verification.document",
        method="PLATT",
        parameters={"a": "8.0000000", "b": "-4.0000000"},
        status="ACTIVE",
        threshold=Decimal("0.90000"),
        target_error_rate=Decimal("0.05"),
        conformal_bound=Decimal("0.03"),
        clopper_pearson_upper=Decimal("0.04"),
        auto_share=Decimal("0.40"),
        audit_sample_rate=Decimal("0.02"),
        label_count=120,
        last_checked_at=now - timedelta(hours=1),
        suspended_reason=None,
    )
    base.update(overrides)
    return d.ModelSnapshot(**base)


def gates_decision(rec: Recorder, eng: dict[str, Any]) -> None:
    d, vocab = eng["decision"], eng["vocab"]
    now = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)

    def suspended_never_automatic() -> None:
        reason = "Paused on 3 Oct: scores shifted."
        for decision_type in vocab.AUTOMATED_DECISION_TYPES:
            snap = _snapshot(
                eng, decision_type=decision_type, status="SUSPENDED",
                suspended_reason=reason,
            )
            for i in range(300):
                out = d.decide(snap, 0.999, sample_key=f"k{i}", now=now)
                assert not out.auto_allowed, f"{decision_type} approved while suspended"
                assert out.reason == d.REASON_SUSPENDED and out.explanation == reason, out
                assert out.probability is not None

    rec.check("a suspended model allows nothing automatically, for every decision type", suspended_never_automatic)

    def policy_order() -> None:
        assert d.decide(None, 0.99, sample_key="x", now=now).probability is None
        prior = d.decide(
            _snapshot(eng, method="PRIOR", parameters={}, label_count=12, auto_share=Decimal("0")),
            0.99, sample_key="x", now=now,
        )
        assert prior.probability is None and prior.reason == d.REASON_PRIOR
        stale = d.decide(
            _snapshot(eng, last_checked_at=now - timedelta(hours=vocab.STALE_AFTER_HOURS + 5)),
            0.99, sample_key="x", now=now,
        )
        assert stale.probability is None and not stale.auto_allowed, (
            "a stale model must fall back to the cold start"
        )
        refused = d.decide(
            _snapshot(eng, auto_share=Decimal("0"), threshold=Decimal("1")),
            0.99, sample_key="x", now=now,
        )
        assert not refused.auto_allowed and refused.reason == d.REASON_NOT_ACHIEVABLE
        low = d.decide(_snapshot(eng), 0.3, sample_key="x", now=now)
        assert not low.auto_allowed and low.reason == d.REASON_BELOW_THRESHOLD

        outcomes = [d.decide(_snapshot(eng), 0.99, sample_key=f"doc-{i}", now=now) for i in range(5000)]
        audits = [o for o in outcomes if o.audit_sample]
        autos = [o for o in outcomes if o.auto_allowed]
        assert autos and audits, "both automatic passes and audits must occur"
        assert all(o.would_auto_approve and not o.auto_allowed for o in audits)
        assert 50 <= len(audits) <= 160, f"{len(audits)} audits in 5000 at 2%"

    rec.check("the decision order: no model, prior, stale, suspended, refused, threshold, audit", policy_order)

    def labels_keep_audits() -> None:
        fit = eng["fit"]

        @dataclass
        class Row:
            raw_score: Decimal
            correct: bool
            sample_weight: Decimal
            auto_eligible: bool
            was_audit_sample: bool

        rows: list[Row] = []
        # Below the threshold: every document reviewed.
        for i in range(400):
            rows.append(Row(Decimal(str(round(i / 800, 4))), i % 3 != 0, Decimal("1"), True, False))
        # Above it: only 2% audits, each standing for 50 automatic passes.
        for i in range(20):
            rows.append(Row(Decimal(str(0.95 + i / 1000)), True, Decimal("50"), True, True))
        examples = fit.examples_from_labels(rows)
        assert sum(1 for e in examples if e.was_audit_sample) == 20, (
            "audit labels were dropped from the labelled set"
        )
        assert all(e.weight == 50.0 for e in examples if e.was_audit_sample)
        outcome = fit.fit_decision(examples, target_error_rate=0.05)
        assert outcome.risk.achievable and outcome.risk.passed > 0, (
            "without the audit labels there is no evidence above the threshold"
        )
        assert outcome.diagnostics["audit_labels"] == 20

    rec.check("audit samples stay in the labelled set with weight 1/r", labels_keep_audits)


# ===========================================================================
# Offline — ARCH-33's interface, unchanged in shape
# ===========================================================================


def gates_arch33_interface(rec: Recorder, eng: dict[str, Any], *, root: Path = BACKEND) -> None:
    cal, routing, a_vocab = eng["a_cal"], eng["a_routing"], eng["a_vocab"]

    def signatures() -> None:
        expected = {
            "fit": ["examples", "family", "model_id"],
            "calibrate": ["model", "raw"],
            "effective_threshold": ["configured", "model"],
            "consequence": ["model", "recent_raw_scores", "threshold"],
        }
        for name, params in expected.items():
            got = list(inspect.signature(getattr(cal, name)).parameters)
            assert got == params, f"{name}{got} changed; ARCH-33 callers use {params}"
        for name in ("LabeledExample", "CalibrationPoint", "CalibrationModel", "Consequence"):
            assert hasattr(cal, name)

    rec.check("fit / calibrate / effective_threshold / consequence keep their signatures", signatures)

    def cold_start_is_load_bearing() -> None:
        model = cal.fit(
            [cal.LabeledExample(Decimal("0.99"), True) for _ in range(49)],
            family=a_vocab.FAMILY_PRESENCE,
        )
        assert not model.fitted and cal.calibrate(model, Decimal("0.99")) is None
        assert cal.effective_threshold(Decimal("0.6"), model) == Decimal(a_vocab.COLD_START_THRESHOLD)
        decision = routing.decide(
            verdict=a_vocab.VERDICT_PASS, raw_score=Decimal("0.99"),
            calibrated_probability=None, effective_threshold=Decimal("0.6"),
        )
        assert decision.routed_to == a_vocab.ROUTE_TRIAGE
        assert routing.mirrors_sql_invariant(
            routed_to=decision.routed_to, verdict=decision.verdict,
            calibrated_probability=decision.calibrated_probability,
        )

    rec.check("below 50 labels calibrate is None and routing.decide still triages", cold_start_is_load_bearing)

    def methods_behind_the_interface() -> None:
        eighty = [cal.LabeledExample(Decimal(str(round(i / 80, 5))), i % 4 != 0) for i in range(80)]
        model = cal.fit(eighty, family=a_vocab.FAMILY_DURATION_BOUND)
        assert model.fitted and model.method == "PLATT", model.as_details()
        scores, labels = _cubic_population(300, 41)
        big = cal.fit(
            [cal.LabeledExample(Decimal(str(round(s, 6))), y) for s, y in zip(scores, labels)],
            family=a_vocab.FAMILY_DURATION_BOUND,
        )
        assert big.fitted and big.method == "ISOTONIC", big.as_details()
        low = cal.calibrate(big, Decimal("0.1"))
        high = cal.calibrate(big, Decimal("0.95"))
        assert low is not None and high is not None and high > low
        assert Decimal("0") < low < Decimal("1") and Decimal("0") < high < Decimal("1")

    rec.check("ARCH-33's fit is Platt at 80 labels and isotonic at 300", methods_behind_the_interface)

    def stored_terms_only_raise() -> None:
        d = eng["decision"]
        model = cal.fit(
            [cal.LabeledExample(Decimal(str(round(i / 80, 5))), i % 4 != 0) for i in range(80)],
            family=a_vocab.FAMILY_PRESENCE,
        )
        import dataclasses

        allowed = dataclasses.replace(
            model,
            autonomy=d.AutonomyTerms(
                model_id="m", status="ACTIVE", threshold=Decimal("0.7"),
                audit_sample_rate=Decimal("0.02"), achievable=True,
            ),
        )
        assert cal.effective_threshold(Decimal("0.9"), allowed) == Decimal("0.9"), (
            "the conformal threshold lowered the administrator's setting"
        )
        assert cal.effective_threshold(Decimal("0.6"), allowed) == Decimal("0.7")
        paused = dataclasses.replace(
            allowed,
            autonomy=d.AutonomyTerms(
                model_id="m", status="SUSPENDED", threshold=Decimal("0.7"),
                audit_sample_rate=Decimal("0.02"), achievable=True,
            ),
        )
        assert cal.effective_threshold(Decimal("0.6"), paused) == Decimal("1")
        decision = routing.decide(
            verdict=a_vocab.VERDICT_PASS, raw_score=Decimal("0.99"),
            calibrated_probability=Decimal("0.99999"),
            effective_threshold=cal.effective_threshold(Decimal("0.6"), paused),
        )
        assert decision.routed_to == a_vocab.ROUTE_TRIAGE

    rec.check("a stored model's threshold only ever raises the setting; paused means review", stored_terms_only_raise)

    def routing_untouched() -> None:
        digest = _normalised_sha256(root / ASSERTION_ROUTING)
        assert digest == ROUTING_SHA256, (
            f"app/services/assertions/routing.py changed (sha256 {digest}). "
            "ARCH-35 must not touch ARCH-33's only route to the pass edge."
        )

    rec.check("assertions/routing.py is byte-identical to ARCH-33's", routing_untouched)


# ===========================================================================
# Offline — wiring (source)
# ===========================================================================


def gates_wiring(rec: Recorder, *, root: Path = BACKEND) -> None:
    def three_places() -> None:
        handlers = _read(root / "app/workers/handlers/__init__.py")
        profiles = _read(root / "app/workers/profiles.py")
        scheduler = _read(root / "app/workers/scheduler.py")
        for job in ("calibration.harvest", "calibration.refit"):
            assert f'"{job}":' in handlers, f"{job} is not in the handler map"
            assert f'"{job}",' in profiles, f"{job} is not claimed by a profile"
            assert f'job_type="{job}"' in scheduler, f"{job} is not scheduled"
        light = profiles.split("LIGHT = WorkerProfile", 1)[1].split("OCR = WorkerProfile", 1)[0]
        assert '"calibration.harvest"' in light and '"calibration.refit"' in light, (
            "the calibration jobs must be on the LIGHT profile"
        )
        assert "ARCH35_JOB_TYPES" in handlers.split("ALL_PHASE_JOB_TYPES: frozenset[str] = (", 1)[1]

    rec.check("both jobs are in the handler map, the LIGHT profile and the schedule", three_places)

    def profile_coverage_at_import() -> None:
        try:
            from app.workers import profiles
            from app.workers.handlers import _HANDLERS
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(f"the worker registry does not import: {exc}") from exc
        uncovered = profiles.uncovered_job_types(_HANDLERS.keys())
        assert not uncovered, f"handlers no profile claims: {sorted(uncovered)}"

    rec.check("no registered handler is left unclaimed by a worker profile", profile_coverage_at_import)

    def capability_registered() -> None:
        tree = ast.parse(_read(root / "app/core/entitlements.py"))
        tuples: dict[str, list[str]] = {}
        registered: list[str] = []
        for node in tree.body:
            target = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                target = node.target.id
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target = node.targets[0].id
            if target in ("CAPABILITY_KEYS", "ADDON_KEYS") and isinstance(node.value, ast.Tuple):
                tuples[target] = [e.id for e in node.value.elts if isinstance(e, ast.Name)]
            if target == "_ENTITLEMENTS" and isinstance(node.value, ast.Tuple):
                for element in node.value.elts:
                    if isinstance(element, ast.Call):
                        for kw in element.keywords:
                            if kw.arg == "name" and isinstance(kw.value, ast.Name):
                                registered.append(kw.value.id)
        assert "CALIBRATED_AUTONOMY_CAPABILITY" in tuples.get("CAPABILITY_KEYS", [])
        assert "CALIBRATED_AUTONOMY_CAPABILITY" not in tuples.get("ADDON_KEYS", [])
        for key in tuples.get("CAPABILITY_KEYS", []):
            assert key in registered, (
                f"{key} is in CAPABILITY_KEYS but not registered in _ENTITLEMENTS; "
                "has_capability raises for it and no tier can carry it"
            )
        source = _read(root / "app/core/entitlements.py")
        assert 'CALIBRATED_AUTONOMY_CAPABILITY: str = "capability.calibrated_autonomy"' in source
        gate = _read(root / "app/api/capability_gate.py")
        assert "CALIBRATED_AUTONOMY_CAPABILITY:" in gate
        assert "def granted_capabilities(" in gate
        usage = _read(root / "app/core/usage_events.py")
        assert "calibration" not in usage.lower(), (
            "ARCH-35 registers no meter: refitting is platform maintenance"
        )

    rec.check("capability.calibrated_autonomy is a registered CAPABILITY, unmetered", capability_registered)

    def entitlements_list_capabilities() -> None:
        schema = _read(root / "app/schemas/entitlements.py")
        api = _strip_comments(_read(root / "app/api/v1/entitlements.py"))
        assert "capabilities: list[str]" in schema
        assert "granted_capabilities(" in api, (
            "the entitlements response does not list capabilities, so the "
            "console's useCapabilityAccess hook reports every capability absent"
        )

    rec.check("the entitlements response lists granted capabilities", entitlements_list_capabilities)

    def router_and_gates() -> None:
        router = _read(root / "app/api/v1/router.py")
        assert "api_router.include_router(autonomy.router)" in router
        api = _strip_comments(_read(root / API_MODULE))
        routes = api.count("@router.")
        gated = api.count("_gate(db, context,")
        assert routes == 4, f"expected the four §6.6 endpoints, found {routes}"
        assert gated == routes, f"{routes} routes and {gated} capability checks"
        for block in api.split("@router.")[1:]:
            head = block.split("\n", 1)[0]
            if head.startswith(("put", "post")):
                assert "Depends(RequireOrgOwner)" in block, f"{head} is not OWNER-gated"
            else:
                assert "Depends(RequireOrgAdmin)" in block, f"{head} is not ADMIN-gated"
        assert "entitlements.CALIBRATED_AUTONOMY_CAPABILITY" in api

    rec.check("four endpoints, every one capability-gated; writes are OWNER", router_and_gates)

    def arch33_callers_repointed() -> None:
        triage = _strip_comments(_read(root / ASSERTION_TRIAGE))
        assert "calibrated_autonomy.assertion_model(" in triage
        assert "calibrated_autonomy.withhold_assertion(" in triage
        assert "correct=(reviewer_verdict == verdict)" in triage
        assert triage.count("record_usage(") == 2
        body = triage.split("def record_evaluation(", 1)[1]
        assert body.find("routing.decide(") < body.find("withhold_assertion(") < body.find("_attach_review("), (
            "the demotion must sit between routing.decide and the review row"
        )
        cal = _strip_comments(_read(root / ASSERTION_CALIBRATION))
        assert "_fitting.fit_decision(" in cal and "_estimators.evaluate(" in cal
        assert "blocks.pop()" not in cal, "ARCH-33's interim PAV estimator is still live"
        model = _read(root / "app/models/assertion.py")
        assert '"calibration_models.id"' in model and 'name="fk_ae_calibration_model"' in model

    rec.check("ARCH-33's triage and calibrator are re-pointed at ARCH-35", arch33_callers_repointed)

    def verification_repointed() -> None:
        source = _strip_comments(_read(root / VERIFICATION_SERVICE))
        triage = source.split("def triage(", 1)[1].split("\ndef ", 1)[0]
        assert "calibrated_autonomy.decide_verification(" in triage
        assert "elif consensus.all_agreed and consensus.confidence >= threshold" in triage, (
            "the fixed threshold must be the fallback, not the first rule"
        )
        assert '"review_all_fields": not autonomy.auto_allowed' in triage
        resolve = source.split("def resolve(", 1)[1]
        assert "review_all" in resolve
        assert "not f.field_path.startswith(FIELD_PATH_PREFIX)" in resolve

    rec.check("document_verifications.auto_approved goes through apply.decide_verification", verification_repointed)

    def apply_script_sentinels() -> None:
        spec = importlib.util.spec_from_file_location("_a35_apply", root / APPLY_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["_a35_apply"] = module
        try:
            spec.loader.exec_module(module)
        finally:
            sys.modules.pop("_a35_apply", None)
        for patch in module.PATCHES:
            written = "".join(edit.replacement for edit in patch.edits)
            assert patch.sentinel in written, f"{patch.relpath}: sentinel not in replacement"
            base = root if patch.root == module.BACKEND else root.parent / "frontend"
            path = base / patch.relpath
            text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            if patch.sentinel in text:
                continue
            for edit in patch.edits:
                assert text.count(edit.anchor) == edit.occurrences, (
                    f"{patch.relpath}: anchor for {edit.description!r}"
                )

    rec.check("every apply_arch35 sentinel is a substring of its own replacement", apply_script_sentinels)


# ===========================================================================
# Offline — the console
# ===========================================================================


def gates_console(rec: Recorder, *, root: Path = BACKEND) -> None:
    frontend = root.parent / "frontend"

    def files_exist() -> None:
        for relpath in (FE_PAGE, FE_LOCK, FE_CHARTS, FE_API, FE_TYPES):
            assert (frontend / relpath).exists(), f"missing {relpath}"

    rec.check("every console file ARCH-35 ships is present", files_exist)

    def page_contract() -> None:
        page = _read(frontend / FE_PAGE)
        assert "useCapabilityAccess(" in page and "AutonomyLockCard" in page
        assert "formatTimestamp" in page and "utils/displayTime" in page
        assert "ApiError" in page and "toLocaleString()" not in page
        assert page.count('type="range"') == 2, "the α slider and the audit slider"
        assert "outcomeForAlpha(" in page, "the slider must preview from the server's curve"
        assert "not achievable on your current" in page
        assert "confidence" in page and "clopper_pearson_upper" in page
        assert "ReliabilityDiagram" in page and "CoverageChart" in page
        assert "Check again now" in page and "resumeAutonomy(" in page
        assert "fewer than {minLabels} reviewed documents" in page, (
            "the plain-language cold-start disclaimer is missing"
        )
        assert "audit" in page.lower() and "cannot be switched" in page
        assert "{entry.summary}" in page and "{entry.suspended_reason}" in page, (
            "sentences about a promise must be rendered verbatim from the server"
        )

    rec.check("the settings page states limits, bounds, audits and the cold start", page_contract)

    def decimals_at_the_edge() -> None:
        api = _read(frontend / FE_API)
        assert "/ 1000).toFixed(5)" in api and "/ 100).toFixed(4)" in api

    rec.check("slider positions become Decimal strings exactly once", decimals_at_the_edge)

    def lock_card_has_no_button() -> None:
        card = _read(frontend / FE_LOCK)
        assert "canChangePlan" in card
        assert "<button" not in card.lower()

    rec.check("the lock card mirrors the others and offers no purchase", lock_card_has_no_button)

    def registered() -> None:
        keys = _read(frontend / "src/services/api/queryKeys.ts")
        endpoints = _read(frontend / "src/services/api/endpoints.ts")
        paths = _read(frontend / "src/routes/tenantPaths.ts")
        app = _read(frontend / "src/App.tsx")
        nav = _read(frontend / "src/components/layout/navigation.ts")
        types_ent = _read(frontend / "src/types/entitlements.ts")
        review = _read(frontend / "src/pages/Verification/VerificationReviewQueue.tsx")
        assert "export const autonomyKeys" in keys
        assert "export const AUTONOMY_ENDPOINTS" in endpoints
        assert 'organizationAutonomy: "autonomy"' in paths and "organizationAutonomyPath" in paths
        assert "ROUTE_PATTERNS.organizationAutonomy" in app and "AutonomySettings" in app
        assert "organizationAutonomyPath(orgSlug)" in nav
        assert "readonly capabilities: readonly string[]" in types_ent
        assert "review_all_fields" in review and ".filter(isReviewable)" in review

    rec.check("keys, endpoints, route, navigation and review queue are wired", registered)


# ===========================================================================
# Live database
# ===========================================================================


def _database_url(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    from app.db.session import engine

    return engine.url.render_as_string(hide_password=False)


def gates_db(rec: Recorder) -> None:
    from sqlalchemy import text as sql
    from sqlalchemy.exc import DBAPIError

    from app.db.session import SessionLocal

    db = SessionLocal()

    def refuses(statement: str, params: dict[str, Any], names: tuple[str, ...]) -> None:
        savepoint = db.begin_nested()
        try:
            db.execute(sql(statement), params)
            db.flush()
        except DBAPIError as exc:
            savepoint.rollback()
            message = str(exc)
            assert any(name in message for name in names), (
                f"refused, but not by {names}: {message[:240]}"
            )
            return
        savepoint.rollback()
        raise AssertionError(f"the database ACCEPTED a row {names} exists to refuse")

    def accepts(statement: str, params: dict[str, Any]) -> None:
        savepoint = db.begin_nested()
        try:
            db.execute(sql(statement), params)
            db.flush()
        finally:
            savepoint.rollback()

    org_id = uuid.uuid4()
    try:
        db.execute(
            sql("INSERT INTO organizations (id, slug, name, status) VALUES (:id, :slug, :name, 'ACTIVE')"),
            {"id": org_id, "slug": f"arch35-gate-{org_id.hex[:12]}", "name": "ARCH-35 gate"},
        )
        db.flush()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        db.close()
        rec.check("a scratch organization can be created", lambda: (_ for _ in ()).throw(AssertionError(str(exc))))
        return

    def head_and_tables() -> None:
        head = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
        assert head == "arch35_step1_calibration", f"alembic head is {head}; run `alembic upgrade head`"
        for table in ("calibration_labels", "calibration_models"):
            assert db.execute(sql("SELECT to_regclass(:t)"), {"t": table}).scalar() is not None

    rec.check("DB: head is arch35_step1_calibration and both tables exist", head_and_tables)

    def foreign_key() -> None:
        row = db.execute(
            sql(
                "SELECT c.contype, c.confdeltype, c.convalidated, t.relname, a.attname "
                "FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.confrelid "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) "
                "WHERE c.conname = 'fk_ae_calibration_model' "
                "AND c.conrelid = 'assertion_evaluations'::regclass"
            )
        ).all()
        assert len(row) == 1, f"fk_ae_calibration_model not found exactly once: {row}"
        contype, deltype, validated, target, column = row[0]
        assert contype == "f" and target == "calibration_models"
        assert column == "calibration_model_id" and deltype == "n" and validated, row
        existing = db.execute(sql("SELECT id FROM assertion_evaluations LIMIT 1")).scalar()
        if existing is not None:
            refuses(
                "UPDATE assertion_evaluations SET calibration_model_id = :m WHERE id = :id",
                {"m": uuid.uuid4(), "id": existing},
                ("fk_ae_calibration_model",),
            )

    rec.check("DB: fk_ae_calibration_model constrains assertion_evaluations.calibration_model_id to calibration_models(id), ON DELETE SET NULL", foreign_key)

    insert_model = (
        "INSERT INTO calibration_models (id, organization_id, decision_type, method, "
        "label_count, breakpoints, ece_before, ece_after, brier_after, target_error_rate, "
        "threshold, conformal_bound, clopper_pearson_upper, auto_share, audit_sample_rate, "
        "status, suspended_reason, suspended_at, input_digest, diagnostics) VALUES "
        "(:id, :org, :dt, :method, :n, '{}'::jsonb, :eb, :ea, 0.1, :alpha, :lam, :bound, "
        "0.05, :share, :audit, :status, :reason, :sat, :digest, '{}'::jsonb)"
    )

    def model_row(**overrides: Any) -> dict[str, Any]:
        row = dict(
            id=uuid.uuid4(), org=org_id, dt="verification.document", method="ISOTONIC",
            n=200, eb=0.1, ea=0.05, alpha=0.05, lam=0.9, bound=0.04, share=0.3,
            audit=0.02, status="ACTIVE", reason=None, sat=None, digest="a" * 64,
        )
        row.update(overrides)
        return row

    def statistics_refused() -> None:
        accepts(insert_model, model_row())
        refuses(insert_model, model_row(n=199), ("ck_cm_method_needs_labels",))
        refuses(insert_model, model_row(method="PLATT", n=49), ("ck_cm_method_needs_labels", "ck_cm_prior_iff_cold"))
        refuses(insert_model, model_row(method="PLATT", n=250), ("ck_cm_method_needs_labels",))
        refuses(
            insert_model,
            model_row(method="PRIOR", n=50, lam=1, share=0, bound=1),
            ("ck_cm_method_needs_labels", "ck_cm_prior_iff_cold"),
        )
        refuses(insert_model, model_row(eb=0.05, ea=0.1), ("ck_cm_improves",))
        accepts(insert_model, model_row(eb=0.05, ea=0.1, status="REJECTED"))
        refuses(insert_model, model_row(alpha=0), ("ck_cm_alpha_bounded",))
        refuses(insert_model, model_row(alpha=0.25), ("ck_cm_alpha_bounded",))
        refuses(insert_model, model_row(audit=0), ("ck_cm_audit_rate_bounded",))
        refuses(insert_model, model_row(status="SUSPENDED"), ("ck_cm_suspension_has_reason",))
        refuses(insert_model, model_row(bound=0.06), ("ck_cm_promise_backed",))
        refuses(insert_model, model_row(dt="verification.unknown"), ("ck_cm_decision_known",))

    rec.check("DB: isotonic at 199, Platt outside 50-199, a worse ECE, an unbacked promise and a disabled audit are all refused", statistics_refused)

    def one_live_model() -> None:
        savepoint = db.begin_nested()
        try:
            db.execute(sql(insert_model), model_row())
            try:
                nested = db.begin_nested()
                db.execute(sql(insert_model), model_row(status="SUSPENDED", reason="x", sat=datetime.now(timezone.utc)))
                nested.rollback()
                raise AssertionError("two live models for one decision type were accepted")
            except DBAPIError as exc:
                nested.rollback()
                assert "uq_cm_active" in str(exc), str(exc)[:200]
        finally:
            savepoint.rollback()

    rec.check("DB: one ACTIVE-or-SUSPENDED model per (organization, decision type)", one_live_model)

    insert_label = (
        "INSERT INTO calibration_labels (id, organization_id, decision_type, raw_score, correct, "
        "source_table, source_id, was_auto_approved, was_audit_sample, auto_eligible, "
        "sample_weight, observed_at) VALUES (:id, :org, :dt, 0.5, true, :src, :sid, :auto, "
        ":audit, true, :w, now())"
    )

    def label_row(**overrides: Any) -> dict[str, Any]:
        row = dict(
            id=uuid.uuid4(), org=org_id, dt="verification.document",
            src="document_verifications", sid=uuid.uuid4(), auto=False, audit=False, w=1,
        )
        row.update(overrides)
        return row

    def labels_refused() -> None:
        accepts(insert_label, label_row(auto=True, audit=True, w=50))
        refuses(insert_label, label_row(w=50), ("ck_cl_weight_is_audit_inverse",))
        refuses(insert_label, label_row(audit=True), ("ck_cl_audit_was_automatic",))
        refuses(insert_label, label_row(dt="assertion.unknown"), ("ck_cl_decision_known",))
        refuses(insert_label, label_row(src="users"), ("ck_cl_source_known",))
        savepoint = db.begin_nested()
        try:
            source = uuid.uuid4()
            db.execute(sql(insert_label), label_row(sid=source))
            nested = db.begin_nested()
            try:
                db.execute(sql(insert_label), label_row(sid=source))
                nested.rollback()
                raise AssertionError("the same source was labelled twice")
            except DBAPIError as exc:
                nested.rollback()
                assert "uq_cl_source" in str(exc)
        finally:
            savepoint.rollback()

    rec.check("DB: label weights, audits, decision types and source idempotence are enforced", labels_refused)

    def lifecycle() -> None:
        from app.api import capability_gate
        from app.core import entitlements
        from app.models.calibration import CalibrationLabel
        from app.schemas.calibration import AutonomyEntry, AutonomyReliability
        from app.services.calibration import apply, labels, overview, refit
        from app.services.calibration import vocabulary as vocab

        for key in entitlements.CAPABILITY_KEYS:
            capability_gate.has_capability(db, organization_id=org_id, capability_key=key)
        assert capability_gate.granted_capabilities(db, organization_id=org_id) == []

        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        dt = vocab.DECISION_VERIFICATION_DOCUMENT
        scores, truth = _cubic_population(300, 77)

        harvested = labels.harvest(db, organization_id=org_id, now=t0)
        assert harvested.backfill and harvested.total_inserted == 0

        def add(score: float, correct: bool, *, at: datetime, auto: bool = False,
                audit: bool = False, weight: str = "1", created: Optional[datetime] = None,
                decision_type: str = dt) -> None:
            db.add(
                CalibrationLabel(
                    id=uuid.uuid4(), organization_id=org_id, decision_type=decision_type,
                    raw_score=Decimal(str(round(score, 7))), correct=correct,
                    source_table="document_verifications", source_id=uuid.uuid4(),
                    was_auto_approved=auto, was_audit_sample=audit, auto_eligible=True,
                    sample_weight=Decimal(weight), observed_at=at,
                    **({"created_at": created} if created is not None else {}),
                )
            )

        for s, y in zip(scores, truth):
            add(s, y, at=t0 - timedelta(days=10), created=t0 - timedelta(days=10))
        for i in range(20):
            add(0.97 + i / 1000, True, at=t0 - timedelta(days=5), auto=True, audit=True,
                weight="50", created=t0 - timedelta(days=5))
        db.flush()

        first = refit.update_settings(
            db, organization_id=org_id, decision_type=dt,
            target_error_rate="0.1", audit_sample_rate="0.02", now=t0,
        )
        assert first.outcome == "fitted" and first.model is not None, first
        model = first.model
        assert model.method == "ISOTONIC" and model.status == "ACTIVE" and model.label_count == 320
        assert model.diagnostics["achievable"], model.diagnostics.get("risk_reason")
        assert refit.refit(db, organization_id=org_id, decision_type=dt, now=t0).outcome == "unchanged"

        later = t0 + timedelta(hours=1)
        high = [
            apply.decide(db, organization_id=org_id, decision_type=dt, raw_score=0.999,
                         sample_key=f"v{i}", now=later, check_capability=False)
            for i in range(400)
        ]
        assert any(d.auto_allowed for d in high) and any(d.audit_sample for d in high)
        assert all(d.model_id == str(model.id) for d in high)
        low = apply.decide(db, organization_id=org_id, decision_type=dt, raw_score=0.05,
                           sample_key="low", now=later, check_capability=False)
        assert low is not None and not low.auto_allowed and low.probability is not None
        triple = apply.calibrated(db, org_id, dt, 0.999, sample_key="x")
        assert triple == (None, None, False), "no capability, no autonomy"

        class _Verification:
            id = uuid.uuid4()
            organization_id = org_id

        assert apply.decide_verification(db, verification=_Verification(), confidence=Decimal("0.99")) is None, (
            "a tenant without the capability must fall through to the fixed threshold"
        )

        # Automatic approvals a reviewer later found wrong (reviewed outside the
        # audit sample, so unweighted).
        for i in range(10):
            add(0.99, False, at=t0 + timedelta(hours=2), auto=True)
        db.flush()
        verdict = refit.check(db, model=model, now=t0 + timedelta(hours=3))
        assert verdict.suspend and model.status == "SUSPENDED", verdict
        assert (model.suspended_reason or "").startswith("Paused on"), model.suspended_reason

        paused = apply.decide(db, organization_id=org_id, decision_type=dt, raw_score=0.999,
                              sample_key="p", now=t0 + timedelta(hours=3), check_capability=False)
        assert paused is not None and not paused.auto_allowed and paused.reason == "suspended"

        held = refit.update_settings(
            db, organization_id=org_id, decision_type=dt,
            target_error_rate="0.15", audit_sample_rate="0.03",
            now=t0 + timedelta(hours=3, minutes=30),
        )
        assert held.outcome == "held" and held.live is not None and held.live.status == "SUSPENDED", held
        assert Decimal(held.live.target_error_rate) == Decimal("0.15")
        assert model.status == "SUPERSEDED"

        refused = refit.resume(db, organization_id=org_id, decision_type=dt, now=t0 + timedelta(hours=4))
        assert not refused.resumed and "reviewed since the pause" in refused.message, refused

        for s, y in zip(scores[:25], truth[:25]):
            add(s, y, at=t0 + timedelta(hours=5), created=t0 + timedelta(hours=5))
        db.flush()
        resumed = refit.resume(db, organization_id=org_id, decision_type=dt, now=t0 + timedelta(hours=6))
        assert resumed.resumed and resumed.model is not None, resumed.message
        assert resumed.model.status == "ACTIVE" and held.live.status == "SUPERSEDED"
        live = refit.live_model(db, organization_id=org_id, decision_type=dt)
        assert live is not None and live.id == resumed.model.id

        stale = apply.decide(
            db, organization_id=org_id, decision_type=dt, raw_score=0.999, sample_key="s",
            now=t0 + timedelta(hours=6 + vocab.STALE_AFTER_HOURS + 1), check_capability=False,
        )
        assert stale is not None and stale.probability is None and stale.reason == "stale"

        for i in range(10):
            add(0.4, i % 2 == 0, at=t0, decision_type=vocab.DECISION_ANOMALY_FINDING)
        db.flush()
        prior = refit.refit(db, organization_id=org_id,
                            decision_type=vocab.DECISION_ANOMALY_FINDING, now=t0)
        assert prior.model is not None and prior.model.method == "PRIOR"
        assert prior.model.threshold == 1 and prior.model.auto_share == 0

        entry = overview.entry_for(db, organization_id=org_id, decision_type=dt)
        AutonomyEntry(**entry)
        payload = overview.reliability(db, organization_id=org_id, decision_type=dt)
        payload["entry"] = AutonomyEntry(**payload["entry"])
        parsed = AutonomyReliability(**payload)
        assert parsed.coverage_curve and parsed.fitted_curve and parsed.reliability
        assert len(overview.overview(db, organization_id=org_id)) == len(vocab.DECISION_TYPES)

    rec.check("DB: fit, decide, audit, suspend, hold, refuse, resume, go stale and cold-start — end to end", lifecycle)

    def harvest_from_reviews() -> None:
        from app.services.calibration import labels

        ws, item, user, verification = (uuid.uuid4() for _ in range(4))
        f1, f2 = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(timezone.utc)
        db.execute(sql(
            "INSERT INTO workspaces (id, workspace_name, timezone, language, currency, "
            "date_format, organization_id, slug, status) VALUES "
            "(:id, 'gate', 'UTC', 'en', 'USD', 'YYYY-MM-DD', :org, :slug, 'ACTIVE')"
        ), {"id": ws, "org": org_id, "slug": f"gate-{ws.hex[:10]}"})
        db.execute(sql(
            "INSERT INTO work_items (id, original_filename, stored_filename, file_type, "
            "file_size, status, workspace_id) VALUES (:id, 'a.pdf', :stored, 'pdf', 1, "
            "'COMPLETED', :ws)"
        ), {"id": item, "ws": ws, "stored": f"arch35-gate-{item.hex}.pdf"})
        db.execute(sql(
            "INSERT INTO users (id, email, hashed_password, is_active, is_superuser, "
            "timezone, locale) VALUES (:id, :email, 'x', true, false, 'UTC', 'en')"
        ), {"id": user, "email": f"gate-{user.hex[:10]}@example.invalid"})
        db.execute(sql(
            "INSERT INTO document_verifications (id, work_item_id, workspace_id, "
            "organization_id, status, agent_count, agreement_score, confidence, "
            "cost_micros, auto_approved, reviewed_by_user_id, reviewed_at, details) VALUES "
            "(:id, :item, :ws, :org, 'REVIEWED', 2, 1.0, 0.95, 0, false, :user, :now, "
            "CAST(:details AS jsonb))"
        ), {
            "id": verification, "item": item, "ws": ws, "org": org_id, "user": user,
            "now": now,
            "details": '{"calibration": {"would_auto_approve": true, '
                       '"audit_sample": true, "audit_sample_rate": "0.02", '
                       '"review_all_fields": true}}',
        })
        db.execute(sql(
            "INSERT INTO document_verification_fields (id, verification_id, field_path, "
            "agreed, confidence, consensus_value, agent_values, disagreement_kind, "
            "resolved_value) VALUES "
            "(:f1, :v, 'invoice_number', true, 1.0, '\"INV-1\"'::jsonb, '[]'::jsonb, NULL, '\"INV-1\"'::jsonb),"
            "(:f2, :v, 'total', true, 0.95, '\"1,200.00\"'::jsonb, '[]'::jsonb, NULL, '\"1250\"'::jsonb)"
        ), {"f1": f1, "f2": f2, "v": verification})
        db.flush()

        result = labels.harvest(db, organization_id=org_id, full=True)
        assert result.inserted.get("verification") == 3, result.as_payload()
        rows = db.execute(sql(
            "SELECT decision_type, correct, was_audit_sample, sample_weight FROM "
            "calibration_labels WHERE organization_id = :org AND source_id IN (:v, :f1, :f2) "
            "ORDER BY decision_type, correct"
        ), {"org": org_id, "v": verification, "f1": f1, "f2": f2}).all()
        assert [(r[0], r[1], r[2], float(r[3])) for r in rows] == [
            ("verification.document", False, True, 50.0),
            ("verification.field", False, True, 50.0),
            ("verification.field", True, True, 50.0),
        ], rows
        again = labels.harvest(db, organization_id=org_id, full=True)
        assert again.inserted.get("verification") == 0, "harvest is not idempotent"

    rec.check("DB: a reviewed audit becomes weighted document and field labels, exactly once", harvest_from_reviews)

    def arch33_path_end_to_end() -> None:
        """ARCH-33's real writer, on a real row, against a stored ARCH-35 model.

        Builds the smallest legal ARCH-13/ARCH-33 chain (rule, node, execution,
        node runs, definition), then drives `triage.record_evaluation` exactly
        as the node executor does. Proves, on Postgres rather than in Python:
        the FK accepts a stored model id and refuses an invented one, ON DELETE
        SET NULL holds, audits are demoted to TRIAGE with the probability kept
        (ck_ae_routing_consistent permits it), a suspended model sends every
        PASS to review, and a resolved audit is harvested as a weighted label.
        """
        from types import SimpleNamespace

        from app.models.assertion import AssertionDefinition, AssertionEvaluation
        from app.models.calibration import CalibrationLabel
        from app.services.assertions import triage
        from app.services.calibration import apply, labels, refit

        def first_label(enum: str) -> str:
            value = db.execute(sql(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = :t ORDER BY e.enumsortorder LIMIT 1"
            ), {"t": enum}).scalar()
            assert value, f"enum {enum} not found"
            return value

        ws, rule, node, execution, user = (uuid.uuid4() for _ in range(5))
        db.execute(sql(
            "INSERT INTO workspaces (id, workspace_name, timezone, language, currency, "
            "date_format, organization_id, slug, status) VALUES "
            "(:id, 'gate33', 'UTC', 'en', 'USD', 'YYYY-MM-DD', :org, :slug, 'ACTIVE')"
        ), {"id": ws, "org": org_id, "slug": f"gate33-{ws.hex[:10]}"})
        db.execute(sql(
            "INSERT INTO users (id, email, hashed_password, is_active, is_superuser, "
            "timezone, locale) VALUES (:id, :email, 'x', true, false, 'UTC', 'en')"
        ), {"id": user, "email": f"gate33-{user.hex[:10]}@example.invalid"})
        db.execute(sql(
            "INSERT INTO automation_rules (id, name, event, is_active, priority, conditions, "
            "logic_operator, actions, workspace_id, graph_version) VALUES "
            "(:id, 'gate', 'document.processed', true, 0, '[]', CAST(:op AS logic_operator), "
            "'[]', :ws, 1)"
        ), {"id": rule, "ws": ws, "op": first_label("logic_operator")})
        db.execute(sql(
            "INSERT INTO automation_nodes (id, rule_id, node_key, node_type, topological_order) "
            "VALUES (:id, :rule, 'clause', 'assertion', 0)"
        ), {"id": node, "rule": rule})
        db.execute(sql(
            "INSERT INTO automation_executions (id, organization_id, workspace_id, rule_id, "
            "correlation_id, status, budget_cost_micros) VALUES "
            "(:id, :org, :ws, :rule, :corr, CAST(:st AS automation_execution_status), 0)"
        ), {"id": execution, "org": org_id, "ws": ws, "rule": rule, "corr": uuid.uuid4(),
            "st": first_label("automation_execution_status")})
        definition = AssertionDefinition(
            id=uuid.uuid4(), organization_id=org_id, workspace_id=ws, node_id=node,
            sentence="There is an automatic renewal clause", family="presence",
            plan={"family": "presence"}, threshold=Decimal("0.6"),
            evaluation_mode="DETERMINISTIC",
        )
        db.add(definition)
        db.flush()

        run_status = first_label("automation_node_run_status")

        sequence = iter(range(10_000))

        def node_run() -> uuid.UUID:
            run_id = uuid.uuid4()
            db.execute(sql(
                "INSERT INTO automation_node_runs (id, execution_id, node_key, node_type, "
                "sequence, status) VALUES (:id, :ex, 'clause', 'assertion', :seq, "
                "CAST(:st AS automation_node_run_status))"
            ), {"id": run_id, "ex": execution, "st": run_status, "seq": next(sequence)})
            return run_id

        def work_item() -> uuid.UUID:
            item = uuid.uuid4()
            db.execute(sql(
                "INSERT INTO work_items (id, original_filename, stored_filename, file_type, "
                "file_size, status, workspace_id) VALUES (:id, 'c.pdf', :stored, 'pdf', 1, "
                "'COMPLETED', :ws)"
            ), {"id": item, "ws": ws, "stored": f"arch35-gate33-{item.hex}.pdf"})
            return item

        # A stored model for assertion.presence.
        t0 = datetime.now(timezone.utc).replace(microsecond=0)
        scores, truth = _cubic_population(300, 91)
        for sc, y in zip(scores, truth):
            db.add(CalibrationLabel(
                id=uuid.uuid4(), organization_id=org_id, decision_type="assertion.presence",
                raw_score=Decimal(str(round(sc, 7))), correct=y,
                source_table="assertion_evaluations", source_id=uuid.uuid4(),
                was_auto_approved=False, was_audit_sample=False, auto_eligible=True,
                sample_weight=Decimal("1"), observed_at=t0 - timedelta(days=3),
            ))
        db.flush()
        fitted = refit.update_settings(
            db, organization_id=org_id, decision_type="assertion.presence",
            target_error_rate="0.1", audit_sample_rate="0.05", now=t0,
        )
        model = fitted.model
        assert model is not None and model.status == "ACTIVE" and model.diagnostics["achievable"]

        original = apply.autonomy_enabled
        apply.autonomy_enabled = lambda db, *, organization_id: True  # type: ignore[assignment]
        try:
            outcomes = []
            for _ in range(120):
                result = SimpleNamespace(
                    verdict="PASS", raw_score=Decimal("0.99900"), extracted_value=None,
                    evidence=[{"quote": "renews automatically", "confidence": 0.9}],
                    matched_phrases=(), token_usage=None,
                )
                outcomes.append(triage.record_evaluation(
                    db, definition=definition, evaluation_result=result,
                    node_run_id=node_run(), work_item_id=work_item(), meter=False,
                ))
            db.flush()
            passed = [o for o in outcomes if o.evaluation.routed_to == "PASS"]
            audited = [o for o in outcomes if o.evaluation.routed_to == "TRIAGE"]
            assert passed and audited, (len(passed), len(audited))
            for o in outcomes:
                assert o.evaluation.calibration_model_id == model.id
                assert o.evaluation.calibrated_probability is not None
            audit = audited[0]
            assert "accuracy audit" in audit.decision.reason
            key = labels.assertion_sample_key(
                definition.id, audit.evaluation.work_item_id, audit.evaluation.node_run_id
            )
            assert key in audit.verification.details["calibration"]["assertion_audits"]

            # FK: an invented model id is refused; deleting the model nulls the reference.
            refuses(
                "UPDATE assertion_evaluations SET calibration_model_id = :m WHERE id = :id",
                {"m": uuid.uuid4(), "id": passed[0].evaluation.id},
                ("fk_ae_calibration_model",),
            )
            savepoint = db.begin_nested()
            try:
                db.execute(sql("DELETE FROM calibration_models WHERE id = :id"), {"id": model.id})
                remaining = db.execute(sql(
                    "SELECT count(*) FROM assertion_evaluations WHERE calibration_model_id = :id"
                ), {"id": model.id}).scalar_one()
                assert remaining == 0, "ON DELETE SET NULL did not clear the references"
            finally:
                savepoint.rollback()
            db.expire_all()

            # A resolved audit becomes a weighted, audit-flagged label.
            triage.resolve_assertion(
                db, evaluation=audit.evaluation, reviewer_verdict="PASS",
                reviewer_user_id=user,
            )
            db.flush()
            harvested = labels.harvest(db, organization_id=org_id, full=True)
            assert harvested.inserted.get("assertion") == 1, harvested.as_payload()
            label = db.execute(sql(
                "SELECT correct, was_audit_sample, was_auto_approved, sample_weight "
                "FROM calibration_labels WHERE source_id = :id"
            ), {"id": audit.evaluation.id}).one()
            assert label[0] is True and label[1] is True and label[2] is True
            assert float(label[3]) == 20.0, label

            # Suspended: every PASS goes to review, with the probability kept.
            live = refit.live_model(db, organization_id=org_id, decision_type="assertion.presence")
            assert live is not None
            live.status = "SUSPENDED"
            live.suspended_reason = "Paused by the ARCH-35 gate."
            live.suspended_at = t0
            db.flush()
            for _ in range(20):
                result = SimpleNamespace(
                    verdict="PASS", raw_score=Decimal("0.99900"), extracted_value=None,
                    evidence=[{"quote": "renews automatically", "confidence": 0.9}],
                    matched_phrases=(), token_usage=None,
                )
                out = triage.record_evaluation(
                    db, definition=definition, evaluation_result=result,
                    node_run_id=node_run(), work_item_id=work_item(), meter=False,
                )
                assert out.evaluation.routed_to == "TRIAGE"
                assert out.evaluation.calibrated_probability is not None
            db.flush()
            assert db.query(AssertionEvaluation).filter(
                AssertionEvaluation.definition_id == definition.id
            ).count() == 140
        finally:
            apply.autonomy_enabled = original  # type: ignore[assignment]

    rec.check("DB: ARCH-33's writer on a stored model — FK, SET NULL, audits, suspension, harvest", arch33_path_end_to_end)

    db.rollback()
    db.close()


# ===========================================================================
# Mutations
# ===========================================================================

MUTANTS: list[dict[str, Any]] = [
    {
        "id": "M1",
        "name": "+1 finite-sample correction removed",
        "file": "app/services/calibration/risk.py",
        "find": "    return (float(k) + 1.0) / (float(n) + 1.0)",
        "replace": "    return float(k) / max(float(n), 1e-12)",
        "must": "die",
    },
    {
        "id": "M2",
        "name": "Clopper-Pearson replaced by the point estimate",
        "file": "app/services/calibration/risk.py",
        "find": "    value = float(beta.ppf(confidence, k + 1.0, m - k))",
        "replace": "    value = k / m",
        "must": "die",
    },
    {
        "id": "M3",
        "name": "isotonic selected below 200 labels",
        "file": "app/services/calibration/estimators.py",
        "find": "    if label_count < vocab.MIN_LABELS_ISOTONIC:\n        return vocab.METHOD_PLATT",
        "replace": "    if label_count < vocab.MIN_LABELS_PLATT + 10:\n        return vocab.METHOD_PLATT",
        "must": "die",
    },
    {
        "id": "M4",
        "name": "audit samples excluded from the labelled set",
        "file": "app/services/calibration/fit.py",
        "find": "    for row in rows:\n        weight = float(",
        "replace": "    for row in rows:\n        if getattr(row, \"was_audit_sample\", False):\n            continue\n        weight = float(",
        "must": "die",
    },
    {
        "id": "M5",
        "name": "audit sampling disabled",
        "file": "app/services/calibration/sampling.py",
        "find": "    return unit_draw(model_id, sample_key) < value",
        "replace": "    return False",
        "must": "die",
    },
    {
        "id": "M6",
        "name": "suspension check skipped in the decision apply.calibrated makes",
        "file": "app/services/calibration/decision.py",
        "find": "    if snapshot.status == vocab.STATUS_SUSPENDED:",
        "replace": "    if False:",
        "must": "die",
    },
    {
        "id": "M7",
        "name": "staleness ignored (an unmonitored model keeps deciding)",
        "file": "app/services/calibration/decision.py",
        "find": "    if monitor.is_stale(snapshot.last_checked_at, now):",
        "replace": "    if False:",
        "must": "die",
    },
    {
        "id": "M8",
        "name": "Platt slope no longer clamped at zero",
        "file": "app/services/calibration/estimators.py",
        "find": "    if a < 0.0:\n        # The constrained optimum is on the boundary.",
        "replace": "    if False:\n        # The constrained optimum is on the boundary.",
        "must": "die",
    },
    {
        "id": "M9",
        "name": "PSI compared against a threshold it can never reach",
        "file": "app/services/calibration/monitor.py",
        "find": "psi_value > vocab.PSI_THRESHOLD",
        "replace": "psi_value > vocab.PSI_THRESHOLD * 100",
        "must": "die",
    },
    {
        "id": "CONTROL",
        "name": "resume evidence requirement tightened from 20 to 25 labels",
        "file": "app/services/calibration/vocabulary.py",
        "find": "RESUME_MIN_NEW_LABELS: int = 20",
        "replace": "RESUME_MIN_NEW_LABELS: int = 25",
        "must": "survive",
    },
]


def _fast_gates(rec: Recorder, root: Path) -> None:
    eng = _engines(root)
    gates_vocabulary(rec, eng, root=root)
    gates_estimators(rec, eng)
    gates_risk(rec, eng)
    gates_monitor(rec, eng)
    gates_sampling(rec, eng)
    gates_decision(rec, eng)
    gates_arch33_interface(rec, eng, root=root)


def run_mutations(rec: Recorder) -> None:
    sys.dont_write_bytecode = True
    for mutant in MUTANTS:

        def make_gate(mutant: dict[str, Any] = mutant) -> Callable[[], None]:
            def gate() -> None:
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp) / "backend"
                    shutil.copytree(
                        BACKEND / "app", root / "app",
                        ignore=shutil.ignore_patterns("__pycache__"),
                    )
                    shutil.copytree(
                        BACKEND / "alembic" / "versions", root / "alembic" / "versions",
                        ignore=shutil.ignore_patterns("__pycache__"),
                    )
                    target = root / mutant["file"]
                    text = target.read_text(encoding="utf-8")
                    assert mutant["find"] in text, (
                        f"{mutant['id']}: anchor not found in {mutant['file']}"
                    )
                    target.write_text(text.replace(mutant["find"], mutant["replace"], 1), encoding="utf-8")
                    importlib.invalidate_caches()

                    sub = Recorder()
                    devnull = open(os.devnull, "w", encoding="utf-8")
                    original = sys.stdout
                    sys.stdout = devnull
                    try:
                        _fast_gates(sub, root)
                    except Exception:  # noqa: BLE001
                        sub.results.append(("harness", False, "raised"))
                    finally:
                        sys.stdout = original
                        devnull.close()
                        _stub_packages(BACKEND)
                    died = sub.failed > 0
                    if mutant["must"] == "die":
                        assert died, f"{mutant['id']} SURVIVED: {mutant['name']}"
                    else:
                        assert not died, (
                            f"{mutant['id']} DIED: "
                            f"{[n for n, ok, _ in sub.results if not ok]}"
                        )

            return gate

        verb = "DIES" if mutant["must"] == "die" else "SURVIVES"
        rec.check(f"{mutant['id']} {mutant['name']} -> {verb}", make_gate())
    _engines(BACKEND)


# ===========================================================================
# Regressions
# ===========================================================================


def run_regressions(rec: Recorder, *, db: bool, database_url: Optional[str]) -> None:
    for script in REGRESSIONS:
        path = BACKEND / script

        def make_gate(script: str = script, path: Path = path) -> Callable[[], None]:
            def gate() -> None:
                assert path.exists(), f"{script} is missing"
                command = [sys.executable, str(path)]
                if script == "verify_arch33.py":
                    command.append("--skip-regressions")
                if db:
                    if script == "verify_arch34.py":
                        command.append("--db")
                    else:
                        command += ["--db", "--database-url", _database_url(database_url)]
                completed = subprocess.run(
                    command, cwd=str(BACKEND), capture_output=True, text=True, timeout=1800
                )
                tail = "\n".join((completed.stdout or completed.stderr).splitlines()[-4:])
                print(f"         ({script}) {tail.splitlines()[-1] if tail else ''}")
                assert completed.returncode == 0, (
                    f"{script} exited {completed.returncode}\n"
                    + "\n".join(completed.stdout.splitlines()[-25:])
                )

            return gate

        rec.check(f"regression: {script}{' --db' if db else ''}", make_gate())


# ===========================================================================
# main
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-35 verification")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--skip-regressions", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    print("ARCH-35 — Calibrated Autonomy & Conformal Risk Control")
    print(f"backend: {BACKEND}")

    try:
        eng = _engines(BACKEND)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 2

    offline = Recorder()
    gates_vocabulary(offline, eng)
    gates_purity(offline)
    gates_estimators(offline, eng)
    gates_risk(offline, eng)
    gates_statistics(offline, eng)
    gates_monitor(offline, eng)
    gates_sampling(offline, eng)
    gates_decision(offline, eng)
    gates_arch33_interface(offline, eng)
    gates_wiring(offline)
    gates_console(offline)
    offline.report("offline")
    failed = offline.failed

    if args.db:
        live = Recorder()
        try:
            gates_db(live)
        except Exception:  # noqa: BLE001
            live.results.append(("database harness", False, traceback.format_exc()))
        live.report("database")
        failed += live.failed

    if args.mutate:
        mutate = Recorder()
        run_mutations(mutate)
        mutate.report("mutation")
        failed += mutate.failed

    if not args.skip_regressions:
        regression = Recorder()
        run_regressions(regression, db=args.db, database_url=args.database_url)
        regression.report("regression")
        failed += regression.failed

    print(f"\n{'FAILED' if failed else 'PASSED'} — {failed} gate(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
