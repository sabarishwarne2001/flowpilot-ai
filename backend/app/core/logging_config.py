import json
import logging
import logging.config
import re
import sys
from typing import Any, Optional
from app.core.config import settings


class RedactSecretPaths(logging.Filter):
    """ARCH46-S1:log-redaction. On the console handler, so EVERY record written
    (uvicorn.access included, whose args carry the request path) has a token in
    a public path -- a calendar-feed or document-request token -- replaced by
    [redacted] before it is formatted. Never drops a record."""

    _ATTRS = ("path", "url", "raw_path", "route")

    def filter(self, record: logging.LogRecord) -> bool:
        from app.core.public_route_registry import redact_path

        try:
            if isinstance(record.msg, str):
                record.msg = redact_path(record.msg)
            if isinstance(record.args, tuple):
                record.args = tuple(redact_path(a) for a in record.args)
            elif isinstance(record.args, dict):
                record.args = {k: redact_path(a) for k, a in record.args.items()}
            for attr in self._ATTRS:
                value = getattr(record, attr, None)
                if isinstance(value, str):
                    setattr(record, attr, redact_path(value))
        except Exception:  # noqa: BLE001 - a filter must never lose a log line
            pass
        return True


#: Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message", "asctime", "color_message", "taskName",
}
_SECRET_KEY = re.compile(r"pass(word)?|secret|token|api[_-]?key|authorization|cookie|credential|private[_-]?key", re.I)
_MAX_VALUE_CHARS = 500


def _context_of(record: logging.LogRecord) -> dict[str, Any]:
    """The record's `extra=` fields, secrets masked, long values cut, public-path tokens redacted."""
    from app.core.public_route_registry import redact_path

    out: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_ATTRS or key.startswith("_"):
            continue
        if _SECRET_KEY.search(key):
            out[key] = "[redacted]"
            continue
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            try:
                value = json.dumps(value, default=str, sort_keys=True)
            except Exception:  # noqa: BLE001 - a formatter must never lose a log line
                value = repr(value)
        if isinstance(value, str):
            value = redact_path(value)
            if len(value) > _MAX_VALUE_CHARS:
                value = value[:_MAX_VALUE_CHARS] + "…"
        out[key] = value
    return out


class ContextFormatter(logging.Formatter):
    """FINAL RELEASE: log lines carry their context.

    Every call site logs an event name plus `extra=` fields (work item, organization, the error...),
    and the old format printed only the event name: "enrich.llm_unavailable", with no error and no
    document. Text mode appends the fields as `key=value`; `LOG_FORMAT=json` writes one JSON object
    per line for a log pipeline (Twelve-Factor: logs are an event stream). Field names that look like
    secrets are masked, whatever their value.
    """

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None, json_output: bool = False) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self.json_output = json_output

    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802 - logging API
        base = super().formatMessage(record)
        context = _context_of(record)
        if not context:
            return base
        rendered = " ".join(
            f"{key}={json.dumps(value) if isinstance(value, str) and (' ' in value or not value) else value}"
            for key, value in context.items()
        )
        return f"{base} | {rendered}"

    def format(self, record: logging.LogRecord) -> str:
        if not self.json_output:
            return super().format(record)
        payload: dict[str, Any] = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_context_of(record),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _formatter_config(fmt: str, datefmt: Optional[str]) -> dict[str, Any]:
    json_output = str(getattr(settings, "LOG_FORMAT", "text") or "text").strip().lower() == "json"
    return {"()": ContextFormatter, "fmt": fmt, "datefmt": datefmt, "json_output": json_output}


def setup_logging(level: Optional[str] = None, fmt: Optional[str] = None) -> None:
    """
    Initializes dictionary-based configuration mappings for system-wide logging.
    Configures format patterns, output handlers, and overrides third-party
    logging behaviors to align with application-wide conventions.

    The worker calls it with its own level and line format, so both processes
    share the context formatter and the secret-path redaction filter.
    """
    log_level: str = (level or settings.LOG_LEVEL).upper()
    
    # Supported levels list validation
    valid_levels: set[str] = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if log_level not in valid_levels:
        log_level = "INFO"

    # Define dictionary config structures
    config_dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": _formatter_config(
                fmt or "[%(asctime)s] [%(levelname)s] [%(name)s:%(filename)s:%(lineno)d] - %(message)s",
                None if fmt else "%Y-%m-%d %H:%M:%S",
            ),
        },
        "filters": {
            "redact_secret_paths": {"()": RedactSecretPaths},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": sys.stdout,
                "level": log_level,
                "filters": ["redact_secret_paths"],
            },
        },
        "loggers": {
            "": {  # Root logger mapping
                "handlers": ["console"],
                "level": log_level,
                "propagate": True,
            },
            "uvicorn": {
                "handlers": ["console"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["console"],
                "level": log_level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["console"],
                "level": log_level,
                "propagate": False,
            },
        },
    }

    logging.config.dictConfig(config_dict)
