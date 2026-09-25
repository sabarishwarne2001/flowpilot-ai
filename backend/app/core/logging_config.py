import logging
import logging.config
import sys
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


def setup_logging() -> None:
    """
    Initializes dictionary-based configuration mappings for system-wide logging.
    Configures format patterns, output handlers, and overrides third-party 
    logging behaviors to align with application-wide conventions.
    """
    log_level: str = settings.LOG_LEVEL.upper()
    
    # Supported levels list validation
    valid_levels: set[str] = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if log_level not in valid_levels:
        log_level = "INFO"

    # Define dictionary config structures
    config_dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "format": "[%(asctime)s] [%(levelname)s] [%(name)s:%(filename)s:%(lineno)d] - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
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
