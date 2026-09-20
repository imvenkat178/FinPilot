"""Private request logging under any Uvicorn logging setup.

Each request writes one line to the ``finpilot.requests`` logger with the method, path,
status, duration and request ID. Query strings, bodies, questions, credentials and amounts
are never logged. Amounts and search text travel in JSON request bodies, so URLs carry only
identifiers, dates, counts and flags. ``tests/test_request_logging.py`` checks both.
"""
from __future__ import annotations

import logging

REQUEST_LOGGER = "finpilot.requests"
ACCESS_LOGGER = "uvicorn.access"
LINE_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


class DropQueryStrings(logging.Filter):
    """Remove query strings from access log records before any handler formats them.

    Uvicorn passes the request target, such as ``/api/forecast?days=30``, as one string
    argument. Its other arguments (client address, method, HTTP version and status) never
    contain ``?``, so cutting every string argument at ``?`` leaves them unchanged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(arg.split("?", 1)[0] if isinstance(arg, str) else arg
                                for arg in record.args)
        return True


def configure_request_logging() -> None:
    """Emit request lines and keep query strings out of the Uvicorn access log.

    Uvicorn's default logging configuration attaches handlers only to its own loggers, so
    INFO records from ``finpilot.requests`` used to be dropped. When neither the root logger
    nor the ``finpilot`` logger has a handler, attach one that writes to standard error.
    A logging configuration supplied by the operator stays in charge.
    """
    access = logging.getLogger(ACCESS_LOGGER)
    if not any(isinstance(item, DropQueryStrings) for item in access.filters):
        access.addFilter(DropQueryStrings())
    application = logging.getLogger("finpilot")
    if not application.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(LINE_FORMAT))
        application.addHandler(handler)
        if application.level == logging.NOTSET:
            application.setLevel(logging.WARNING)
    requests = logging.getLogger(REQUEST_LOGGER)
    if requests.level == logging.NOTSET:
        requests.setLevel(logging.INFO)
