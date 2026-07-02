"""Tests for `bat run` / `bat dry-run` orchestration (CARD-016).

Exercises :func:`bat.engine.runner.run_protocol` and
:func:`bat.engine.runner.dry_run_protocol` directly (lower-level than the
CLI), against small on-disk plugin fixtures built the same way
``tests/test_plugins_discovery.py`` and ``tests/test_engine_executor.py``
do, since the real ``core.*``/``lab.*`` modules (CARD-019/020+) don't exist
yet.

CLI-level behavior (output formatting, ``--var``/``--vars-file``/
``--run-name`` wiring, exit codes) is covered in ``tests/test_cli_run.py``;
this file focuses on the orchestration functions themselves.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from bat.engine.runner import (
    ExecutionPlan,
    MissingModulesError,
    RunResult,
    dry_run_protocol,
    run_protocol,
)

# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------

MODULE_TEMPLATE = """\
from pydantic import BaseModel
from bat.plugins.schema import ModuleSchema
from bat.artifacts.model import Artifact


class Schema(ModuleSchema):
    class Meta:
        name = {name!r}
        description = "Stub module for CARD-016 tests."
        citations = "none"

    class Params(BaseModel):
        pass

    class Inputs(BaseModel):
        pass

    class Outputs(BaseModel):
        pass


schema = Schema


def run(inputs, params, context=None):
{body}
"""

LOAD_BODY = """\
    path = context.artifacts_dir / "raw_signal"
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.bin").write_text("x")
    return {
        "raw_signal": Artifact(
            name="raw_signal",
            artifact_type="signal",
            format="wfdb",
            path=path,
            creator_module="stub.load",
            creator_step="load_record",
            creator_workflow="preprocess",
        )
    }
"""

FILTER_BODY = """\
    path = context.artifacts_dir / "filtered_signal"
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.bin").write_text("x")
    return {
        "filtered_signal": Artifact(
            name="filtered_signal",
            artifact_type="signal",
            format="wfdb",
            path=path,
            creator_module="stub.filter",
            creator_step="filter_signal",
            creator_workflow="preprocess",
        )
    }
"""

RAISE_BODY = """\
    raise RuntimeError("module invoked (should not happen during dry-run)")
"""

EXPLODE_BODY = """\
    raise RuntimeError("boom")
"""

PROTOCOL = """\
version: "0.1"
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load
        module: stub.load
        outputs:
          raw_signal:
            type: signal
            format: wfdb
      - id: filter_signal
        name: Filter
        module: stub.filter
        depends_on: [load_record]
        inputs:
          signal:
            artifact: raw_signal
        outputs:
          filtered_signal:
            type: signal
            format: wfdb
"""

FAILING_PROTOCOL = """\
version: "0.1"
workflows:
  main:
    steps:
      - id: explode_step
        name: Explode
        module: stub.explode
"""

MISSING_MODULE_PROTOCOL = """\
version: "0.1"
workflows:
  main:
    steps:
      - id: mystery
        name: Mystery
        module: nomodule.here
"""


def write_plugins(base: Path, load_body: str = LOAD_BODY, filter_body: str = FILTER_BODY) -> None:
    plugins_dir = base / "plugins" / "stub"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "__init__.py").write_text("")
    (plugins_dir / "load.py").write_text(
        MODULE_TEMPLATE.format(name="stub.load", body=load_body)
    )
    (plugins_dir / "filter.py").write_text(
        MODULE_TEMPLATE.format(name="stub.filter", body=filter_body)
    )


def write_failing_plugin(base: Path) -> None:
    plugins_dir = base / "plugins" / "stub"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "__init__.py").write_text("")
    (plugins_dir / "explode.py").write_text(
        MODULE_TEMPLATE.format(name="stub.explode", body=EXPLODE_BODY)
    )


@pytest.fixture(autouse=True)
def _clean_sys_state():
    """Restore ``sys.path``/``sys.modules`` after every test.

    Plugin discovery imports local plugin packages by dotted name into the
    real module cache and temporarily mutates ``sys.path``; several tests
    reuse the ``stub`` namespace pointing at different ``tmp_path``
    locations, so leftovers would make later tests import a stale module.
    """
    path_before = list(sys.path)
    modules_before = set(sys.modules)
    yield
    sys.path[:] = path_before
    for name in list(sys.modules):
        if name not in modules_before:
            del sys.modules[name]


# --------------------------------------------------------------------------
# run_protocol
# --------------------------------------------------------------------------


def test_run_protocol_succeeds_and_writes_provenance(tmp_path):
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(PROTOCOL)
    write_plugins(tmp_path)

    result = run_protocol(protocol_path, run_name="t1", vars_file=None, cli_vars={})

    assert isinstance(result, RunResult)
    assert result.status == "success"
    assert result.succeeded
    assert result.workflow_count == 1
    assert result.step_count == 2
    assert result.artifact_count == 2
    assert result.failed_step is None
    assert result.duration_seconds >= 0

    run_dir = result.run_ctx.run_dir
    assert run_dir == tmp_path / "runs" / "t1"
    assert (run_dir / "resolved_protocol.yaml").is_file()

    provenance_path = run_dir / "provenance.yaml"
    assert provenance_path.is_file()
    data = yaml.safe_load(provenance_path.read_text())
    assert data["status"] == "success"
    assert set(data["artifacts"]) == {"raw_signal", "filtered_signal"}
    assert data["workflows"]["preprocess"]["status"] == "success"
    assert set(data["workflows"]["preprocess"]["steps"]) == {
        "load_record",
        "filter_signal",
    }


def test_run_protocol_missing_module_raises_before_run_dir_created(tmp_path):
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(MISSING_MODULE_PROTOCOL)

    with pytest.raises(MissingModulesError) as exc_info:
        run_protocol(protocol_path, run_name=None, vars_file=None, cli_vars={})

    assert exc_info.value.missing_modules == ["nomodule.here"]
    assert not (tmp_path / "runs").exists()


def test_run_protocol_failed_step_reports_failure_and_writes_provenance(tmp_path):
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(FAILING_PROTOCOL)
    write_failing_plugin(tmp_path)

    result = run_protocol(protocol_path, run_name="fail1", vars_file=None, cli_vars={})

    assert result.status == "failed"
    assert not result.succeeded
    assert result.failed_step == "explode_step"
    assert result.failed_workflow == "main"

    provenance_path = result.run_ctx.run_dir / "provenance.yaml"
    assert provenance_path.is_file()
    data = yaml.safe_load(provenance_path.read_text())
    assert data["status"] == "failed"
    assert data["workflows"]["main"]["status"] == "failed"
    assert data["workflows"]["main"]["steps"]["explode_step"]["status"] == "failed"


def test_run_protocol_var_override_reaches_resolved_protocol(tmp_path):
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(
        """\
version: "0.1"
vars:
  record: "100"
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load
        module: stub.load
        params:
          record: "{{ record }}"
        outputs:
          raw_signal:
            type: signal
            format: wfdb
      - id: filter_signal
        name: Filter
        module: stub.filter
        depends_on: [load_record]
        inputs:
          signal:
            artifact: raw_signal
        outputs:
          filtered_signal:
            type: signal
            format: wfdb
"""
    )
    write_plugins(tmp_path)

    result = run_protocol(
        protocol_path, run_name="varrun", vars_file=None, cli_vars={"record": "202"}
    )

    assert result.status == "success"
    resolved = yaml.safe_load(
        (result.run_ctx.run_dir / "resolved_protocol.yaml").read_text()
    )
    load_step = next(
        s
        for s in resolved["workflows"]["preprocess"]["steps"]
        if s["id"] == "load_record"
    )
    assert load_step["params"]["record"] == "202"


# --------------------------------------------------------------------------
# dry_run_protocol
# --------------------------------------------------------------------------


def test_dry_run_protocol_builds_plan_without_invoking_modules(tmp_path):
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(PROTOCOL)
    write_plugins(tmp_path, load_body=RAISE_BODY, filter_body=RAISE_BODY)

    plan = dry_run_protocol(protocol_path, run_name="dry1", vars_file=None, cli_vars={})

    assert isinstance(plan, ExecutionPlan)
    assert plan.workflows == [
        ("preprocess", [("load_record", "stub.load"), ("filter_signal", "stub.filter")])
    ]

    run_dir = plan.run_ctx.run_dir
    assert run_dir == tmp_path / "runs" / "dry1"
    assert (run_dir / "resolved_protocol.yaml").is_file()
    assert not (run_dir / "provenance.yaml").exists()
    # No artifacts directory contents -- modules were never invoked.
    assert list(plan.run_ctx.artifacts_dir.iterdir()) == []


def test_dry_run_protocol_missing_module_raises_before_run_dir_created(tmp_path):
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(MISSING_MODULE_PROTOCOL)

    with pytest.raises(MissingModulesError):
        dry_run_protocol(protocol_path, run_name=None, vars_file=None, cli_vars={})

    assert not (tmp_path / "runs").exists()


# --------------------------------------------------------------------------
# Dependency cycles (improvement: cycle detection pre-flight)
# --------------------------------------------------------------------------

CYCLIC_STEP_PROTOCOL = """\
version: "0.1"
workflows:
  main:
    steps:
      - id: a
        name: A
        module: stub.load
        depends_on: [b]
      - id: b
        name: B
        module: stub.load
        depends_on: [a]
"""

CYCLIC_WORKFLOW_PROTOCOL = """\
version: "0.1"
workflows:
  first:
    depends_on: [second]
    steps:
      - id: s1
        name: S1
        module: stub.load
  second:
    depends_on: [first]
    steps:
      - id: s2
        name: S2
        module: stub.load
"""


def test_run_protocol_step_cycle_raises_before_run_dir_created(tmp_path):
    from bat.engine.executor import CycleError

    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(CYCLIC_STEP_PROTOCOL)
    write_plugins(tmp_path)

    with pytest.raises(CycleError):
        run_protocol(protocol_path, run_name=None, vars_file=None, cli_vars={})

    assert not (tmp_path / "runs").exists()


def test_run_protocol_workflow_cycle_raises_before_run_dir_created(tmp_path):
    from bat.engine.executor import CycleError

    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(CYCLIC_WORKFLOW_PROTOCOL)
    write_plugins(tmp_path)

    with pytest.raises(CycleError):
        run_protocol(protocol_path, run_name=None, vars_file=None, cli_vars={})

    assert not (tmp_path / "runs").exists()


def test_dry_run_protocol_cycle_raises_before_run_dir_created(tmp_path):
    from bat.engine.executor import CycleError

    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text(CYCLIC_STEP_PROTOCOL)
    write_plugins(tmp_path)

    with pytest.raises(CycleError):
        dry_run_protocol(protocol_path, run_name=None, vars_file=None, cli_vars={})

    assert not (tmp_path / "runs").exists()
