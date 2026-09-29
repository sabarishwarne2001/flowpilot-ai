"""ARCH-50 RevOps — the clock, the error envelope and money arithmetic. ARCH50-S1:revops-service"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.core.exceptions import FlowPilotError
from app.services.revops import vocabulary as v


def now() -> datetime:
    """ARCH-50 RevOps' ONE clock (injectable: the gates pin it to a date)."""
    return datetime.now(timezone.utc)


def today() -> date:
    return now().date()


_BY_CODE: dict[tuple[str, int], type] = {}


class RevOpsError(FlowPilotError):
    """A refusal the console branches on by `code`. The domain exception handler maps an error to its HTTP status
    and body code through the CLASS (`resolve_exception_mapping` walks the MRO), so each (code, status) gets its own
    subclass carrying them: a PRICE_NOT_SOLD is a 400 with code PRICE_NOT_SOLD, never a 409 REVOPS_CONFLICT."""

    status_code = 409
    code = "REVOPS_CONFLICT"

    def __new__(cls, message: str = "", code: str = "REVOPS_CONFLICT", status_code: int = 409, **details: Any) -> Any:
        key = (str(code), int(status_code))
        sub = _BY_CODE.get(key)
        if sub is None:
            sub = type(f"RevOpsError_{key[0]}_{key[1]}", (RevOpsError,), {"status_code": key[1], "code": key[0]})
            _BY_CODE[key] = sub
        return super().__new__(sub, message)

    def __init__(self, message: str, code: str, status_code: int = 409, **details: Any) -> None:
        super().__init__(message)
        if code not in v.CODES:
            raise ValueError(f"unknown RevOps code {code}")
        self.code = code
        self.status_code = status_code
        self.details = {"code": code, **{k: (str(x) if not isinstance(x, (int, float, bool, type(None))) else x)
                                         for k, x in details.items()}}


def to_minor_micros(micros: int | Decimal) -> int:
    """Round micros to whole minor units (cents / paise), half up."""
    value = Decimal(int(micros)) / v.MICROS_PER_MINOR_UNIT
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP)) * v.MICROS_PER_MINOR_UNIT


def minor_to_micros(minor: int) -> int:
    return int(minor) * v.MICROS_PER_MINOR_UNIT


def micros_to_minor(micros: int) -> int:
    return int(micros) // v.MICROS_PER_MINOR_UNIT


def add_months(day: date, months: int) -> date:
    """Calendar months, clamped to the last day (31 Jan + 1 month = 28/29 Feb)."""
    import calendar

    month0 = day.month - 1 + months
    year = day.year + month0 // 12
    month = month0 % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def month_start(day: date) -> date:
    return day.replace(day=1)


__all__ = ["RevOpsError", "add_months", "micros_to_minor", "minor_to_micros", "month_start", "now",
           "to_minor_micros", "today"]
