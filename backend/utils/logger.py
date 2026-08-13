import logging
import logging.handlers
import os
import sys
from pathlib import Path

import structlog

from .log_sanitizer import sanitize

_default_log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR = Path(os.environ.get("LOG_DIR", str(_default_log_dir)))
LOG_DIR.mkdir(exist_ok=True)

_initialized = False


def init_logging(env: str = "dev"):
    global _initialized
    if _initialized:
        return
    _initialized = True

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        timestamper,
        sanitize,
    ]

    if env == "dev":
        structlog.configure(
            processors=shared_processors + [
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )
        formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.dev.ConsoleRenderer(colors=True),
        )
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        json_formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
        )
        file_handler = logging.handlers.TimedRotatingFileHandler(
            str(LOG_DIR / "app.jsonl"), encoding="utf-8",
            when="midnight", interval=1, backupCount=30,
        )
        file_handler.setFormatter(json_formatter)
        root = logging.getLogger()
        root.setLevel(logging.DEBUG)
        root.handlers.clear()
        root.addHandler(handler)
        root.addHandler(file_handler)

    else:
        structlog.configure(
            processors=shared_processors + [
                structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
            ],
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=True,
        )

        json_formatter = structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(),
        )

        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(json_formatter)

        file_handler = logging.handlers.TimedRotatingFileHandler(
            str(LOG_DIR / "app.jsonl"), encoding="utf-8",
            when="midnight", interval=1, backupCount=30,
        )
        file_handler.setFormatter(json_formatter)

        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.handlers.clear()
        root.addHandler(stderr_handler)
        root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # The OpenAI-compatible SDK logs complete request payloads at DEBUG,
    # including copyrighted standard text and customer document excerpts.
    logging.getLogger("openai").setLevel(logging.WARNING)
    structlog.get_logger("emc-review.logging").info(
        "logging_initialized",
        log_env=env,
        log_file=str(LOG_DIR / "app.jsonl"),
    )


def get_logger(name: str = "emc-review"):
    return structlog.get_logger(name)


def bind_trace_id(req_id: str):
    """Bind a request/trace ID to the current async context.

    All subsequent log calls in this asyncio task (and tasks created from it
    via ``asyncio.create_task``) will include ``reqId=<req_id>`` automatically.

    Typically set by ``LoggingMiddleware`` at the start of each HTTP request,
    but can also be called manually in background jobs, scripts, or tests.

    Usage:
        bind_trace_id("abc12345")
        logger.info("processing")  # includes reqId=abc12345
    """
    structlog.contextvars.bind_contextvars(reqId=req_id)


def unbind_trace_id():
    """Remove the trace ID from the current context.

    Should be called when exiting a context to avoid leaking the value into
    unrelated tasks (e.g. connection pools that reuse threads).
    """
    structlog.contextvars.unbind_contextvars("reqId")


def get_frontend_logger():
    flog = logging.getLogger("emc.frontend")
    flog.propagate = False
    if not flog.handlers:
        fh = logging.handlers.TimedRotatingFileHandler(
            str(LOG_DIR / "frontend.jsonl"), encoding="utf-8",
            when="midnight", interval=1, backupCount=30,
        )
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(message)s"))
        flog.addHandler(fh)
    return flog
