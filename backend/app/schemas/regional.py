"""A workspace's regional settings, checked where they enter (campaign session 3, F-320).

A workspace carries a clock (`timezone`), a fallback `currency` for three-way matching, a
`date_format` for dates on documents, and a `language`. Each is read by something that cannot
use a value it does not know: the obligation and export clocks need a real IANA zone, the
matching engine an ISO 4217 code, the document renderer one of `DATE_FORMATS`. A value outside
those was stored and shown back while every reader quietly fell back to its default, so the
setting said one thing and the product did another.

These validators are shared by `WorkspaceCreate`, `WorkspaceUpdate` and `OrganizationCreate`
(which makes the first workspace). `None` passes through: on an update it means "unchanged".
"""

from __future__ import annotations

import re
from typing import Optional
from zoneinfo import available_timezones

from app.core.display_format import DATE_FORMATS

#: Loaded once; the set does not change while the process runs.
_ZONES = frozenset(available_timezones()) | {"UTC"}
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,8}(-[A-Za-z0-9]{1,8})*$")


def check_timezone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if value not in _ZONES:
        raise ValueError(f"{value!r} is not an IANA time zone (for example Europe/Berlin or Asia/Kolkata).")
    return value


def check_currency(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip().upper()
    if not _CURRENCY.match(value):
        raise ValueError(f"{value!r} is not a three-letter ISO 4217 currency code (for example USD or EUR).")
    return value


def check_date_format(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if value not in DATE_FORMATS:
        raise ValueError(f"{value!r} is not one of the date formats: {', '.join(DATE_FORMATS)}.")
    return value


def check_language(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not _LANGUAGE.match(value):
        raise ValueError(f"{value!r} is not a language tag (for example en, de or pt-BR).")
    return value


__all__ = ["check_currency", "check_date_format", "check_language", "check_timezone"]
