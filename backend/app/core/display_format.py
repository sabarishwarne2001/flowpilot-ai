"""Campaign session 3 (N-064) — how the server writes dates and amounts that people read.

The frontend's display layer (`frontend/src/utils/displayTime.ts`) and this module apply one set
of rules:

* A **moment** (a sign-in, an upload, "ends at") is shown on the reader's clock: their profile's
  time zone, or the workspace's while they have none. Server-written moments (emails) say "UTC"
  explicitly, because one message can reach several people on different clocks.
* A **calendar date** (an invoice date, a due date, a contract end: a day, not a moment) is shown
  in the **workspace's date format**, the same setting that tells three-way matching how to read
  the dates on that workspace's documents, so a team reads document dates one way.
* An **amount** keeps its own currency; one with no currency is shown as a plain number (the
  workspace currency is what three-way matching assumes for it, not a label to print: F-172).
* Machine-readable output (webhook payloads, CSV and JSON exports, calendar feeds) stays ISO 8601,
  whatever the settings: spreadsheets and integrations parse that in every locale.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional, Union

#: The workspace date formats the settings page offers (Settings > Workspace > Date format).
DATE_FORMATS: tuple[str, ...] = (
    "YYYY-MM-DD", "DD-MM-YYYY", "MM-DD-YYYY", "DD/MM/YYYY", "MM/DD/YYYY", "YYYY/MM/DD",
)
DEFAULT_DATE_FORMAT = "YYYY-MM-DD"

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def format_calendar_date(value: Union[date, datetime, str, None], date_format: Optional[str]) -> str:
    """A day in the workspace's format: `format_calendar_date(date(2026, 12, 5), "DD/MM/YYYY")` → "05/12/2026".

    An unknown format falls back to ISO; a string that is not an ISO date is returned unchanged.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    else:
        match = _ISO_DATE.match(str(value).strip())
        if not match:
            return str(value)
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return str(value)
    pattern = date_format if date_format in DATE_FORMATS else DEFAULT_DATE_FORMAT
    return (
        pattern.replace("YYYY", f"{day.year:04d}")
        .replace("MM", f"{day.month:02d}")
        .replace("DD", f"{day.day:02d}")
    )


def is_iso_date(value: object) -> bool:
    return isinstance(value, str) and bool(_ISO_DATE.match(value.strip()))


__all__ = [
    "DATE_FORMATS",
    "DEFAULT_DATE_FORMAT",
    "format_calendar_date",
    "is_iso_date",
]


def format_amount_micros(amount_micros: int, currency: Optional[str]) -> str:
    """An amount held in micros, for text one message sends to several people (emails, notices).

    Its own currency as an ISO code, two decimals and thousands separators ("USD 1,234.50"):
    readable in every locale, never a guessed symbol (F-172). No currency, no code.
    """
    value = int(amount_micros) / 1_000_000
    text = f"{value:,.2f}"
    return f"{currency.upper()} {text}" if currency else text
