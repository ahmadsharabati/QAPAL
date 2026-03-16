"""
_log.py — Centralised logging configuration for QAPal.

All modules get their logger via:
    from _log import get_logger
    log = get_logger(__name__)

Levels:
  DEBUG   — verbose internal detail (selector resolution, token counts, raw retries)
  INFO    — normal progress output the user sees in the terminal
  WARNING — recoverable problems (self-heal triggered, stale data, retry, etc.)
  ERROR   — hard failures (test fail, unhandled exception, plan error)

Environment:
  QAPAL_LOG_LEVEL   — DEBUG | INFO | WARNING | ERROR  (default: INFO)
  QAPAL_LOG_FILE    — optional path to write a full structured log file
  QAPAL_NO_COLOR    — set to any value to disable ANSI colour codes
"""

import logging
import os
import sys

_SETUP_DONE = False
_NO_COLOR = bool(os.getenv("QAPAL_NO_COLOR", "").strip())


class _CLIFormatter(logging.Formatter):
    """
    Clean formatter for CLI output:
      INFO    → plain message (no prefix — mimics print())
      DEBUG   → dim  [DEBUG] name: message
      WARNING → yellow WARNING: message
      ERROR   → red   ERROR: message
    """
    _ANSI = {
        logging.DEBUG:    "\033[2m[DEBUG] %(name)s: %(message)s\033[0m",
        logging.INFO:     "%(message)s",
        logging.WARNING:  "\033[33mWARNING: %(message)s\033[0m",
        logging.ERROR:    "\033[31mERROR: %(message)s\033[0m",
        logging.CRITICAL: "\033[31;1mCRITICAL: %(message)s\033[0m",
    }
    _PLAIN = {
        logging.DEBUG:    "[DEBUG] %(name)s: %(message)s",
        logging.INFO:     "%(message)s",
        logging.WARNING:  "WARNING: %(message)s",
        logging.ERROR:    "ERROR: %(message)s",
        logging.CRITICAL: "CRITICAL: %(message)s",
    }

    def format(self, record: logging.LogRecord) -> str:
        table = self._PLAIN if _NO_COLOR else self._ANSI
        fmt   = table.get(record.levelno, "%(message)s")
        return logging.Formatter(fmt).format(record)


def setup_logging() -> None:
    """Initialise the qapal root logger. Safe to call multiple times."""
    global _SETUP_DONE
    if _SETUP_DONE:
        return
    _SETUP_DONE = True

    level_name = os.getenv("QAPAL_LOG_LEVEL", "INFO").upper()
    level      = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger("qapal")
    root.setLevel(level)
    root.propagate = False

    # Console handler ─ stdout so INFO lines appear alongside test output
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(_CLIFormatter())
    root.addHandler(console)

    # Optional file handler ─ always DEBUG-level, full timestamps
    log_file = os.getenv("QAPAL_LOG_FILE", "").strip()
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)-30s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        root.addHandler(fh)


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the 'qapal' namespace.

    Usage:
        log = get_logger(__name__)        # e.g. qapal.executor
        log = get_logger("crawler")       # qapal.crawler
    """
    setup_logging()
    clean = name.removeprefix("qapal.").removeprefix("_")
    return logging.getLogger(f"qapal.{clean}")
