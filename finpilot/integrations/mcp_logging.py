"""Sanitize SDK diagnostics before any application handler can receive them.

The SDK sometimes logs validation errors containing the original MCP payload,
including through the root logger. A source-scoped record factory runs this
filter before all handlers rather than relying on logger ancestry propagation.
Application and other dependency records are left intact.
"""
import logging
from pathlib import Path

import mcp

_SDK_ROOT = Path(mcp.__file__).resolve().parent


class MCPPrivacyFilter(logging.Filter):
    def filter(self, record):
        try:
            source = Path(record.pathname).resolve().relative_to(_SDK_ROOT)
        except (ValueError, OSError):
            return True
        record.msg = f"MCP SDK diagnostic at {source.as_posix()}:{record.lineno}; provider payload omitted."
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        record.__dict__.pop("message", None)
        return True


def install_sdk_log_privacy():
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_finpilot_mcp_privacy", False):
        return
    privacy = MCPPrivacyFilter()

    def record_factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        privacy.filter(record)
        return record

    record_factory._finpilot_mcp_privacy = True
    logging.setLogRecordFactory(record_factory)


install_sdk_log_privacy()
