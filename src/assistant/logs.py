"""Structured logging setup (structlog).

One pipeline for our loggers AND third-party stdlib loggers: pretty console
by default (dev), JSON lines with ASSISTANT_LOG_JSON=true (production shape).
`structlog.contextvars` context (session_id, turn_id, backend — bound by the
WS layer) is merged into every line from every logger automatically.
"""

import logging

import structlog

from assistant.config import Settings


def configure_logging(settings: Settings) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso")
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]
    renderer = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )

    # stdlib logging (uvicorn deps, our logging.getLogger modules) -> same renderer.
    # JSON mode needs format_exc_info to turn exc_info into an "exception" string
    # (ConsoleRenderer pretty-prints tracebacks itself and must NOT get it).
    formatter_processors: list = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if settings.log_json:
        formatter_processors.append(structlog.processors.format_exc_info)
    formatter_processors.append(renderer)
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=formatter_processors,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
