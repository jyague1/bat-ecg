"""Tests for step/workflow error handling (CARD-011).

Covers the tests listed in ``cards/backlog/CARD-011-error-handling.md``:
default stop-on-failure via ``StepExecutionError``, ``on_error: continue``
producing a registered error artifact with the full YAML payload, downstream
steps of a failed-but-continued step still running, and workflow-level
``on_error: continue`` stopping just that workflow while the run continues.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from bat.artifacts.registry import ArtifactRegistry
from bat.artifacts.storage import artifact_dir
from bat.engine.errors import StepExecutionError, handle_step_error
from bat.engine.executor import execute_protocol
from bat.engine.run import create_run
from bat.engine.schema import (
    ArtifactDeclaration,
    ArtifactRef,
    OnError,
    Protocol,
    Step,
    Workflow,
)

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
    step_id: str,
    module: str = "test.echo",
    depends_on: list[str] | None = None,
    inputs: dict[str, ArtifactRef] | None = None,
    outputs: dict[str, ArtifactDeclaration] | None = None,
    on_error: OnError | None = None,
) -> Step:
    return Step(
        id=step_id,
        name=step_id,
        module=module,
        depends_on=depends_on or [],
        inputs=inputs or {},
        params={},
        outputs=outputs or {},
        on_error=on_error,
    )


def out_decl(artifact_type: str = "metadata", fmt: str = "yaml") -> ArtifactDeclaration:
    return ArtifactDeclaration(type=artifact_type, format=fmt)


def failing_module(exc: Exception | None = None):
    def run(inputs, params, context=None):
        raise exc if exc is not None else RuntimeError("boom")

    return SimpleNamespace(run=run)


# --------------------------------------------------------------------------
# handle_step_error: unit-level
# --------------------------------------------------------------------------


def test_handle_step_error_returns_false_with_no_on_error(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step("load_record", module="core.wfdb.read")

    exc = FileNotFoundError("No such file or directory: 'data/100'")
    result = handle_step_error(
        exc,
        step,
        workflow_id="preprocess",
        on_error=None,
        registry=registry,
        artifacts_dir=run_ctx.artifacts_dir,
        logger=run_ctx.logger,
    )

    assert result is False
    assert registry.all() == []


def test_handle_step_error_on_error_continue_produces_error_artifact(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(
        "load_record",
        module="core.wfdb.read",
        on_error=OnError(
            action="continue",
            output={"load_failure": out_decl(artifact_type="error")},
        ),
    )

    exc = FileNotFoundError("No such file or directory: 'data/100'")
    result = handle_step_error(
        exc,
        step,
        workflow_id="preprocess",
        on_error=step.on_error,
        registry=registry,
        artifacts_dir=run_ctx.artifacts_dir,
        logger=run_ctx.logger,
    )

    assert result is True

    data_path = artifact_dir(run_ctx.run_dir, "load_failure") / "error.yaml"
    assert data_path.is_file()
    meta_path = artifact_dir(run_ctx.run_dir, "load_failure") / "meta.yaml"
    assert meta_path.is_file()


def test_handle_step_error_registers_error_artifact_in_registry(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(
        "load_record",
        module="core.wfdb.read",
        on_error=OnError(
            action="continue",
            output={"load_failure": out_decl(artifact_type="error")},
        ),
    )

    exc = FileNotFoundError("No such file or directory: 'data/100'")
    handle_step_error(
        exc,
        step,
        workflow_id="preprocess",
        on_error=step.on_error,
        registry=registry,
        artifacts_dir=run_ctx.artifacts_dir,
        logger=run_ctx.logger,
    )

    assert registry.exists("load_failure")
    artifact = registry.get("load_failure")
    assert artifact.artifact_type == "error"
    assert artifact.format == "yaml"
    assert artifact.creator_step == "load_record"
    assert artifact.creator_workflow == "preprocess"
    assert artifact.creator_module == "core.wfdb.read"


def test_error_yaml_contains_correct_fields(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step(
        "load_record",
        module="core.wfdb.read",
        on_error=OnError(
            action="continue",
            output={"load_failure": out_decl(artifact_type="error")},
        ),
    )

    try:
        raise FileNotFoundError("No such file or directory: 'data/100'")
    except FileNotFoundError as exc:
        handle_step_error(
            exc,
            step,
            workflow_id="preprocess",
            on_error=step.on_error,
            registry=registry,
            artifacts_dir=run_ctx.artifacts_dir,
            logger=run_ctx.logger,
        )

    data_path = artifact_dir(run_ctx.run_dir, "load_failure") / "error.yaml"
    payload = yaml.safe_load(data_path.read_text())

    assert payload["artifact_name"] == "load_failure"
    assert payload["step_id"] == "load_record"
    assert payload["workflow_id"] == "preprocess"
    assert payload["module"] == "core.wfdb.read"
    assert payload["error_type"] == "FileNotFoundError"
    assert payload["message"] == "No such file or directory: 'data/100'"
    assert "Traceback (most recent call last)" in payload["traceback"]
    # Timestamp is UTC "YYYY-MM-DDTHH:MM:SSZ".
    assert payload["timestamp"].endswith("Z")
    assert "T" in payload["timestamp"]


def test_handle_step_error_logs_full_traceback(protocol_path, tmp_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    step = make_step("load_record", module="core.wfdb.read")

    try:
        raise ValueError("kaboom")
    except ValueError as exc:
        handle_step_error(
            exc,
            step,
            workflow_id="preprocess",
            on_error=None,
            registry=registry,
            artifacts_dir=run_ctx.artifacts_dir,
            logger=run_ctx.logger,
        )

    for handler in run_ctx.logger.handlers:
        handler.flush()
    log_text = (run_ctx.logs_dir / "run.log").read_text()
    assert "kaboom" in log_text
    assert "Traceback (most recent call last)" in log_text


# --------------------------------------------------------------------------
# execute_protocol: step-level on_error, wired through the executor
# --------------------------------------------------------------------------


def test_step_failure_no_on_error_raises_step_execution_error_and_stops_run(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    steps = [
        make_step("failing", module="test.failing"),
        make_step("never_runs", module="test.never", depends_on=["failing"]),
    ]
    protocol = Protocol(version="0.1", workflows={"main": Workflow(steps=steps)})
    ran_second = []
    plugin_registry = {
        "test.failing": failing_module(),
        "test.never": SimpleNamespace(
            run=lambda inputs, params, context=None: ran_second.append(True)
        ),
    }

    with pytest.raises(StepExecutionError) as exc_info:
        execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert ran_second == []


def test_step_failure_on_error_continue_produces_artifact_and_continues(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    steps = [
        make_step(
            "failing",
            module="test.failing",
            on_error=OnError(
                action="continue",
                output={"load_failure": out_decl(artifact_type="error")},
            ),
        ),
        make_step("after", module="test.after"),
    ]
    protocol = Protocol(version="0.1", workflows={"main": Workflow(steps=steps)})
    after_ran = []
    plugin_registry = {
        "test.failing": failing_module(),
        "test.after": SimpleNamespace(
            run=lambda inputs, params, context=None: after_ran.append(True) or {}
        ),
    }

    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert after_ran == [True]
    assert registry.exists("load_failure")


def test_downstream_steps_of_failed_continued_step_still_execute(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    steps = [
        make_step(
            "load_record",
            module="test.load",
            outputs={"raw_signal": out_decl()},
            on_error=OnError(
                action="continue",
                output={"load_failure": out_decl(artifact_type="error")},
            ),
        ),
        make_step(
            "downstream",
            module="test.downstream",
            depends_on=["load_record"],
        ),
    ]
    protocol = Protocol(version="0.1", workflows={"main": Workflow(steps=steps)})
    downstream_ran = []
    plugin_registry = {
        "test.load": failing_module(FileNotFoundError("no such file")),
        "test.downstream": SimpleNamespace(
            run=lambda inputs, params, context=None: downstream_ran.append(True) or {}
        ),
    }

    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    # The downstream step ran even though load_record failed -- it is the
    # protocol author's responsibility to have wired it not to strictly
    # require `raw_signal` (this step declares no `inputs`, matching the
    # card's guidance that downstream steps still run on continue).
    assert downstream_ran == [True]
    assert registry.exists("load_failure")
    assert not registry.exists("raw_signal")


# --------------------------------------------------------------------------
# execute_protocol: workflow-level on_error
# --------------------------------------------------------------------------


def test_workflow_level_on_error_continue_stops_workflow_but_continues_run(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    protocol = Protocol(
        version="0.1",
        workflows={
            "preprocess": Workflow(
                on_error=OnError(action="continue"),
                steps=[
                    make_step("failing", module="test.failing"),
                    make_step(
                        "never_runs_in_workflow",
                        module="test.never",
                        depends_on=["failing"],
                    ),
                ],
            ),
            "report": Workflow(
                depends_on=["preprocess"],
                steps=[make_step("make_report", module="test.report")],
            ),
        },
    )
    ran = []
    plugin_registry = {
        "test.failing": failing_module(),
        "test.never": SimpleNamespace(
            run=lambda inputs, params, context=None: ran.append("never_runs_in_workflow")
        ),
        "test.report": SimpleNamespace(
            run=lambda inputs, params, context=None: ran.append("make_report") or {}
        ),
    }

    # Should not raise: the failing workflow's own on_error=continue absorbs
    # the StepExecutionError, and the run proceeds to the next workflow.
    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert "never_runs_in_workflow" not in ran
    assert ran == ["make_report"]


def test_workflow_level_default_stop_propagates_step_execution_error(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    protocol = Protocol(
        version="0.1",
        workflows={
            "preprocess": Workflow(
                steps=[make_step("failing", module="test.failing")],
            ),
            "report": Workflow(
                depends_on=["preprocess"],
                steps=[make_step("make_report", module="test.report")],
            ),
        },
    )
    ran = []
    plugin_registry = {
        "test.failing": failing_module(),
        "test.report": SimpleNamespace(
            run=lambda inputs, params, context=None: ran.append("make_report") or {}
        ),
    }

    with pytest.raises(StepExecutionError):
        execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert ran == []
