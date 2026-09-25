"""ARCH44-S1:validate — arithmetic validation. Pure; no I/O.

Relations are DISCOVERED from the table, not assumed from its headers: a
relation is real when it holds on at least 75% of the (at least three) rows it
can be checked on. The rows where it then fails are the flagged cells, and
every cell a passing check touches has its confidence raised.

  RUNNING_BALANCE  balance[i] = balance[i-1] + credit[i] - debit[i], or with a
                   single signed amount (Dr/Cr). Carry rows (b/f, c/f) must
                   move the balance by nothing (CARRY_FORWARD).
  ROW_PRODUCT      amount = quantity x rate.
  ROW_TOTAL        total = a + b (+ c), a - b, or the sum of the amounts left of it.
  COLUMN_SUM       a TOTAL row = the sum of the body rows above it.
  HIERARCHY_SUM    a SUBTOTAL row = the sum of its group's rows.

LOCALISATION
============

A failed balance transition is ambiguous: the amount may be misread, or the
balance may. When transition i fails AND transition i+1 fails AND the balance
after i+1 is exactly what the one before i predicts through both amounts, the
balance at i is the misread cell; otherwise the amount at i is. A total that
fails only because of a cell already flagged (it reconciles when that cell's
expected value is used) is recorded as explained, not flagged a second time:
the reviewer is shown the cause, not its echoes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations, permutations
from typing import Callable, Optional, Sequence

from app.services.tables import vocabulary as v

TOL = Decimal(v.MONEY_TOLERANCE)


@dataclass
class Grid:
    n_rows: int
    n_cols: int
    header_rows: int
    kinds: list[str]
    levels: list[int]
    types: list[str]
    roles: list[str]
    values: dict[tuple[int, int], Decimal]
    names: list[str] = field(default_factory=list)

    def name(self, c: int) -> str:
        return self.names[c] if c < len(self.names) and self.names[c] else f"column {c + 1}"

    def val(self, r: int, c: int) -> Optional[Decimal]:
        return self.values.get((r, c))


@dataclass
class Check:
    kind: str
    scope: str
    outcome: str
    row: Optional[int] = None
    col: Optional[int] = None
    expected: Optional[Decimal] = None
    actual: Optional[Decimal] = None
    checked: int = 0
    failed: int = 0
    message: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class Result:
    checks: list[Check] = field(default_factory=list)
    support: dict[tuple[int, int], int] = field(default_factory=dict)
    flagged: dict[tuple[int, int], Check] = field(default_factory=dict)
    balance_col: Optional[int] = None

    @property
    def relations(self) -> list[Check]:
        return [c for c in self.checks if c.scope == v.SCOPE_RELATION]

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.scope == v.SCOPE_CELL]

    def support_cells(self, *cells: tuple[int, int]) -> None:
        for cell in cells:
            self.support[cell] = self.support.get(cell, 0) + 1

    def flag(self, check: Check) -> None:
        key = (check.row, check.col)
        if key not in self.flagged:
            self.flagged[key] = check  # type: ignore[index]
            self.checks.append(check)


def close(a: Decimal, b: Decimal, tol: Decimal = TOL) -> bool:
    return abs(a - b) <= tol + abs(b) * Decimal("0.0000005")


def _fmt(d: Optional[Decimal]) -> str:
    return "" if d is None else f"{d:,.2f}"


# ---------------------------------------------------------------------------
# Running balance
# ---------------------------------------------------------------------------


@dataclass
class _Transition:
    prev_r: int
    r: int
    prev_b: Decimal
    b: Decimal
    delta: Decimal
    has_amount: bool
    carry: bool

    @property
    def ok(self) -> bool:
        return close(self.prev_b + self.delta, self.b)


def _transitions(g: Grid, seq: Sequence[int], bal: int, amount: Callable[[int], Optional[Decimal]]) -> list[_Transition]:
    out: list[_Transition] = []
    prev: Optional[tuple[int, Decimal]] = None
    for r in seq:
        b = g.val(r, bal)
        if b is None:
            continue
        if prev is not None:
            a = amount(r)
            carry = g.kinds[r] == v.ROW_CARRY
            out.append(_Transition(prev[0], r, prev[1], b, a if a is not None else Decimal(0), a is not None, carry))
        prev = (r, b)
    return out


def _running_balance(g: Grid, res: Result) -> None:
    seq = [r for r in range(g.header_rows, g.n_rows) if g.kinds[r] in (v.ROW_BODY, v.ROW_CARRY)]
    body = [r for r in seq if g.kinds[r] == v.ROW_BODY]
    if len(body) < v.RELATION_MIN_ROWS + 1:
        return
    numeric = [c for c in range(g.n_cols) if g.types[c] in (v.TYPE_MONEY, v.TYPE_NUMBER)]

    def share(c: int) -> float:
        return sum(1 for r in body if g.val(r, c) is not None) / len(body)

    balances = [c for c in numeric if share(c) >= 0.6]
    amounts = [c for c in numeric if share(c) >= 0.1]
    options: list[tuple[tuple, int, Callable[[int], Optional[Decimal]], dict]] = []
    for bal in balances:
        others = [c for c in amounts if c != bal]
        for d, c in permutations(others, 2):
            def pair(r: int, d=d, c=c) -> Optional[Decimal]:
                dv, cv = g.val(r, d), g.val(r, c)
                if dv is None and cv is None:
                    return None
                return (cv or Decimal(0)) - (dv or Decimal(0))
            options.append(((bal, d, c), bal, pair, {"balance_col": bal, "debit_col": d, "credit_col": c}))
        for a in others:
            for sign in (1, -1):
                def single(r: int, a=a, sign=sign) -> Optional[Decimal]:
                    av = g.val(r, a)
                    return None if av is None else av * sign
                options.append(((bal, a, sign), bal, single, {"balance_col": bal, "amount_col": a, "sign": sign}))
    best = None
    for key, bal, amount, detail in options:
        trans = _transitions(g, seq, bal, amount)
        body_trans = [t for t in trans if not t.carry]
        if len(body_trans) < v.RELATION_MIN_ROWS:
            continue
        if sum(1 for t in body_trans if t.has_amount) < 0.6 * len(body_trans):
            continue
        share_ok = sum(1 for t in trans if t.ok) / len(trans)
        roles = {g.roles[bal] == v.ROLE_BALANCE}
        rank = (share_ok, len(trans), 1 if True in roles else 0, -len(key))
        if share_ok >= v.RELATION_MIN_SHARE and (best is None or rank > best[0]):
            best = (rank, bal, amount, detail, trans)
    if best is None:
        return
    _, bal, amount, detail, trans = best
    res.balance_col = bal
    fails = [i for i, t in enumerate(trans) if not t.ok]
    handled: set[int] = set()
    for i, t in enumerate(trans):
        if t.ok:
            # A transition that reconciles corroborates both balances and the amount.
            res.support_cells((t.prev_r, bal), (t.r, bal),
                              *[(t.r, c) for c in _amount_cols(detail) if g.val(t.r, c) is not None])
    for i in fails:
        if i in handled:
            continue
        t = trans[i]
        kind = v.CHECK_CARRY_FORWARD if t.carry else v.CHECK_RUNNING_BALANCE
        nxt = trans[i + 1] if i + 1 < len(trans) else None
        if nxt is not None and (i + 1) in fails and nxt.prev_r == t.r and close(t.prev_b + t.delta + nxt.delta, nxt.b):
            expected = t.prev_b + t.delta
            res.flag(Check(kind, v.SCOPE_CELL, v.OUTCOME_FAIL, t.r, bal, expected, t.b,
                           message=f"Balance reads {_fmt(t.b)}; the previous balance and this row's amounts give {_fmt(expected)}.",
                           detail={"cause": "BALANCE", **detail}))
            handled.add(i + 1)
            continue
        needed = t.b - t.prev_b
        target, expected = _amount_target(g, t.r, detail, needed)
        if target is None or t.carry:
            expected = t.prev_b + t.delta
            res.flag(Check(kind, v.SCOPE_CELL, v.OUTCOME_FAIL, t.r, bal, expected, t.b,
                           message=(f"Carried balance {_fmt(t.b)} differs from {_fmt(expected)} brought from the row above."
                                    if t.carry else f"Balance reads {_fmt(t.b)}; expected {_fmt(expected)}."),
                           detail={"cause": "BALANCE", **detail}))
            continue
        res.flag(Check(kind, v.SCOPE_CELL, v.OUTCOME_FAIL, t.r, target, expected, g.val(t.r, target),
                       message=f"The balance moves by {_fmt(abs(needed))}, so this amount should be {_fmt(expected)}.",
                       detail={"cause": "AMOUNT", **detail}))
    failed = len([c for c in res.failures if c.kind in (v.CHECK_RUNNING_BALANCE, v.CHECK_CARRY_FORWARD)])
    res.checks.append(Check(v.CHECK_RUNNING_BALANCE, v.SCOPE_RELATION,
                            v.OUTCOME_PASS if failed == 0 else v.OUTCOME_FAIL, checked=len(trans), failed=failed,
                            message=f"Running balance: {len(trans) - failed} of {len(trans)} rows reconcile.",
                            detail=detail))


def _amount_cols(detail: dict) -> list[int]:
    return [detail[k] for k in ("debit_col", "credit_col", "amount_col") if k in detail]


def _amount_target(g: Grid, r: int, detail: dict, needed: Decimal) -> tuple[Optional[int], Optional[Decimal]]:
    if "amount_col" in detail:
        a = detail["amount_col"]
        return (a, needed * detail["sign"]) if g.val(r, a) is not None else (None, None)
    d, c = detail["debit_col"], detail["credit_col"]
    dv, cv = g.val(r, d), g.val(r, c)
    if cv is not None and dv is None:
        return c, needed
    if dv is not None and cv is None:
        return d, -needed
    if dv is not None and cv is not None:
        return c, needed + dv
    return None, None


# ---------------------------------------------------------------------------
# Row relations
# ---------------------------------------------------------------------------


def _row_relations(g: Grid, res: Result) -> None:
    body = [r for r in range(g.header_rows, g.n_rows) if g.kinds[r] == v.ROW_BODY]
    if len(body) < v.RELATION_MIN_ROWS:
        return
    numeric = [c for c in range(g.n_cols) if g.types[c] in (v.TYPE_MONEY, v.TYPE_NUMBER) and c != res.balance_col]
    candidates: list[tuple[tuple, str, int, tuple[int, ...], Callable]] = []
    for t in numeric:
        others = [c for c in numeric if c != t]
        for a, b in combinations(others, 2):
            candidates.append(((0, t, a, b), v.CHECK_ROW_PRODUCT, t, (a, b), lambda x: x[0] * x[1]))
            candidates.append(((1, t, a, b), v.CHECK_ROW_TOTAL, t, (a, b), lambda x: x[0] + x[1]))
            candidates.append(((2, t, a, b), v.CHECK_ROW_TOTAL, t, (a, b), lambda x: x[0] - x[1]))
            candidates.append(((2, t, b, a), v.CHECK_ROW_TOTAL, t, (b, a), lambda x: x[0] - x[1]))
        for a, b, c in combinations(others, 3):
            candidates.append(((3, t, a, b, c), v.CHECK_ROW_TOTAL, t, (a, b, c), lambda x: x[0] + x[1] + x[2]))
        left = [c for c in others if c < t]
        if len(left) >= 4:
            candidates.append(((4, t), v.CHECK_ROW_TOTAL, t, tuple(left), lambda x: sum(x, Decimal(0))))
    formulas = {v.CHECK_ROW_PRODUCT: "×", v.CHECK_ROW_TOTAL: "+"}
    chosen: dict[int, tuple] = {}
    for key, kind, t, ops, fn in candidates:
        rows = [r for r in body if g.val(r, t) is not None and all(g.val(r, o) is not None for o in ops)]
        if len(rows) < v.RELATION_MIN_ROWS:
            continue
        if kind == v.CHECK_ROW_TOTAL and any(sum(1 for r in rows if g.val(r, o) != 0) < 0.5 * len(rows) for o in ops):
            continue  # an always-zero operand makes any column "equal" another
        ok = [r for r in rows if close(fn([g.val(r, o) for o in ops]), g.val(r, t))]
        share = len(ok) / len(rows)
        if share < v.RELATION_MIN_SHARE:
            continue
        rank = (share, len(rows), -key[0])
        if t not in chosen or rank > chosen[t][0]:
            chosen[t] = (rank, kind, ops, fn, rows, key)
    used_operands: set[int] = set()
    for t, (_, kind, ops, fn, rows, key) in sorted(chosen.items(), key=lambda kv: -kv[1][0][0]):
        if t in used_operands and kind == v.CHECK_ROW_TOTAL:
            continue
        used_operands.update(ops)
        failed = 0
        for r in rows:
            expected = fn([g.val(r, o) for o in ops])
            if close(expected, g.val(r, t)):
                res.support_cells((r, t), *[(r, o) for o in ops])
            else:
                failed += 1
                symbol = "×" if kind == v.CHECK_ROW_PRODUCT else ("−" if key[0] == 2 else "+")
                res.flag(Check(kind, v.SCOPE_CELL, v.OUTCOME_FAIL, r, t, expected, g.val(r, t),
                               message=f"Should be {_fmt(expected)} ({f' {symbol} '.join(g.name(o) for o in ops)}); reads {_fmt(g.val(r, t))}.",
                               detail={"target_col": t, "operand_cols": list(ops), "op": symbol}))
        res.checks.append(Check(kind, v.SCOPE_RELATION, v.OUTCOME_PASS if failed == 0 else v.OUTCOME_FAIL,
                                checked=len(rows), failed=failed,
                                message=f"{g.name(t)} = {f' {formulas[kind]} '.join(g.name(o) for o in ops)}: "
                                        f"{len(rows) - failed} of {len(rows)} rows reconcile.",
                                detail={"target_col": t, "operand_cols": list(ops),
                                        "op": "×" if kind == v.CHECK_ROW_PRODUCT else ("−" if key[0] == 2 else "+")}))


# ---------------------------------------------------------------------------
# Sums: totals and subtotals
# ---------------------------------------------------------------------------


def _expected_or_actual(res: Result, g: Grid, r: int, c: int) -> Decimal:
    check = res.flagged.get((r, c))
    if check is not None and check.expected is not None:
        return check.expected
    return g.val(r, c) or Decimal(0)


def _sum_check(g: Grid, res: Result, kind: str, r: int, leaves: list[int], label: str) -> tuple[int, int]:
    checked = failed = 0
    for c in range(g.n_cols):
        printed = g.val(r, c)
        if printed is None or c == res.balance_col or g.types[c] not in (v.TYPE_MONEY, v.TYPE_NUMBER):
            continue
        values = [g.val(x, c) for x in leaves if g.val(x, c) is not None]
        if not values:
            continue
        checked += 1
        total = sum(values, Decimal(0))
        if close(total, printed):
            res.support_cells((r, c))
            continue
        corrected = sum((_expected_or_actual(res, g, x, c) for x in leaves if g.val(x, c) is not None), Decimal(0))
        if close(corrected, printed):
            continue  # explained by a cell already flagged
        failed += 1
        res.flag(Check(kind, v.SCOPE_CELL, v.OUTCOME_FAIL, r, c, total, printed,
                       message=f"{label} reads {_fmt(printed)}; the {len(values)} rows above add up to {_fmt(total)}.",
                       detail={"rows": leaves}))
    return checked, failed


def _sums(g: Grid, res: Result) -> None:
    rows = list(range(g.header_rows, g.n_rows))
    totals_checked = totals_failed = subs_checked = subs_failed = 0
    last_total = g.header_rows - 1
    for r in rows:
        kind = g.kinds[r]
        if kind == v.ROW_SUBTOTAL:
            leaves = []
            for x in range(r - 1, g.header_rows - 1, -1):
                k = g.kinds[x]
                if k in (v.ROW_SUBTOTAL, v.ROW_TOTAL) or (k == v.ROW_SECTION and g.levels[x] <= g.levels[r]):
                    break
                if k == v.ROW_BODY:
                    leaves.append(x)
            if leaves:
                c_, f_ = _sum_check(g, res, v.CHECK_HIERARCHY_SUM, r, sorted(leaves), "Subtotal")
                subs_checked, subs_failed = subs_checked + c_, subs_failed + f_
        elif kind == v.ROW_TOTAL:
            leaves = [x for x in range(last_total + 1, r) if g.kinds[x] == v.ROW_BODY]
            if leaves:
                c_, f_ = _sum_check(g, res, v.CHECK_COLUMN_SUM, r, leaves, "Total")
                totals_checked, totals_failed = totals_checked + c_, totals_failed + f_
            last_total = r
    if subs_checked:
        res.checks.append(Check(v.CHECK_HIERARCHY_SUM, v.SCOPE_RELATION,
                                v.OUTCOME_PASS if subs_failed == 0 else v.OUTCOME_FAIL, checked=subs_checked,
                                failed=subs_failed, message=f"Subtotals: {subs_checked - subs_failed} of {subs_checked} reconcile."))
    if totals_checked:
        res.checks.append(Check(v.CHECK_COLUMN_SUM, v.SCOPE_RELATION,
                                v.OUTCOME_PASS if totals_failed == 0 else v.OUTCOME_FAIL, checked=totals_checked,
                                failed=totals_failed, message=f"Totals: {totals_checked - totals_failed} of {totals_checked} reconcile."))


def _carries_without_balance(g: Grid, res: Result) -> None:
    """Brought/carried-forward pairs must agree even when no running balance exists."""
    if res.balance_col is not None:
        return
    carries = [r for r in range(g.header_rows, g.n_rows) if g.kinds[r] == v.ROW_CARRY]
    checked = failed = 0
    for a, b in zip(carries, carries[1:]):
        if any(g.kinds[x] == v.ROW_BODY for x in range(a + 1, b)):
            continue
        for c in range(g.n_cols):
            va, vb = g.val(a, c), g.val(b, c)
            if va is None or vb is None:
                continue
            checked += 1
            if close(va, vb):
                res.support_cells((a, c), (b, c))
            else:
                failed += 1
                res.flag(Check(v.CHECK_CARRY_FORWARD, v.SCOPE_CELL, v.OUTCOME_FAIL, b, c, va, vb,
                               message=f"Brought forward {_fmt(vb)} differs from {_fmt(va)} carried forward."))
    if checked:
        res.checks.append(Check(v.CHECK_CARRY_FORWARD, v.SCOPE_RELATION, v.OUTCOME_PASS if failed == 0 else v.OUTCOME_FAIL,
                                checked=checked, failed=failed,
                                message=f"Carried-forward figures: {checked - failed} of {checked} agree."))


def validate(g: Grid) -> Result:
    res = Result()
    _running_balance(g, res)
    _row_relations(g, res)
    _sums(g, res)
    _carries_without_balance(g, res)
    return res


def adjust(base: float, support: int, flagged: bool) -> float:
    """Cell confidence after validation: every passing check halves the doubt
    (at most twice); a failing one cuts confidence to 40%."""
    if flagged:
        return round(max(0.0, base * 0.4), 4)
    doubt = 1.0 - base
    return round(min(1.0, 1.0 - doubt * (0.5 ** min(support, 2))), 4)


def status_of(res: Result) -> str:
    if res.failures:
        return v.STATUS_FLAGGED
    if res.relations:
        return v.STATUS_VALIDATED
    return v.STATUS_EXTRACTED


__all__ = ["Check", "Grid", "Result", "adjust", "close", "status_of", "validate"]
