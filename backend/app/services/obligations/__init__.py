"""ARCH46-S1:package — Obligations & Temporal Intelligence (capability.obligations,
Business and Enterprise).

Pure (no I/O):  vocabulary, temporal (month ends, leap years, business days,
                RRULE, local dates), holidays (stored calendars: templates and
                .ics import), extract (clauses + typed values + ARCH-33's notice
                reader -> obligation drafts; no model call), ical (RFC 5545
                writer), feeds (signed feed tokens).
Around them:    service (the database: extraction upsert, a person's changes,
                the sweep, calendars, feeds), gate.
Planted sets:   synthetic (verify_arch46 gates; truth from dateutil).
"""

from app.services.obligations import vocabulary

__all__ = ["vocabulary"]
