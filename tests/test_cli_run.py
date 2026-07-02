"""Tests for the ``bat run`` and ``bat dry-run`` commands (CARD-016).

CLI-level, using ``click.testing.CliRunner`` end to end against small
on-disk plugin fixtures (the real ``core.*``/``lab.*`` modules from
CARD-019/020 don't exist yet -- see ``tests/test_plugins_discovery.py`` and
``tests/test_engine_executor.py`` for the established fixture pattern this
file follows).

Lower-level orchestration behavior is covered in
``tests/test_engine_runner.py``; this file focuses on CLI wiring: option
parsing, output formatting, and exit codes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from bat.cli import main

MODULE_TEMPLATE = """\
from pydantic import BaseModel
from bat.plugins.schema import ModuleSchema
from bat.artifacts.model import Artifact


class Schema(ModuleSchema):
    class Meta:
        name = {name!r}
        description = "Stub module for CARD-016 CLI tests."
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


def write_plugins(load_body: str = LOAD_BODY, filter_body: str = FILTER_BODY) -> None:
    plugins_dir = Path("plugins") / "stub"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "__init__.py").write_text("")
    (plugins_dir / "load.py").write_text(
        MODULE_TEMPLATE.format(name="stub.load", body=load_body)
    )
    (plugins_dir / "filter.py").write_text(
        MODULE_TEMPLATE.format(name="stub.filter", body=filter_body)
    )


def write_failing_plugin() -> None:
    plugins_dir = Path("plugins") / "stub"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "__init__.py").write_text("")
    (plugins_dir / "explode.py").write_text(
        MODULE_TEMPLATE.format(name="stub.explode", body=EXPLODE_BODY)
    )


def write(name: str, content: str) -> Path:
    path = Path(name)
    path.write_text(content)
    return path


@pytest.fixture(autouse=True)
def _clean_sys_state():
    """Restore ``sys.path``/``sys.modules`` after every test (see
    ``tests/test_plugins_discovery.py`` for why this is necessary: plugin
    discovery imports the local ``plugins/stub`` package by dotted name
    into the real module cache, and every test here reuses that name
    pointing at a different ``isolated_filesystem`` temp dir)."""
    path_before = list(sys.path)
    modules_before = set(sys.modules)
    yield
    sys.path[:] = path_before
    for name in list(sys.modules):
        if name not in modules_before:
            del sys.modules[name]


# --------------------------------------------------------------------------
# bat run: success
# --------------------------------------------------------------------------


def test_run_valid_protocol_completes_successfully():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", PROTOCOL)
        write_plugins()

        result = runner.invoke(main, ["run", "protocol.yaml"])

        assert result.exit_code == 0, result.output
        assert "Run complete:" in result.output
        assert "runs/" in result.output
        assert "Workflows: 1" in result.output
        assert "Steps:     2" in result.output
        assert "Artifacts: 2" in result.output
        assert "Duration:" in result.output


# --------------------------------------------------------------------------
# bat run --dry-run / bat dry-run
# --------------------------------------------------------------------------


def test_run_dry_run_prints_plan_without_invoking_modules():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", PROTOCOL)
        write_plugins(load_body=RAISE_BODY, filter_body=RAISE_BODY)

        result = runner.invoke(main, ["run", "protocol.yaml", "--dry-run"])

        assert result.exit_code == 0, result.output
        assert "Dry run: protocol.yaml" in result.output
        assert "Execution plan:" in result.output
        assert "[workflow] preprocess" in result.output
        assert "[step] load_record" in result.output
        assert "stub.load" in result.output
        assert "[step] filter_signal" in result.output
        assert "stub.filter" in result.output
        # The stub modules raise loudly if invoked -- reaching this point
        # with exit_code 0 proves they were never called.


def test_dry_run_command_equivalent_to_run_dry_run_flag():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", PROTOCOL)
        write_plugins(load_body=RAISE_BODY, filter_body=RAISE_BODY)

        run_result = runner.invoke(main, ["run", "protocol.yaml", "--dry-run"])

    # Discovery imports the local ``plugins/stub`` package under the bare
    # name "stub" into the real module cache; both invocations below reuse
    # that name pointing at two different temp directories, so the second
    # one must not see the first's cached module.
    sys.modules.pop("stub", None)
    for name in list(sys.modules):
        if name.startswith("stub."):
            del sys.modules[name]

    runner2 = CliRunner()
    with runner2.isolated_filesystem():
        write("protocol.yaml", PROTOCOL)
        write_plugins(load_body=RAISE_BODY, filter_body=RAISE_BODY)

        dry_run_result = runner2.invoke(main, ["dry-run", "protocol.yaml"])

    assert run_result.exit_code == 0
    assert dry_run_result.exit_code == 0

    def normalize(output: str) -> str:
        # Both share the same execution-plan body; only the run directory's
        # timestamp differs (unless --run-name is given) and the leading
        # "Dry run: ..." / "Run directory: ..." lines are identical anyway.
        lines = output.strip().splitlines()
        return "\n".join(line for line in lines if not line.startswith("Run directory"))

    assert normalize(run_result.output) == normalize(dry_run_result.output)


# --------------------------------------------------------------------------
# --var / --vars-file / --run-name
# --------------------------------------------------------------------------


def test_var_overrides_protocol_vars():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", PROTOCOL)
        write_plugins()

        result = runner.invoke(
            main, ["run", "protocol.yaml", "--var", "record=202", "--run-name", "varrun"]
        )

        assert result.exit_code == 0, result.output
        resolved = yaml.safe_load(
            Path("runs", "varrun", "resolved_protocol.yaml").read_text()
        )
        load_step = next(
            s
            for s in resolved["workflows"]["preprocess"]["steps"]
            if s["id"] == "load_record"
        )
        assert load_step["params"]["record"] == "202"


def test_vars_file_loads_additional_vars():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", PROTOCOL)
        write_plugins()
        Path("vars").mkdir()
        write("vars/extra.yaml", "record: '303'\n")

        result = runner.invoke(
            main,
            [
                "run",
                "protocol.yaml",
                "--vars-file",
                "vars/extra.yaml",
                "--run-name",
                "varsfilerun",
            ],
        )

        assert result.exit_code == 0, result.output
        resolved = yaml.safe_load(
            Path("runs", "varsfilerun", "resolved_protocol.yaml").read_text()
        )
        load_step = next(
            s
            for s in resolved["workflows"]["preprocess"]["steps"]
            if s["id"] == "load_record"
        )
        assert load_step["params"]["record"] == "303"


def test_run_name_creates_named_run_directory():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", PROTOCOL)
        write_plugins()

        result = runner.invoke(
            main, ["run", "protocol.yaml", "--run-name", "mitdb-baseline"]
        )

        assert result.exit_code == 0, result.output
        assert Path("runs", "mitdb-baseline").is_dir()
        assert "runs/mitdb-baseline/" in result.output


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


def test_missing_module_reference_exits_before_creating_run_dir():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", MISSING_MODULE_PROTOCOL)

        result = runner.invoke(main, ["run", "protocol.yaml"])

        assert result.exit_code == 1
        assert "nomodule.here" in result.output
        assert not Path("runs").exists()


def test_missing_module_reference_dry_run_also_exits_before_creating_run_dir():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", MISSING_MODULE_PROTOCOL)

        result = runner.invoke(main, ["run", "protocol.yaml", "--dry-run"])

        assert result.exit_code == 1
        assert "nomodule.here" in result.output
        assert not Path("runs").exists()


def test_failed_step_prints_failure_summary_and_exits_one():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", FAILING_PROTOCOL)
        write_failing_plugin()

        result = runner.invoke(main, ["run", "protocol.yaml", "--run-name", "failrun"])

        assert result.exit_code == 1
        assert "Run failed:" in result.output
        assert "Failed at step: explode_step (workflow: main)" in result.output
        assert "See logs:" in result.output
        assert "run.log" in result.output
        # The run directory itself IS created for a failed run (it's the
        # missing-module preflight check that must precede run-dir creation).
        assert Path("runs", "failrun").is_dir()


def test_nonexistent_protocol_file_exits_one():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["run", "missing.yaml"])

        assert result.exit_code == 1
