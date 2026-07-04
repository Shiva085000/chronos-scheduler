"""Structured logging setup.

JSON logs in production (machine-parseable, one event per line), pretty
console logs when debug=true. Context (request_id, worker_id, job_id) is
attached via structlog contextvars so every log line within a request or
job execution carries correlation ids.
"""

import logging
import sys

import structlog


def configure_logging(*, debug: bool = False, service: str = "api") -> None:
    renderer = (
        structlog.dev.ConsoleRenderer()
        if debug
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if debug else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)

    logging.basicConfig(level=logging.WARNING, stream=sys.stdout)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
