"""Run directory management for BAT.

Every ``bat run`` invocation creates a new, self-contained run directory
under ``runs/`` (relative to the protocol file being run). This module is
responsible for:

- choosing a run id (timestamp-based by default, or a caller-supplied
  ``--run-name``);
- creating the run directory layout (``logs/``, ``artifacts/``);
- setting up a run-scoped logger that writes plain text to
  ``logs/run.log``;
- writing the resolved protocol (imports inlined, vars substituted) to
  ``resolved_protocol.yaml`` before any step executes.

``provenance.yaml`` is part of the same directory layout but is written at
the *end* of a run by a later card (CARD-012) -- it is not handled here.

See ``cards/backlog/CARD-009-run-directory-management.md`` for the full
spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

# The base logger name under which all per-run child loggers are created.
# Components get child loggers named e.g. ``bat.run.<run_id>.engine`` or
# ``bat.run.<run_id>.step.load_record``; the formatter below strips the
# ``bat.run.<run_id>.`` prefix so the log line only shows the component
# name (``[engine]``, ``[step.load_record]``, ...), matching the card's
# example output.
_LOGGER_ROOT = "bat.run"


class RunError(Exception):
    """Raised when a run directory cannot be created."""


@dataclass
class RunContext:
    """Run-scoped context returned by :func:`create_run`.

    Holds every path a running protocol needs, plus a logger already
    configured to write to this run's ``logs/run.log``.
    """

    run_id: str
    run_dir: Path
    artifacts_dir: Path
    logs_dir: Path
    logger: logging.Logger


class _ComponentNameFilter(logging.Filter):
    """Rewrites ``record.name`` to just the component suffix.

    Child loggers are named ``bat.run.<run_id>.<component>`` so that each
    run gets its own, uniquely-named logger tree (avoiding handler/logger
    collisions across runs within the same process, e.g. in tests). This
    filter strips the ``bat.run.<run_id>.`` prefix before formatting so the
    log line shows just ``[<component>]`` (e.g. ``[engine]``,
    ``[workflow.preprocess]``, ``[step.load_record]``).
    """

    def __init__(self, prefix: str) -> None:
        super().__init__()
        self._prefix = prefix

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(self._prefix):
            record.name = record.name[len(self._prefix) :] or record.name
        return True


def _make_run_logger(run_id: str, logs_dir: Path) -> logging.Logger:
    """Create the top-level, run-scoped logger for ``run_id``.

    The returned logger is named ``bat.run.<run_id>`` and has a single
    ``FileHandler`` writing plain text to ``logs_dir/run.log``. Components
    should log via child loggers, e.g.
    ``logger.getChild("step.load_record")``, which propagate up to this
    handler. Because the logger (and its handler) are named/scoped per
    ``run_id``, distinct runs -- even within the same test process -- never
    share or leak handlers.
    """
    logger_name = f"{_LOGGER_ROOT}.{run_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    # Don't propagate to the root logger -- this run's log output should
    # only go to its own run.log file, not to any handlers attached
    # elsewhere (e.g. pytest's caplog root handler, or another run).
    logger.propagate = False

    # Guard against re-configuring an already-configured logger (e.g. if
    # create_run() were ever called twice for the same run_id).
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    log_path = logs_dir / "run.log"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.addFilter(_ComponentNameFilter(f"{logger_name}."))
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def _default_run_id() -> str:
    """The default, timestamp-based run id: ``YYYY-MM-DD_HHmmss`` (local time)."""
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def create_run(protocol_path: Path, run_name: str | None = None) -> RunContext:
    """Create a new run directory for ``protocol_path`` and return its context.

    Run directories are created under ``runs/`` relative to the protocol
    file's directory. If ``run_name`` is given, the run directory is
    ``runs/<run_name>/``; otherwise a timestamp-based id
    (``YYYY-MM-DD_HHmmss``, local time) is used.

    Raises :class:`RunError` if the target run directory already exists
    (named runs are never overwritten).
    """
    protocol_path = Path(protocol_path)
    run_id = run_name if run_name else _default_run_id()

    runs_root = protocol_path.parent / "runs"
    run_dir = runs_root / run_id

    if run_dir.exists():
        raise RunError(
            f"Run directory already exists: {run_dir} "
            "(choose a different --run-name, or remove the existing directory)"
        )

    artifacts_dir = run_dir / "artifacts"
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)

    logger = _make_run_logger(run_id, logs_dir)

    return RunContext(
        run_id=run_id,
        run_dir=run_dir,
        artifacts_dir=artifacts_dir,
        logs_dir=logs_dir,
        logger=logger,
    )


def write_resolved_protocol(run_ctx: RunContext, resolved: dict) -> None:
    """Write the resolved protocol dict to ``resolved_protocol.yaml``.

    Must be called after the protocol has been loaded and fully resolved
    (imports inlined, ``{{ var }}`` references substituted) but before any
    step executes -- ``resolved_protocol.yaml`` is meant to capture the
    exact protocol the engine will run.
    """
    resolved_path = run_ctx.run_dir / "resolved_protocol.yaml"
    with resolved_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(resolved, f, sort_keys=False)
