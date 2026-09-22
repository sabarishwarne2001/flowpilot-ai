"""HARDENING-T1:D34 — `extra=` keys can never crash a log call.

`logging.Logger.makeRecord` raises KeyError when an `extra` key collides with
a LogRecord attribute ("created", "name", "module", "filename", "message",
"args", ...). The radar logged `extra=outcome.as_payload()`, whose payload
carries a `created` count, so with INFO logging on — production — every
`anomaly.scan_document` job raised AFTER doing its work, retried, and died.
Gates never saw it because they ran with logging disabled.

Payloads built by `as_details()` / `as_payload()` helpers are exactly where the
next collision will come from, so the guard is process-wide: a colliding key
is renamed `extra_<key>` and the record is emitted. Installed from
`app/__init__.py`, so the API, every worker and every script get it.
"""

from __future__ import annotations

import logging

_INSTALLED_FLAG = "_flowpilot_safe_make_record"


def install() -> None:
    if getattr(logging.Logger, _INSTALLED_FLAG, False):
        return
    original = logging.Logger.makeRecord
    reserved = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}

    def make_record(self, name, level, fn, lno, msg, args, exc_info, func=None, extra=None, sinfo=None):  # type: ignore[no-untyped-def]
        if extra:
            extra = {(f"extra_{k}" if k in reserved else k): v for k, v in extra.items()}
        return original(self, name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)

    logging.Logger.makeRecord = make_record  # type: ignore[method-assign]
    setattr(logging.Logger, _INSTALLED_FLAG, True)


__all__ = ["install"]
