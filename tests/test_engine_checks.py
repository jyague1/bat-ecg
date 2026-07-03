"""Tests for the post-step output restriction check (CARD-013).

Covers the tests listed in
``cards/backlog/CARD-013-output-restriction-check.md``: a step whose
declared outputs are all registered and inside ``artifacts_dir`` passes;
a registered-but-outside-``artifacts_dir`` output raises
``ArtifactViolationError``; a declared-but-unregistered output raises;
a registered output whose file does not exist on disk raises. Also
verifies, at the integration level through :func:`execute_protocol`, that
the check actually runs after every successful step and that a violation
is handled exactly like any other step failure (respecting ``on_error``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bat.artifacts.model import Artifact
from bat.artifacts.registry import ArtifactRegistry
from bat.artifacts.storage import artifact_dir
from bat.engine.checks import ArtifactViolationError, check_step_outputs
from bat.engine.errors import StepExecutionError
from bat.engine.executor import execute_protocol
from bat.engine.run import create_run
from bat.engine.schema import ArtifactDeclaration, OnError, Protocol, Step, Workflow

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture
def protocol_path(tmp_path):
    path = tmp_path / "protocol.yaml"
    path.write_text("name: dummy\n")
    return path


def make_run_ctx(protocol_path, run_name="test-run"):
    return create_run(protocol_path, run_name=run_name)


def make_step(
    step_id: str = "step",
    module: str = "test.module",
    outputs: dict[str, ArtifactDeclaration] | None = None,
    depends_on: list[str] | None = None,
    on_error: OnError | None = None,
) -> Step:
    return Step(
        id=step_id,
        name=step_id,
        module=module,
        depends_on=depends_on or [],
        outputs=outputs or {},
        on_error=on_error,
    )


def out_decl(artifact_type: str = "metadata", fmt: str = "yaml") -> ArtifactDeclaration:
    return ArtifactDeclaration(type=artifact_type, format=fmt)


def make_artifact(name: str, path, **kwargs) -> Artifact:
    return Artifact(
        name=name,
        artifact_type="metadata",
        format="yaml",
        path=path,
        creator_module="test.module",
        **kwargs,
    )


def write_placeholder(path) -> None:
    """Create ``path`` as a directory containing a placeholder data file."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.bin").write_text("x")


# --------------------------------------------------------------------------
# check_step_outputs: unit-level
# --------------------------------------------------------------------------


def test_all_outputs_registered_and_inside_artifacts_dir_passes(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(outputs={"out": out_decl()})

    path = artifact_dir(run_ctx.run_dir, "out")
    write_placeholder(path)
    registry.register(make_artifact("out", path))

    # Should not raise.
    check_step_outputs(step, registry, run_ctx)


def test_output_registered_but_path_outside_artifacts_dir_raises(protocol_path, tmp_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(outputs={"out": out_decl()})

    outside_dir = tmp_path / "outside" / "out"
    write_placeholder(outside_dir)
    registry.register(make_artifact("out", outside_dir))

    with pytest.raises(ArtifactViolationError, match="out"):
        check_step_outputs(step, registry, run_ctx)


def test_output_not_registered_raises(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(outputs={"out": out_decl()})

    # Nothing registered at all.
    with pytest.raises(ArtifactViolationError, match="out"):
        check_step_outputs(step, registry, run_ctx)


def test_output_registered_but_file_missing_on_disk_raises(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(outputs={"out": out_decl()})

    # Path is inside artifacts_dir and correctly named, but nothing was
    # ever written there.
    path = artifact_dir(run_ctx.run_dir, "out")
    registry.register(make_artifact("out", path))

    with pytest.raises(ArtifactViolationError, match="out"):
        check_step_outputs(step, registry, run_ctx)


def test_output_registered_as_direct_file_path_passes(protocol_path):
    """``path`` may also point directly at a single file, not a directory."""
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(outputs={"out": out_decl()})

    path = run_ctx.artifacts_dir / "out.yaml"
    path.write_text("x")
    registry.register(make_artifact("out", path))

    check_step_outputs(step, registry, run_ctx)


def test_multiple_outputs_all_checked(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(outputs={"first": out_decl(), "second": out_decl()})

    first_path = artifact_dir(run_ctx.run_dir, "first")
    write_placeholder(first_path)
    registry.register(make_artifact("first", first_path))
    # "second" is declared but never registered.

    with pytest.raises(ArtifactViolationError, match="second"):
        check_step_outputs(step, registry, run_ctx)


def test_step_with_no_declared_outputs_passes_trivially(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(outputs={})

    check_step_outputs(step, registry, run_ctx)


# --------------------------------------------------------------------------
# check_step_outputs: wired into execute_protocol
# --------------------------------------------------------------------------


def test_execute_protocol_runs_check_after_successful_step(protocol_path):
    """A module that registers an output whose file it never wrote should
    cause the run to fail via the same StepExecutionError path as any other
    step failure, since no on_error is declared."""
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    def run(inputs, params, context=None):
        # Registers "out" pointing inside artifacts_dir, but never
        # actually writes anything there -- a bug the check should catch.
        return {
            "out": make_artifact("out", artifact_dir(run_ctx.run_dir, "out"))
        }

    step = make_step("bad_step", module="test.bad", outputs={"out": out_decl()})
    protocol = Protocol(version="0.1", workflows=[Workflow(id="main", steps=[step])])
    plugin_registry = {"test.bad": SimpleNamespace(run=run)}

    with pytest.raises(StepExecutionError) as exc_info:
        execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert isinstance(exc_info.value.__cause__, ArtifactViolationError)


def test_execute_protocol_violation_outside_artifacts_dir_stops_run(
    protocol_path, tmp_path
):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    outside_path = tmp_path / "escaped"

    def run(inputs, params, context=None):
        write_placeholder(outside_path)
        return {"out": make_artifact("out", outside_path)}

    step = make_step("escaping_step", module="test.escape", outputs={"out": out_decl()})
    protocol = Protocol(version="0.1", workflows=[Workflow(id="main", steps=[step])])
    plugin_registry = {"test.escape": SimpleNamespace(run=run)}

    with pytest.raises(StepExecutionError) as exc_info:
        execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert isinstance(exc_info.value.__cause__, ArtifactViolationError)


def test_execute_protocol_violation_respects_on_error_continue(protocol_path):
    """A step declaring on_error: continue should have its output-restriction
    violation handled exactly like a module.run() exception: an error
    artifact is produced for each on_error.output name, and execution
    continues with the next step."""
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    def run(inputs, params, context=None):
        # Declared output never actually written to disk.
        return {
            "out": make_artifact("out", artifact_dir(run_ctx.run_dir, "out"))
        }

    steps = [
        make_step(
            "bad_step",
            module="test.bad",
            outputs={"out": out_decl()},
            on_error=OnError(
                action="continue",
                output={"bad_step_error": out_decl(artifact_type="error")},
            ),
        ),
        make_step("after", module="test.after", depends_on=["bad_step"]),
    ]
    protocol = Protocol(version="0.1", workflows=[Workflow(id="main", steps=steps)])
    after_ran = []
    plugin_registry = {
        "test.bad": SimpleNamespace(run=run),
        "test.after": SimpleNamespace(
            run=lambda inputs, params, context=None: after_ran.append(True) or {}
        ),
    }

    # Should not raise: on_error=continue absorbs the ArtifactViolationError.
    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert after_ran == [True]
    assert registry.exists("bad_step_error")
    error_artifact = registry.get("bad_step_error")
    assert error_artifact.artifact_type == "error"
    assert error_artifact.creator_step == "bad_step"

    data_path = artifact_dir(run_ctx.run_dir, "bad_step_error") / "error.yaml"
    assert data_path.is_file()
