"""Step-failure error handling for BAT protocol execution.

BAT's default behavior is to stop the run on the first unhandled step
failure. A step may opt out of that by declaring ``on_error: action:
continue``, in which case the failure is caught, an ``error``-typed
artifact is written and registered for every artifact name declared under
``on_error.output``, and execution continues with the next step in
topological order (including downstream steps that depend on the failed
one -- the executor's normal topological walk handles that; this module
only decides whether to stop or continue and produces the error
artifact(s)).

Workflow-level ``on_error: continue`` is a related but distinct
mechanism, implemented in :mod:`bat.engine.executor`: when a step fails
with no ``on_error`` (or ``on_error.action == "stop"``), the resulting
:class:`StepExecutionError` normally propagates out of
:func:`~bat.engine.executor.execute_protocol` and stops the whole run. If
the *workflow* containing that step has ``on_error: action: continue``,
the executor instead stops just that workflow and moves on to the next
one in workflow-level topological order.

See ``cards/backlog/CARD-011-error-handling.md`` for the full spec.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

from bat._util import format_utc
from bat.artifacts import storage
from bat.artifacts.model import Artifact
from bat.artifacts.registry import ArtifactRegistry
from bat.engine.schema import OnError, Step

__all__ = ["StepExecutionError", "handle_step_error"]

#: Filename an error artifact's YAML payload is written to, per the card's
#: "Error artifact structure" section (``artifacts/<name>/error.yaml``).
ERROR_DATA_FILENAME = "error.yaml"


class StepExecutionError(Exception):
    """Raised when a step fails and the failure is not handled.

    Raised by the executor (not this module) once :func:`handle_step_error`
    reports that execution should stop. The original exception is chained
    via ``raise ... from exc`` so the traceback and root cause are still
    visible.
    """


def _format_traceback(exc: Exception) -> str:
    """Render ``exc``'s full traceback, independent of the calling frame.

    Uses ``exc.__traceback__`` (attached automatically once ``exc`` has been
    raised) rather than ``traceback.format_exc()`` / ``sys.exc_info()``, so
    this works correctly even if :func:`handle_step_error` is ever called
    outside of the ``except`` block that caught ``exc``.
    """
    return "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )


def _build_error_payload(
    exc: Exception,
    step: Step,
    workflow_id: str,
    artifact_name: str,
) -> dict:
    """Build the error artifact's YAML payload per the card's spec."""
    return {
        "artifact_name": artifact_name,
        "step_id": step.id,
        "workflow_id": workflow_id,
        "module": step.module,
        "error_type": type(exc).__name__,
        "message": str(exc),
        "traceback": _format_traceback(exc),
        "timestamp": format_utc(datetime.now(timezone.utc)),
    }


def handle_step_error(
    exc: Exception,
    step: Step,
    workflow_id: str,
    on_error: OnError | None,
    registry: ArtifactRegistry,
    artifacts_dir: Path,
    logger: logging.Logger,
) -> bool:
    """Handle a step failure.

    Always logs the full traceback. If ``on_error`` declares
    ``action == "continue"``, writes one ``error``-typed artifact (YAML,
    ``artifacts/<name>/error.yaml``) per name in ``on_error.output``,
    registers each in ``registry``, and returns ``True`` so the caller
    continues execution. Otherwise returns ``False``, meaning the caller
    should stop.

    Returns:
        ``True`` if execution should continue, ``False`` if it should stop.
    """
    logger.error(
        "step %r failed (module=%s): %s\n%s",
        step.id,
        step.module,
        exc,
        _format_traceback(exc),
    )

    if on_error is None or on_error.action != "continue":
        return False

    run_dir = Path(artifacts_dir).parent

    for artifact_name, declaration in on_error.output.items():
        payload = _build_error_payload(exc, step, workflow_id, artifact_name)
        storage.write_data_yaml(
            run_dir, artifact_name, payload, filename=ERROR_DATA_FILENAME
        )
        artifact = Artifact(
            name=artifact_name,
            artifact_type=declaration.type,
            format=declaration.format,
            path=storage.artifact_dir(run_dir, artifact_name),
            creator_module=step.module,
            creator_step=step.id,
            creator_workflow=workflow_id,
            params=step.params,
        )
        storage.write_meta(run_dir, artifact)
        registry.register(artifact)

    return True
