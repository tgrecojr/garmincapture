"""Logging configuration using structlog for garmincapture.

Mirrors the glucose-loader convention so the whole fleet shares one logging
posture: structured JSON in production, pretty console output in development.

Environment Variables:
    LOG_LEVEL: DEBUG, INFO, WARNING, ERROR, CRITICAL (default: INFO)
    LOG_FORMAT: json, console (default: json)

Usage:
    from garmincapture.logging_config import configure_logging, get_logger

    logger = configure_logging()
    logger.info("application_started", version="0.1.0")

    log = get_logger(__name__)
    log.warning("pull_failed", collection="sleep", error="timeout")
"""

import os
import sys

import structlog
from structlog.dev import ConsoleRenderer
from structlog.processors import JSONRenderer


def configure_logging():
    """Configure structlog with environment-based settings.

    Returns:
        Configured structlog logger instance.
    """
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("LOG_FORMAT", "json").lower()

    level_map = {
        "DEBUG": 10,
        "INFO": 20,
        "WARNING": 30,
        "ERROR": 40,
        "CRITICAL": 50,
    }
    numeric_level = level_map.get(log_level, 20)

    if log_format == "console":
        renderer = ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.plain_traceback,
        )
    else:
        renderer = JSONRenderer()

    processors = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        renderer,
    ]

    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
    )

    return structlog.get_logger()


def get_logger(name: str | None = None):
    """Get a structlog logger instance.

    Args:
        name: Optional logger name (typically ``__name__`` of calling module).

    Returns:
        Bound structlog logger.
    """
    if name:
        return structlog.get_logger(name)
    return structlog.get_logger()
