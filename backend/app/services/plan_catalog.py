"""The published plans as a customer reads them (campaign session 4).

One place turns a published tier version into what a plan card shows, so the
in-app plan cards (`GET .../billing/plans`) and anything else that describes
the plans cannot disagree about a number.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Union


def allowance_quantity(value: Optional[Decimal]) -> Optional[Union[int, float]]:
    """A tier entry's quantity for a client: whole numbers stay whole, fractions are kept.

    Free stores 0.25 GB; an `int()` here made the plan card read 0 (and print "0 MB").
    None stays None: no ceiling.
    """
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


__all__ = ["allowance_quantity"]
