"""Tests for the DAG execution engine (CARD-010).

Covers the acceptance criteria from
``cards/backlog/CARD-010-dag-execution-engine.md``: topological sort of a
linear chain and a branching DAG (with YAML order as tiebreaker), cycle
detection at both the workflow and step level, step input resolution from
the ``ArtifactRegistry`` before ``module.run()`` is invoked, and
post-step validation of declared outputs.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bat.artifacts.model import Artifact
from bat.artifacts.registry import ArtifactRegistry
from bat.artifacts.storage import artifact_dir
from bat.engine.executor import CycleError, ExecutorError, execute_protocol, topological_sort
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
    params: dict[str, Any] | None = None,
) -> Step:
    return Step(
        id=step_id,
        name=step_id,
        module=module,
        depends_on=depends_on or [],
        inputs=inputs or {},
        params=params or {},
        outputs=outputs or {},
        on_error=on_error,
    )


def out_decl(artifact_type: str = "metadata", fmt: str = "yaml") -> ArtifactDeclaration:
    return ArtifactDeclaration(type=artifact_type, format=fmt)


class RecordingModule:
    """A fake plugin module recording every call it received."""

    def __init__(self, output_names: list[str] | None = None, run_fn=None):
        self.calls: list[tuple[dict, dict, Any]] = []
        self._output_names = output_names or []
        self._run_fn = run_fn

    def run(self, inputs, params, context=None):
        self.calls.append((inputs, params, context))
        if self._run_fn is not None:
            return self._run_fn(inputs, params, context)
        run_dir = context.run_dir if context is not None else None
        return {
            name: Artifact(
                name=name,
                artifact_type="metadata",
                format="yaml",
                path=artifact_dir(run_dir, name) if run_dir else name,
                creator_module="test.module",
            )
            for name in self._output_names
        }


def failing_module():
    def run(inputs, params, context=None):
        raise RuntimeError("boom")

    return SimpleNamespace(run=run)


# --------------------------------------------------------------------------
# topological_sort
# --------------------------------------------------------------------------


def test_topological_sort_linear_chain():
    nodes = ["a", "b", "c"]
    depends_on = {"a": [], "b": ["a"], "c": ["b"]}
    assert topological_sort(nodes, depends_on) == ["a", "b", "c"]


def test_topological_sort_branching_dag_uses_yaml_order_tiebreaker():
    # load -> {filter_ecg, filter_eeg} -> merge
    # filter_ecg declared before filter_eeg, so it must run first.
    nodes = ["load", "filter_ecg", "filter_eeg", "merge"]
    depends_on = {
        "load": [],
        "filter_ecg": ["load"],
        "filter_eeg": ["load"],
        "merge": ["filter_ecg", "filter_eeg"],
    }
    assert topological_sort(nodes, depends_on) == [
        "load",
        "filter_ecg",
        "filter_eeg",
        "merge",
    ]


def test_topological_sort_tiebreaker_respects_declaration_order_when_reversed():
    # Two independent roots; declared in this order, neither depends on
    # the other -- declaration order alone decides.
    nodes = ["second", "first"]
    depends_on = {"second": [], "first": []}
    assert topological_sort(nodes, depends_on) == ["second", "first"]


def test_topological_sort_cycle_raises_error():
    nodes = ["a", "b", "c"]
    depends_on = {"a": ["c"], "b": ["a"], "c": ["b"]}
    with pytest.raises(CycleError):
        topological_sort(nodes, depends_on)


def test_topological_sort_self_cycle_raises_error():
    nodes = ["a"]
    depends_on = {"a": ["a"]}
    with pytest.raises(CycleError):
        topological_sort(nodes, depends_on)


# --------------------------------------------------------------------------
# execute_protocol: ordering
# --------------------------------------------------------------------------


def test_execute_protocol_linear_chain_executes_in_order(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    execution_log: list[str] = []

    def make_recorder(step_id, output_name):
        def run(inputs, params, context=None):
            execution_log.append(step_id)
            return {
                output_name: Artifact(
                    name=output_name,
                    artifact_type="metadata",
                    format="yaml",
                    path=artifact_dir(run_ctx.run_dir, output_name),
                    creator_module="test.module",
                    creator_step=step_id,
                )
            }

        return SimpleNamespace(run=run)

    steps = [
        make_step("load_record", module="test.load", outputs={"raw_signal": out_decl()}),
        make_step(
            "filter_signal",
            module="test.filter",
            depends_on=["load_record"],
            inputs={"signal": ArtifactRef(artifact="raw_signal")},
            outputs={"filtered_signal": out_decl()},
        ),
        make_step(
            "detect_rpeaks",
            module="test.detect",
            depends_on=["filter_signal"],
            inputs={"signal": ArtifactRef(artifact="filtered_signal")},
            outputs={"rpeaks": out_decl()},
        ),
    ]
    protocol = Protocol(
        version="0.1",
        workflows={"main": Workflow(steps=steps)},
    )
    plugin_registry = {
        "test.load": make_recorder("load_record", "raw_signal"),
        "test.filter": make_recorder("filter_signal", "filtered_signal"),
        "test.detect": make_recorder("detect_rpeaks", "rpeaks"),
    }

    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert execution_log == ["load_record", "filter_signal", "detect_rpeaks"]
    assert registry.exists("raw_signal")
    assert registry.exists("filtered_signal")
    assert registry.exists("rpeaks")


def test_execute_protocol_branching_dag_uses_yaml_order_tiebreaker(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    execution_log: list[str] = []

    def make_recorder(step_id, output_name):
        def run(inputs, params, context=None):
            execution_log.append(step_id)
            return {
                output_name: Artifact(
                    name=output_name,
                    artifact_type="metadata",
                    format="yaml",
                    path=artifact_dir(run_ctx.run_dir, output_name),
                    creator_module="test.module",
                    creator_step=step_id,
                )
            }

        return SimpleNamespace(run=run)

    steps = [
        make_step("load_record", module="test.load", outputs={"raw_signal": out_decl()}),
        make_step(
            "filter_ecg",
            module="test.filter_ecg",
            depends_on=["load_record"],
            inputs={"signal": ArtifactRef(artifact="raw_signal")},
            outputs={"ecg_filtered": out_decl()},
        ),
        make_step(
            "filter_eeg",
            module="test.filter_eeg",
            depends_on=["load_record"],
            inputs={"signal": ArtifactRef(artifact="raw_signal")},
            outputs={"eeg_filtered": out_decl()},
        ),
        make_step(
            "merge",
            module="test.merge",
            depends_on=["filter_ecg", "filter_eeg"],
            inputs={
                "ecg": ArtifactRef(artifact="ecg_filtered"),
                "eeg": ArtifactRef(artifact="eeg_filtered"),
            },
            outputs={"merged": out_decl()},
        ),
    ]
    protocol = Protocol(version="0.1", workflows={"main": Workflow(steps=steps)})
    plugin_registry = {
        "test.load": make_recorder("load_record", "raw_signal"),
        "test.filter_ecg": make_recorder("filter_ecg", "ecg_filtered"),
        "test.filter_eeg": make_recorder("filter_eeg", "eeg_filtered"),
        "test.merge": make_recorder("merge", "merged"),
    }

    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert execution_log == ["load_record", "filter_ecg", "filter_eeg", "merge"]


def test_execute_protocol_workflow_level_ordering(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()
    execution_log: list[str] = []

    def make_recorder(step_id, output_name):
        def run(inputs, params, context=None):
            execution_log.append(step_id)
            return {
                output_name: Artifact(
                    name=output_name,
                    artifact_type="metadata",
                    format="yaml",
                    path=artifact_dir(run_ctx.run_dir, output_name),
                    creator_module="test.module",
                    creator_step=step_id,
                )
            }

        return SimpleNamespace(run=run)

    protocol = Protocol(
        version="0.1",
        workflows={
            "report": Workflow(
                depends_on=["features"],
                steps=[
                    make_step(
                        "make_report",
                        module="test.report",
                        inputs={"feat": ArtifactRef(artifact="features_out")},
                        outputs={"report_out": out_decl()},
                    )
                ],
            ),
            "preprocess": Workflow(
                steps=[
                    make_step(
                        "load", module="test.load", outputs={"raw": out_decl()}
                    )
                ],
            ),
            "features": Workflow(
                depends_on=["preprocess"],
                steps=[
                    make_step(
                        "extract",
                        module="test.extract",
                        inputs={"raw": ArtifactRef(artifact="raw")},
                        outputs={"features_out": out_decl()},
                    )
                ],
            ),
        },
    )
    plugin_registry = {
        "test.load": make_recorder("load", "raw"),
        "test.extract": make_recorder("extract", "features_out"),
        "test.report": make_recorder("make_report", "report_out"),
    }

    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert execution_log == ["load", "extract", "make_report"]


# --------------------------------------------------------------------------
# execute_protocol: cycles
# --------------------------------------------------------------------------


def test_execute_protocol_step_cycle_raises_error(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    # Both step ids exist within the workflow (so Protocol's own
    # depends_on validators are satisfied) but reference each other,
    # forming a cycle that only topological_sort itself can detect.
    steps = [
        make_step("a", module="test.a", depends_on=["b"]),
        make_step("b", module="test.b", depends_on=["a"]),
    ]
    protocol = Protocol(version="0.1", workflows={"main": Workflow(steps=steps)})

    with pytest.raises(CycleError):
        execute_protocol(protocol, registry, {}, run_ctx)


def test_execute_protocol_workflow_cycle_raises_error(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    protocol = Protocol(
        version="0.1",
        workflows={
            "a": Workflow(depends_on=["b"], steps=[make_step("step_a", module="test.a")]),
            "b": Workflow(depends_on=["a"], steps=[make_step("step_b", module="test.b")]),
        },
    )

    with pytest.raises(CycleError):
        execute_protocol(protocol, registry, {}, run_ctx)


# --------------------------------------------------------------------------
# execute_protocol: input resolution
# --------------------------------------------------------------------------


def test_step_inputs_resolved_from_registry_before_execution(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    load_step = make_step(
        "load_record", module="test.load", outputs={"raw_signal": out_decl()}
    )
    load_module = RecordingModule(output_names=["raw_signal"])

    filter_module = RecordingModule(output_names=["filtered_signal"])
    filter_step = make_step(
        "filter_signal",
        module="test.filter",
        depends_on=["load_record"],
        inputs={"signal": ArtifactRef(artifact="raw_signal")},
        outputs={"filtered_signal": out_decl()},
    )
    protocol = Protocol(
        version="0.1", workflows={"main": Workflow(steps=[load_step, filter_step])}
    )
    plugin_registry = {"test.load": load_module, "test.filter": filter_module}

    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    # The artifact registered from load_record's output must be exactly
    # what filter_signal received as its "signal" input -- proving the
    # input was resolved from the registry (not passed through directly).
    raw_signal_artifact = registry.get("raw_signal")
    assert len(filter_module.calls) == 1
    received_inputs, received_params, context = filter_module.calls[0]
    assert received_inputs == {"signal": raw_signal_artifact}
    assert context.run_dir == run_ctx.run_dir
    assert context.artifacts_dir == run_ctx.artifacts_dir


def test_missing_module_in_plugin_registry_raises_clear_error(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    step = make_step("orphan", module="does.not.exist", outputs={"out": out_decl()})
    protocol = Protocol(version="0.1", workflows={"main": Workflow(steps=[step])})

    with pytest.raises(ExecutorError):
        execute_protocol(protocol, registry, {}, run_ctx)


# --------------------------------------------------------------------------
# execute_protocol: output validation
# --------------------------------------------------------------------------


def test_step_missing_declared_output_raises_validation_error(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    # Module returns nothing, but the step declares an output.
    module = RecordingModule(output_names=[])
    step = make_step(
        "no_outputs", module="test.no_outputs", outputs={"expected_artifact": out_decl()}
    )
    protocol = Protocol(version="0.1", workflows={"main": Workflow(steps=[step])})
    plugin_registry = {"test.no_outputs": module}

    with pytest.raises(ExecutorError):
        execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert not registry.exists("expected_artifact")


# --------------------------------------------------------------------------
# execute_protocol: error handling (on_error)
# --------------------------------------------------------------------------


def test_step_with_no_on_error_reraises_and_stops_run(protocol_path):
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

    with pytest.raises(RuntimeError):
        execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert ran_second == []


def test_step_with_on_error_continue_produces_error_artifact_and_continues(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    steps = [
        make_step(
            "failing",
            module="test.failing",
            on_error=OnError(action="continue", output={"failing_error": out_decl(artifact_type="error")}),
        ),
        make_step("downstream", module="test.downstream", depends_on=["failing"]),
    ]
    protocol = Protocol(version="0.1", workflows={"main": Workflow(steps=steps)})
    downstream_ran = []
    plugin_registry = {
        "test.failing": failing_module(),
        "test.downstream": SimpleNamespace(
            run=lambda inputs, params, context=None: downstream_ran.append(True) or {}
        ),
    }

    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert downstream_ran == [True]
    assert registry.exists("failing_error")
    error_artifact = registry.get("failing_error")
    assert error_artifact.artifact_type == "error"
    assert error_artifact.creator_step == "failing"

    data_path = artifact_dir(run_ctx.run_dir, "failing_error") / "data.yaml"
    assert data_path.is_file()
    meta_path = artifact_dir(run_ctx.run_dir, "failing_error") / "meta.yaml"
    assert meta_path.is_file()
