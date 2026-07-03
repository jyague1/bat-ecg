"""Post-step output restriction check for BAT protocol execution.

By convention, plugin modules should only write artifact files inside the
current run's ``artifacts/`` directory. Nothing in v1 enforces this at the
OS/filesystem level -- modules run in-process, with no sandboxing -- so this
module implements a best-effort, post-hoc check instead: after a step
executes successfully, :func:`check_step_outputs` verifies that every
artifact the step declared in its ``outputs`` was actually registered by the
module and actually exists on disk inside ``run_ctx.artifacts_dir``.

This catches the common mistake of a module writing to the wrong path. It is
explicitly not a security boundary (true enforcement would require
subprocess/sandbox isolation, deferred to a future version), and it does not
scan the filesystem for undeclared files written outside the run directory --
only the artifacts a step *declared* are checked.

See ``cards/backlog/CARD-013-output-restriction-check.md`` for the full spec.
"""

from __future__ import annotations

from pathlib import Path

from bat.artifacts.registry import ArtifactRegistry
from bat.engine.run import RunContext
from bat.engine.schema import Step

__all__ = ["ArtifactViolationError", "check_step_outputs"]


class ArtifactViolationError(Exception):
    """Raised when a declared artifact is missing or outside the run directory."""


def _has_any_file(path: Path) -> bool:
    """Return whether ``path`` denotes at least one real file on disk.

    Artifacts conventionally live at a *directory*
    (``artifacts/<name>/``, per :mod:`bat.artifacts.storage`) containing the
    artifact's data file(s) plus ``meta.yaml``, so the common case here is
    "does this directory contain at least one regular file". A module could
    in principle also point ``path`` directly at a single file, so that case
    is handled too.
    """
    if path.is_dir():
        return any(child.is_file() for child in path.rglob("*"))
    return path.is_file()


def check_step_outputs(
    step: Step,
    registry: ArtifactRegistry,
    run_ctx: RunContext,
) -> None:
    """Post-step check: verify all of ``step``'s declared outputs are legit.

    Called by the execution engine after a step's ``module.run()`` has
    returned successfully and its outputs have been registered in
    ``registry`` -- never when the step raised an exception (in that case,
    error handling takes over instead; see :mod:`bat.engine.errors`).

    For every artifact name declared in ``step.outputs``, checks, in order:

    1. The artifact was registered in ``registry`` by the module.
    2. The artifact's ``path`` resolves to somewhere inside
       ``run_ctx.artifacts_dir`` (not escaping it via ``..`` or an absolute
       path elsewhere on disk).
    3. At least one real file exists at that path (see :func:`_has_any_file`).

    Raises:
        ArtifactViolationError: On the first failing check, naming the
            artifact and which check failed.
    """
    artifacts_dir = run_ctx.artifacts_dir.resolve()

    for artifact_name in step.outputs.values():
        if not registry.exists(artifact_name):
            raise ArtifactViolationError(
                f"step {step.id!r} declared output {artifact_name!r} but no "
                "artifact with that name was registered"
            )

        artifact = registry.get(artifact_name)
        resolved_path = Path(artifact.path).resolve()

        if not resolved_path.is_relative_to(artifacts_dir):
            raise ArtifactViolationError(
                f"step {step.id!r} output {artifact_name!r} has path "
                f"{artifact.path} which is outside the run's artifacts "
                f"directory ({run_ctx.artifacts_dir})"
            )

        if not _has_any_file(resolved_path):
            raise ArtifactViolationError(
                f"step {step.id!r} output {artifact_name!r} declares path "
                f"{artifact.path} but no file exists there"
            )
