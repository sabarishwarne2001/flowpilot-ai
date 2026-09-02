#!/usr/bin/env python
"""ARCH-17 — Observability & Per-Tenant SLOs. Verification gate.

BACKFILLED BY ARCH-0V TRANCHE 8 (finding G4)

ARCH-17 shipped without a gate. Every other phase from ARCH-04 onward has one;
`scripts/verify_arch17.py` simply did not exist at ARCH-22 completion, so the
SLO registry, the histogram aggregator, W3C traceparent propagation and
`DEFAULT_LATENCY_BOUNDS_MS` were unguarded.

That last one is not academic. **ARCH-21 gate check 21.5 asserts that
`api_key_usage_daily`'s histogram bounds match `DEFAULT_LATENCY_BOUNDS_MS`
exactly (16 buckets).** So ARCH-21's gate was anchored to a constant no gate
protected: edit the ARCH-17 tuple and `verify_arch21.py` fails with a message
pointing at ARCH-21, which is the wrong place to start looking. G6 below is
the check that makes the ARCH-21 failure legible.

Static by construction — this reads source and imports pure modules. It opens
no database connection, so it runs in CI's fast path.

    python scripts/verify_arch17.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Callable

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(number: str, description: str) -> Callable:
    def decorator(fn: Callable[[], None]) -> Callable[[], None]:
        def wrapped() -> None:
            try:
                fn()
                RESULTS.append((f"{number} {description}", True, ""))
            except AssertionError as exc:
                RESULTS.append((f"{number} {description}", False, str(exc)))
            except Exception as exc:  # noqa: BLE001
                RESULTS.append(
                    (f"{number} {description}", False, f"{type(exc).__name__}: {exc}")
                )

        wrapped.__name__ = fn.__name__
        return wrapped

    return decorator


def _source(rel: str) -> str:
    path = BACKEND_ROOT / rel
    assert path.exists(), f"{rel} does not exist"
    return path.read_text(encoding="utf-8-sig")


def _tree(rel: str) -> ast.Module:
    return ast.parse(_source(rel))


# ---------------------------------------------------------------------
# G1 — the SLO registry is a closed vocabulary
# ---------------------------------------------------------------------
@check("17.1", "SLO registry is a closed vocabulary built from SLOSpec")
def g1() -> None:
    from app.core.slo_registry import SLO_REGISTRY, SLOSpec

    assert SLO_REGISTRY, "SLO_REGISTRY is empty"
    for key, spec in SLO_REGISTRY.items():
        assert isinstance(spec, SLOSpec), f"{key} is not an SLOSpec"
        assert spec.key == key, (
            f"SLO_REGISTRY key {key!r} disagrees with spec.key {spec.key!r}. "
            f"The dict is built by comprehension from spec.key, so a mismatch "
            f"means someone hand-edited it."
        )
    assert SLOSpec.__dataclass_params__.frozen, (
        "SLOSpec must be frozen. A mutable spec means a target can be edited "
        "at runtime and the SLO silently starts measuring something else."
    )


# ---------------------------------------------------------------------
# G2 — every registry stage name is a real stage with a budget
# ---------------------------------------------------------------------
@check("17.2", "Every registry stage_name has a STAGE_BUDGETS entry")
def g2() -> None:
    from app.core.request_context import STAGE_BUDGETS
    from app.core.slo_registry import SLO_REGISTRY

    orphans = [
        spec.key
        for spec in SLO_REGISTRY.values()
        if spec.stage_name is not None and spec.stage_name not in STAGE_BUDGETS
    ]
    assert not orphans, (
        f"SLO specs naming a stage with no budget: {orphans}. The stage will "
        f"never emit a measurement and the SLO will read as permanently "
        f"healthy, which is the worst possible failure for an SLO."
    )


# ---------------------------------------------------------------------
# G3 — assert_registry_matches_budgets has a live call site
# ---------------------------------------------------------------------
@check("17.3", "assert_registry_matches_budgets is called, not just exported")
def g3() -> None:
    call_sites: list[str] = []
    for path in (BACKEND_ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8-sig")
        if "assert_registry_matches_budgets()" in text:
            call_sites.append(str(path.relative_to(BACKEND_ROOT)))

    assert call_sites, (
        "assert_registry_matches_budgets is defined in app/core/slo_registry.py "
        "and called from nowhere in app/. This is the orphaned-guard defect "
        "(invariant I4) — the same shape as require_superadmin before ARCH-18, "
        "require_api_key before ARCH-21, and ip_matches_pin before ARCH-19. "
        "Call it at application startup or from the SLO service."
    )


# ---------------------------------------------------------------------
# G4 — histogram bounds are monotonic and non-empty
# ---------------------------------------------------------------------
@check("17.4", "DEFAULT_LATENCY_BOUNDS_MS is strictly increasing")
def g4() -> None:
    from app.models.slo import DEFAULT_LATENCY_BOUNDS_MS

    bounds = list(DEFAULT_LATENCY_BOUNDS_MS)
    assert bounds, "DEFAULT_LATENCY_BOUNDS_MS is empty"
    assert bounds == sorted(bounds), "bounds are not sorted"
    assert len(bounds) == len(set(bounds)), "bounds contain duplicates"
    assert all(b > 0 for b in bounds), "bounds contain a non-positive value"


# ---------------------------------------------------------------------
# G5 — bucket_bounds_for never silently drops the target
# ---------------------------------------------------------------------
@check("17.5", "bucket_bounds_for merges the target into the bounds")
def g5() -> None:
    from app.models.slo import (
        DEFAULT_LATENCY_BOUNDS_MS,
        SLOUnit,
        bucket_bounds_for,
    )

    merged = bucket_bounds_for(137.0, unit=SLOUnit.MILLISECONDS)
    assert 137.0 in merged, (
        "A target that is not a bucket boundary cannot be measured exactly; "
        "bucket_bounds_for must insert it."
    )
    assert list(merged) == sorted(merged), "merged bounds are not sorted"

    default = bucket_bounds_for(None, unit=SLOUnit.MILLISECONDS)
    assert tuple(default) == tuple(sorted(DEFAULT_LATENCY_BOUNDS_MS)), (
        "With no target, bucket_bounds_for must return the defaults unchanged."
    )

    assert bucket_bounds_for(0.99, unit=SLOUnit.RATIO) == (), (
        "Ratio SLOs have no latency buckets; returning some would produce a "
        "histogram over a unit that has no distribution."
    )


# ---------------------------------------------------------------------
# G6 — the ARCH-21 coupling, made explicit
# ---------------------------------------------------------------------
@check("17.6", "ARCH-21 rollup histogram bounds still match ARCH-17's")
def g6() -> None:
    from app.models.slo import DEFAULT_LATENCY_BOUNDS_MS

    assert len(DEFAULT_LATENCY_BOUNDS_MS) == 16, (
        f"DEFAULT_LATENCY_BOUNDS_MS has {len(DEFAULT_LATENCY_BOUNDS_MS)} "
        f"buckets, not 16. `api_key_usage_daily` (ARCH-21) stores a fixed-width "
        f"histogram against these bounds and verify_arch21.py check 21.5 "
        f"asserts the width. Changing this tuple silently reinterprets every "
        f"latency figure already stored in the rollup — the old rows keep their "
        f"old counts under the new labels. Migrate the rollup, or do not change "
        f"the tuple."
    )


# ---------------------------------------------------------------------
# G7 — the aggregator is bounded
# ---------------------------------------------------------------------
@check("17.7", "Histogram aggregator has a series cap and reports drops")
def g7() -> None:
    from app.core import slo_recorder

    cap = getattr(slo_recorder, "MAX_TRACKED_SERIES", None)
    assert isinstance(cap, int) and cap > 0, (
        "slo_recorder.MAX_TRACKED_SERIES must be a positive int. An unbounded "
        "in-process aggregator keyed by tenant is a memory leak with a "
        "customer-count-shaped growth curve."
    )

    source = _source("app/core/slo_recorder.py")
    assert "MAX_TRACKED_SERIES" in source and "dropped" in source, (
        "The cap must be enforced AND the drop counted. A cap that silently "
        "discards measurements produces an SLO that improves under load."
    )


# ---------------------------------------------------------------------
# G8 — traceparent is W3C-shaped and round-trips
# ---------------------------------------------------------------------
@check("17.8", "traceparent is W3C-formatted and parse_traceparent round-trips")
def g8() -> None:
    from app.core.request_context import parse_traceparent

    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    span_id = "00f067aa0ba902b7"
    parsed_trace, parsed_span = parse_traceparent(
        f"00-{trace_id}-{span_id}-01"
    )
    assert parsed_trace == trace_id, f"trace id lost: {parsed_trace!r}"
    assert parsed_span == span_id, f"span id lost: {parsed_span!r}"

    for garbage in ("", "nonsense", "00-short-00f067aa0ba902b7-01", None):
        result = parse_traceparent(garbage)
        assert result == (None, None), (
            f"parse_traceparent({garbage!r}) returned {result!r}; malformed "
            f"input must yield (None, None) rather than a partial trace that "
            f"looks real in a dashboard."
        )


# ---------------------------------------------------------------------
# G9 — the queue-boundary carrier exists and carries the trace
# ---------------------------------------------------------------------
@check("17.9", "carrier() exposes traceparent for the queue boundary")
def g9() -> None:
    from app.core.request_context import carrier, request_scope, traceparent

    with request_scope(request_id="arch17-gate-probe"):
        payload = carrier()
        assert isinstance(payload, dict), "carrier() must return a dict"
        assert payload, "carrier() returned an empty dict inside a request scope"
        current = traceparent()
        assert current, "traceparent() is empty inside a request scope"
        assert any(
            value == current for value in payload.values()
        ), (
            f"carrier() does not include the current traceparent. A job "
            f"enqueued with this carrier loses its trace at the queue "
            f"boundary, which is the one place ARCH-17 exists to instrument. "
            f"carrier={payload!r} traceparent={current!r}"
        )


# ---------------------------------------------------------------------
# G10 — the trace middleware is installed
# ---------------------------------------------------------------------
@check("17.10", "RequestTraceMiddleware is registered in main.py")
def g10() -> None:
    source = _source("app/main.py")
    assert "RequestTraceMiddleware" in source, (
        "RequestTraceMiddleware is not referenced in app/main.py. ARCH-17 "
        "added it precisely because ~175 routes had no trace scope."
    )
    assert "app.add_middleware(RequestTraceMiddleware)" in source, (
        "RequestTraceMiddleware is imported but never added. An imported, "
        "unregistered middleware is the orphaned-guard defect wearing a "
        "different hat."
    )


# ---------------------------------------------------------------------
# G11 — SLO models are registered in the model registry
# ---------------------------------------------------------------------
@check("17.11", "app/models/__init__.py registers the SLO module")
def g11() -> None:
    source = _source("app/models/__init__.py")
    assert "slo" in source, (
        "app/models/slo.py is not registered in app/models/__init__.py. An "
        "unregistered model module means `alembic revision --autogenerate` "
        "cannot see its tables and will propose dropping them. This exact "
        "defect was found for slo.py and dunning_action.py during ARCH-20."
    )
    for symbol in ("SLODefinition", "SLOObservation", "SLOMeasurement"):
        assert symbol in source, f"{symbol} is not exported from app.models"


# ---------------------------------------------------------------------
# G12 — SLOMethod distinguishes measured from interpolated
# ---------------------------------------------------------------------
@check("17.12", "SLOMethod separates EXACT from HISTOGRAM_INTERPOLATED")
def g12() -> None:
    from app.models.slo import SLOMethod

    values = {member.value for member in SLOMethod}
    assert {"EXACT", "HISTOGRAM_INTERPOLATED"} <= values, (
        f"SLOMethod must distinguish an exact measurement from one "
        f"interpolated out of a histogram. Found: {sorted(values)}. A p95 "
        f"read off 16 buckets is an estimate, and a dashboard that presents "
        f"it as measured is making a claim the data does not support. ARCH-21 "
        f"carries the same label on its latency percentiles."
    )


# ---------------------------------------------------------------------
# G13 — no zero-coercion of unmeasured latency (invariant I9)
# ---------------------------------------------------------------------
@check("17.13", "SLO service never coerces an unmeasured value to zero")
def g13() -> None:
    tree = _tree("app/services/slo_service.py")

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name not in ("coalesce", "COALESCE"):
            continue
        for arg in node.args[1:]:
            if isinstance(arg, ast.Constant) and arg.value in (0, 0.0):
                offenders.append(f"line {node.lineno}: coalesce(..., 0)")

    assert not offenders, (
        f"Zero-coercion found in slo_service: {offenders}. Invariant I9 — an "
        f"unmeasured latency is None, never 0.0. A zero renders as an "
        f"instantaneous response and makes a broken stage look like the "
        f"fastest one in the trace."
    )


def main() -> int:
    for fn in (g1, g2, g3, g4, g5, g6, g7, g8, g9, g10, g11, g12, g13):
        fn()

    print("=" * 74)
    print("ARCH-17 — Observability & Per-Tenant SLOs")
    print("  (gate backfilled by ARCH-0V Tranche 8, finding G4)")
    print("=" * 74)

    for label, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
        if not ok:
            for line in detail.splitlines():
                print(f"         {line}")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("-" * 74)
    print(f"  {passed} passed / {failed} failed")
    print("=" * 74)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())