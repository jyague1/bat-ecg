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
from pydantic import BaseModel

from bat.artifacts.model import Artifact
from bat.artifacts.registry import ArtifactRegistry
from bat.artifacts.storage import artifact_dir
from bat.engine.errors import StepExecutionError
from bat.engine.executor import (
    CycleError,
    ExecutorError,
    _remap_outputs_to_step_names,
    execute_protocol,
    topological_sort,
)
from bat.engine.run import create_run
from bat.engine.schema import (
    ArtifactDeclaration,
    ArtifactRef,
    OnError,
    Protocol,
    Step,
    Workflow,
)
from bat.plugins.schema import ModuleSchema, OutputField

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


def _write_placeholder(path) -> None:
    """Create ``path`` as a directory containing a placeholder data file.

    Mirrors ``bat.artifacts.storage``'s convention of an artifact's ``path``
    pointing at a directory (``artifacts/<name>/``) holding its data
    file(s). Real plugin modules write their actual payload there; these
    fake test modules only need *some* file to exist so the CARD-013
    output-restriction check (which verifies declared outputs were
    actually written inside the run's artifacts directory) passes.
    """
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.bin").write_text("x")


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
        outputs = {}
        for name in self._output_names:
            path = artifact_dir(run_dir, name) if run_dir else name
            if run_dir:
                _write_placeholder(path)
            outputs[name] = Artifact(
                name=name,
                artifact_type="metadata",
                format="yaml",
                path=path,
                creator_module="test.module",
            )
        return outputs


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
            path = artifact_dir(run_ctx.run_dir, output_name)
            _write_placeholder(path)
            return {
                output_name: Artifact(
                    name=output_name,
                    artifact_type="metadata",
                    format="yaml",
                    path=path,
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
            path = artifact_dir(run_ctx.run_dir, output_name)
            _write_placeholder(path)
            return {
                output_name: Artifact(
                    name=output_name,
                    artifact_type="metadata",
                    format="yaml",
                    path=path,
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
            path = artifact_dir(run_ctx.run_dir, output_name)
            _write_placeholder(path)
            return {
                output_name: Artifact(
                    name=output_name,
                    artifact_type="metadata",
                    format="yaml",
                    path=path,
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

    # No on_error is declared, so handle_step_error reports "stop" and the
    # executor wraps the underlying ExecutorError in StepExecutionError.
    with pytest.raises(StepExecutionError) as exc_info:
        execute_protocol(protocol, registry, {}, run_ctx)
    assert isinstance(exc_info.value.__cause__, ExecutorError)


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

    with pytest.raises(StepExecutionError) as exc_info:
        execute_protocol(protocol, registry, plugin_registry, run_ctx)
    assert isinstance(exc_info.value.__cause__, ExecutorError)

    assert not registry.exists("expected_artifact")


# --------------------------------------------------------------------------
# _remap_outputs_to_step_names (CARD-019)
# --------------------------------------------------------------------------


class _FixedOutputSchema(ModuleSchema):
    """A minimal schema whose Outputs always has a single field, "signal"
    -- mirrors core.wfdb.read: the module can only return {"signal": ...},
    regardless of what name a step gives that output."""

    class Meta:
        name = "test.fixed_output"
        description = "test module"
        citations = "none"

    class Outputs(BaseModel):
        signal: OutputField(artifact_type="signal", artifact_format="wfdb")


def _fixed_output_module():
    return SimpleNamespace(schema=_FixedOutputSchema)


def test_remap_is_noop_when_returned_keys_already_match_step_outputs():
    step = make_step("s", outputs={"signal": out_decl()})
    outputs = {"signal": Artifact(name="signal", artifact_type="signal", format="wfdb", path="p")}

    result = _remap_outputs_to_step_names(step, _fixed_output_module(), outputs)

    assert result is outputs


def test_remap_renames_single_output_positionally_when_keys_differ():
    step = make_step("s", outputs={"raw_signal": out_decl(artifact_type="signal", fmt="wfdb")})
    artifact = Artifact(name="signal", artifact_type="signal", format="wfdb", path="p")
    outputs = {"signal": artifact}

    result = _remap_outputs_to_step_names(step, _fixed_output_module(), outputs)

    # Only the dict *key* is remapped here; the artifact's own .name is
    # left untouched by this function -- _relocate_artifact (called by
    # _execute_step for every declared output, remapped or not) is what
    # authoritatively sets .name to the step-declared name afterward.
    assert set(result.keys()) == {"raw_signal"}
    assert result["raw_signal"] is artifact
    assert result["raw_signal"].name == "signal"


def test_remap_skipped_when_module_has_no_schema():
    step = make_step("s", outputs={"raw_signal": out_decl()})
    outputs = {"signal": Artifact(name="signal", artifact_type="metadata", format="yaml", path="p")}
    module = SimpleNamespace()  # no `schema` attribute at all

    result = _remap_outputs_to_step_names(step, module, outputs)

    assert result is outputs


def test_remap_skipped_when_output_counts_differ():
    # Schema declares 1 output field, but the step declares 2 -- sizes
    # don't line up, so remapping can't be done safely; left as-is (the
    # caller's own missing-outputs check will raise a clear error).
    step = make_step(
        "s", outputs={"raw_signal": out_decl(), "extra": out_decl()}
    )
    outputs = {"signal": Artifact(name="signal", artifact_type="signal", format="wfdb", path="p")}

    result = _remap_outputs_to_step_names(step, _fixed_output_module(), outputs)

    assert result is outputs


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

    with pytest.raises(StepExecutionError) as exc_info:
        execute_protocol(protocol, registry, plugin_registry, run_ctx)
    assert isinstance(exc_info.value.__cause__, RuntimeError)

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

    data_path = artifact_dir(run_ctx.run_dir, "failing_error") / "error.yaml"
    assert data_path.is_file()
    meta_path = artifact_dir(run_ctx.run_dir, "failing_error") / "meta.yaml"
    assert meta_path.is_file()


# --------------------------------------------------------------------------
# Execution records (improvement: real per-step status + timings)
# --------------------------------------------------------------------------


def test_records_capture_real_status_and_timings_on_success(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    load = RecordingModule(output_names=["raw_signal"])
    filt = RecordingModule(output_names=["filtered_signal"])
    plugin_registry = {"test.load": load, "test.filter": filt}

    steps = [
        make_step("load", module="test.load", outputs={"raw_signal": out_decl()}),
        make_step(
            "filter",
            module="test.filter",
            depends_on=["load"],
            inputs={"signal": ArtifactRef(artifact="raw_signal")},
            outputs={"filtered_signal": out_decl()},
        ),
    ]
    protocol = Protocol(version="0.1", workflows={"wf": Workflow(steps=steps)})

    from bat.engine.executor import RunRecords

    records = RunRecords()
    execute_protocol(protocol, registry, plugin_registry, run_ctx, records=records)

    assert len(records.workflows) == 1
    wf = records.workflows[0]
    assert wf.workflow_id == "wf"
    assert wf.status == "success"
    assert wf.started_at is not None and wf.finished_at is not None
    assert wf.finished_at >= wf.started_at

    assert [s.step_id for s in wf.steps] == ["load", "filter"]
    for step_outcome in wf.steps:
        assert step_outcome.status == "success"
        assert step_outcome.started_at is not None
        assert step_outcome.finished_at is not None
        assert step_outcome.finished_at >= step_outcome.started_at

    # Real per-step timing: the second step starts no earlier than the
    # first step finished (they ran sequentially, not sharing one stamp).
    assert wf.steps[1].started_at >= wf.steps[0].started_at


def test_records_mark_downstream_steps_skipped_after_unhandled_failure(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    steps = [
        make_step("boom", module="test.boom", outputs={"out": out_decl()}),
        make_step("after", module="test.after", depends_on=["boom"]),
    ]
    protocol = Protocol(version="0.1", workflows={"wf": Workflow(steps=steps)})
    plugin_registry = {
        "test.boom": failing_module(),
        "test.after": RecordingModule(output_names=[]),
    }

    from bat.engine.executor import RunRecords

    records = RunRecords()
    with pytest.raises(StepExecutionError):
        execute_protocol(protocol, registry, plugin_registry, run_ctx, records=records)

    wf = records.workflows[0]
    assert wf.status == "failed"
    by_id = {s.step_id: s for s in wf.steps}
    assert by_id["boom"].status == "failed"
    assert by_id["boom"].started_at is not None
    assert by_id["after"].status == "skipped"
    assert by_id["after"].started_at is None  # never ran


def test_records_mark_handled_failure_as_failed_step_partial_workflow(protocol_path):
    run_ctx = make_run_ctx(protocol_path)
    registry = ArtifactRegistry()

    steps = [
        make_step(
            "boom",
            module="test.boom",
            outputs={"out": out_decl()},
            on_error=OnError(
                action="continue",
                output={"boom_error": out_decl(artifact_type="error")},
            ),
        ),
        make_step("after", module="test.after", depends_on=["boom"]),
    ]
    protocol = Protocol(version="0.1", workflows={"wf": Workflow(steps=steps)})
    plugin_registry = {
        "test.boom": failing_module(),
        "test.after": RecordingModule(output_names=[]),
    }

    from bat.engine.executor import RunRecords

    records = RunRecords()
    execute_protocol(protocol, registry, plugin_registry, run_ctx, records=records)

    wf = records.workflows[0]
    assert wf.status == "partial"
    by_id = {s.step_id: s for s in wf.steps}
    assert by_id["boom"].status == "failed"      # it failed...
    assert by_id["after"].status == "success"    # ...but downstream still ran
