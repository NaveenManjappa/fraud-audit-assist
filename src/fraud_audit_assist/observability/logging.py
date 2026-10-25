"""Structured JSON logging with a request id bound to every line logged while serving that request."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_SENSITIVE_KEYS = frozenset({"api_key", "authorization", "password", "secret", "token", "x-api-key"})


def _redact(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    # A last line of defence: callers should never log secrets, but a stray header dump must not leak one.
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Third-party loggers log every request and query at INFO; ours already log what matters.
    for noisy in ("uvicorn.access", "httpx", "httpcore", "openai", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
