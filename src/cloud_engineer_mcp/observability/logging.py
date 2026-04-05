"""Structured logging configuration using structlog."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", fmt: str = "json", log_file: str | None = None) -> None:
    """Configure structlog for cloud_engineer_mcp.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        fmt: Output format - "json" for production, "console" for development.
        log_file: Optional file path to write logs to.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.UnicodeDecoder(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(log_level)
        logging.root.addHandler(file_handler)

    logging.basicConfig(level=log_level, stream=sys.stderr, format="%(message)s")


def get_logger(component: str) -> structlog.stdlib.BoundLogger:
    """Get a logger bound with the component name."""
    return structlog.get_logger(component=component)
