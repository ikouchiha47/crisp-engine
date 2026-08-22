"""Structured rotating logger for the Crisp Engine.

Every log line carries a trace context (session_id, project) so events
can be correlated across hooks, CLI, and store operations.

Usage:
    from lib.log import get_logger
    log = get_logger(__name__)
    log.info("episode saved", extra={"session_id": sid, "project": root})

Or use a bound logger that always includes context:
    from lib.log import bind
    log = bind(session_id="abc123", project="/path/to/project")
    log.info("SessionStart fired")

Tail the log:
    tail -f ~/.cache/crisp/crisp.log
"""

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional


# ── Log file location ──────────────────────────────────────────────────────────
_DEFAULT_LOG_DIR = Path.home() / ".cache" / "crisp"
_LOG_FILE = Path(os.environ.get("CRISP_LOG_FILE", str(_DEFAULT_LOG_DIR / "crisp.log")))
_LOG_LEVEL = os.environ.get("CRISP_LOG_LEVEL", "DEBUG").upper()

# ── Format ─────────────────────────────────────────────────────────────────────
# Each line: timestamp | level | session | project | module | message [key=val …]
_FMT = "%(asctime)s | %(levelname)-8s | %(session_id)-12s | %(project)-24s | %(name)s | %(message)s"
_DATE_FMT = "%Y-%m-%dT%H:%M:%S"

_root_configured = False


def _configure_root() -> None:
    global _root_configured
    if _root_configured:
        return
    _root_configured = True

    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        _LOG_FILE,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))

    root = logging.getLogger("crisp")
    root.setLevel(getattr(logging, _LOG_LEVEL, logging.INFO))
    root.addHandler(handler)
    root.propagate = False  # don't bubble up to the stdlib root


class _ContextAdapter(logging.LoggerAdapter):
    """Logger adapter that merges bound context into every log record."""

    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        # Merge adapter-level context; caller-supplied extra takes precedence
        for k, v in self.extra.items():
            extra.setdefault(k, v)
        # Ensure the format fields always exist
        extra.setdefault("session_id", "-")
        extra.setdefault("project", "-")
        return msg, kwargs


def get_logger(name: str) -> _ContextAdapter:
    """Return a logger bound to `name` under the crisp namespace.

    The returned adapter accepts ``extra={"session_id": ..., "project": ...}``
    on individual calls to add per-call trace context.
    """
    _configure_root()
    logger = logging.getLogger(f"crisp.{name}")
    return _ContextAdapter(logger, {"session_id": "-", "project": "-"})


def bind(
    session_id: Optional[str] = None,
    project: Optional[str] = None,
    name: str = "app",
) -> _ContextAdapter:
    """Return a logger pre-bound with session_id and project.

    All calls on the returned adapter automatically include this context
    without needing to pass extra= every time.
    """
    _configure_root()
    logger = logging.getLogger(f"crisp.{name}")
    ctx: dict = {}
    if session_id:
        ctx["session_id"] = session_id[:16]  # truncate long UUIDs
    if project:
        # Use the last two path components so logs stay readable
        p = Path(project)
        ctx["project"] = f"{p.parent.name}/{p.name}" if p.parent != p else p.name
    ctx.setdefault("session_id", "-")
    ctx.setdefault("project", "-")
    return _ContextAdapter(logger, ctx)


def log_path() -> Path:
    """Return the path of the active log file."""
    return _LOG_FILE
