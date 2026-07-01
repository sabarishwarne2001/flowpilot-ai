import logging.config
import sys
from app.core.config import settings

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
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": sys.stdout,
                "level": log_level,
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