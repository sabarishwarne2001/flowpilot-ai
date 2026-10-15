"""Money in Helm: integer micros (one millionth of a currency unit), never floats.

The platform already stores amounts as micros (`document_roles.total_micros`,
`procurement_case_lines.*_micros`). Helm keeps that unit everywhere a number decides something;
floats appear only inside the simulation's arrays and are rounded back before anything is stored.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Optional, Union

MICROS = 1_000_000
Number = Union[int, float, Decimal, str]


def to_micros(value: Optional[Number]) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value * MICROS
    try:
        amount = Decimal(str(value).replace(",", "").strip())
    except Exception:  # noqa: BLE001 - unreadable is "unknown", never a guess
        return None
    return int((amount * MICROS).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def from_micros(micros: Optional[int]) -> Optional[Decimal]:
    if micros is None:
        return None
    return (Decimal(micros) / MICROS).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def pct_of(micros: int, pct: Decimal) -> int:
    """`pct` per cent of an amount, in micros (rounded half-even)."""
    return int((Decimal(micros) * pct / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def interest(micros: int, annual_rate_bps: int, days: int) -> int:
    """Simple interest on `micros` at `annual_rate_bps` (1200 = 12% a year) for `days` days."""
    if days <= 0 or annual_rate_bps <= 0 or micros <= 0:
        return 0
    value = Decimal(micros) * Decimal(annual_rate_bps) / Decimal(10_000) * Decimal(days) / Decimal(365)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))


def plain(micros: Optional[int], currency: Optional[str]) -> str:
    """A deterministic, locale-free rendering for explanations and certificates ("INR 1,25,000.00"
    style grouping is the display layer's job; this one is stable for tests and audit text)."""
    if micros is None:
        return "an unknown amount"
    amount = from_micros(micros) or Decimal(0)
    return f"{currency or ''} {amount:,.2f}".strip()


def lakh(micros: Optional[int]) -> str:
    """Indian grouping for INR explanations: ₹2.4 lakh, ₹1.2 crore, ₹45,000."""
    if micros is None:
        return "an unknown amount"
    rupees = Decimal(micros) / MICROS
    sign = "-" if rupees < 0 else ""
    rupees = abs(rupees)
    if rupees >= Decimal(10_000_000):
        return f"{sign}₹{(rupees / Decimal(10_000_000)).quantize(Decimal('0.01')).normalize():f} crore"
    if rupees >= Decimal(100_000):
        return f"{sign}₹{(rupees / Decimal(100_000)).quantize(Decimal('0.1')).normalize():f} lakh"
    return f"{sign}₹{rupees.quantize(Decimal('1')):,}"


def say(micros: Optional[int], currency: Optional[str]) -> str:
    """The amount as an explanation says it: lakh and crore for INR, grouped thousands otherwise."""
    if currency == "INR":
        return lakh(micros)
    return plain(micros, currency)


__all__ = ["MICROS", "from_micros", "interest", "lakh", "pct_of", "plain", "say", "to_micros"]
