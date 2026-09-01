"""
Logging.

Separate from trace.py, which measures how long things took. This records
what happened and what went wrong -- including stack traces, which until now
were printed as a one-line 'ERROR: ...' and lost.

Console stays quiet by default so it does not compete with the
conversation; the file keeps everything.
"""

import logging
import logging.handlers
import os
import sys

from app.config import LOG_DIR

LOG_FILE = LOG_DIR / "edith.log"

MAX_BYTES = 2_000_000
BACKUP_COUNT = 3

CONSOLE_HANDLER = "edith-console"

_configured = False


class _ConsoleFormatter(logging.Formatter):
    """Short and unobtrusive: this shares a terminal with the conversation."""

    def format(self, record):
        message = record.getMessage()

        if record.levelno >= logging.ERROR:
            return f"[{record.levelname.lower()}] {message}"

        return f"[{record.levelname.lower()}] {message}"


def setup(console_level: str | None = None) -> logging.Logger:
    """
    Configure logging once. Safe to call repeatedly.

    EDITH_LOG_LEVEL controls the file (default INFO).
    EDITH_CONSOLE_LOG_LEVEL controls the terminal (default ERROR).
    """
    global _configured

    root = logging.getLogger("edith")

    if _configured:
        return root

    file_level = os.environ.get("EDITH_LOG_LEVEL", "INFO").upper()
    term_level = (
        console_level
        or os.environ.get("EDITH_CONSOLE_LOG_LEVEL", "ERROR")
    ).upper()

    root.setLevel(logging.DEBUG)
    root.propagate = False

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(getattr(logging, file_level, logging.INFO))
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)-28s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

        root.addHandler(file_handler)

    except OSError:
        # Never let logging failures stop the assistant working.
        pass

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, term_level, logging.ERROR))
    console.setFormatter(_ConsoleFormatter())
    # Named so tests can find it among handlers other tools attach.
    console.set_name(CONSOLE_HANDLER)

    root.addHandler(console)

    _configured = True

    return root


def get_logger(name: str) -> logging.Logger:
    """A child logger. Call setup() once at startup first."""
    return logging.getLogger(f"edith.{name}")
