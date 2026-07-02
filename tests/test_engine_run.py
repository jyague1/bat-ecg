"""Tests for run directory management (CARD-009).

Covers the acceptance criteria from
``cards/backlog/CARD-009-run-directory-management.md``: run directory
creation and layout, timestamp-based and named run ids, collision errors,
writing ``resolved_protocol.yaml``, and the run-scoped log file.
"""

import re

import pytest
import yaml

from bat.engine.run import RunError, create_run, write_resolved_protocol


@pytest.fixture
def protocol_path(tmp_path):
    """A fake protocol file location -- runs/ is created next to it."""
    path = tmp_path / "protocol.yaml"
    path.write_text("name: dummy\n")
    return path


# --- create_run: directory structure ------------------------------------


def test_create_run_creates_expected_directory_structure(protocol_path):
    run_ctx = create_run(protocol_path, run_name="mitdb-baseline")

    runs_root = protocol_path.parent / "runs"
    assert run_ctx.run_dir == runs_root / "mitdb-baseline"
    assert run_ctx.run_dir.is_dir()
    assert run_ctx.artifacts_dir == run_ctx.run_dir / "artifacts"
    assert run_ctx.artifacts_dir.is_dir()
    assert run_ctx.logs_dir == run_ctx.run_dir / "logs"
    assert run_ctx.logs_dir.is_dir()
    assert (run_ctx.logs_dir / "run.log").is_file()


def test_create_run_dir_is_under_protocol_parent_runs(protocol_path):
    nested = protocol_path.parent / "nested"
    nested.mkdir()
    other_protocol = nested / "other.yaml"
    other_protocol.write_text("name: dummy\n")

    run_ctx = create_run(other_protocol, run_name="a-run")

    assert run_ctx.run_dir == nested / "runs" / "a-run"


# --- run id format -------------------------------------------------------


def test_timestamp_run_id_uses_correct_format(protocol_path):
    run_ctx = create_run(protocol_path)

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}_\d{6}", run_ctx.run_id)
    assert run_ctx.run_dir.name == run_ctx.run_id
    assert run_ctx.run_dir.parent.name == "runs"


# --- --run-name ------------------------------------------------------------


def test_run_name_creates_named_run_directory(protocol_path):
    run_ctx = create_run(protocol_path, run_name="mitdb-baseline")

    assert run_ctx.run_id == "mitdb-baseline"
    assert run_ctx.run_dir == protocol_path.parent / "runs" / "mitdb-baseline"


def test_existing_named_run_dir_raises_error(protocol_path):
    create_run(protocol_path, run_name="mitdb-baseline")

    with pytest.raises(RunError):
        create_run(protocol_path, run_name="mitdb-baseline")


def test_existing_run_dir_is_not_overwritten(protocol_path):
    run_ctx = create_run(protocol_path, run_name="mitdb-baseline")
    marker = run_ctx.artifacts_dir / "marker.txt"
    marker.write_text("keep me")

    with pytest.raises(RunError):
        create_run(protocol_path, run_name="mitdb-baseline")

    assert marker.exists()
    assert marker.read_text() == "keep me"


# --- write_resolved_protocol ----------------------------------------------


def test_write_resolved_protocol_writes_valid_yaml(protocol_path):
    run_ctx = create_run(protocol_path, run_name="mitdb-baseline")
    resolved = {
        "name": "mitdb-baseline",
        "steps": [{"id": "load", "module": "core.wfdb.read"}],
    }

    write_resolved_protocol(run_ctx, resolved)

    resolved_path = run_ctx.run_dir / "resolved_protocol.yaml"
    assert resolved_path.is_file()
    with resolved_path.open() as f:
        loaded = yaml.safe_load(f)
    assert loaded == resolved


# --- logger ----------------------------------------------------------------


def test_logger_writes_to_logs_run_log(protocol_path):
    run_ctx = create_run(protocol_path, run_name="mitdb-baseline")

    engine_logger = run_ctx.logger.getChild("engine")
    engine_logger.info("Starting run mitdb-baseline")

    for handler in run_ctx.logger.handlers:
        handler.flush()

    log_path = run_ctx.logs_dir / "run.log"
    contents = log_path.read_text()

    assert "[engine]" in contents
    assert "Starting run mitdb-baseline" in contents
    assert "INFO" in contents
    # Line should start with a YYYY-MM-DD HH:MM:SS timestamp.
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} ", contents)


def test_logger_is_plain_text_with_expected_format(protocol_path):
    run_ctx = create_run(protocol_path, run_name="mitdb-baseline")

    step_logger = run_ctx.logger.getChild("step.load_record")
    step_logger.info("Running module core.wfdb.read")

    for handler in run_ctx.logger.handlers:
        handler.flush()

    contents = run_ctx.logs_dir.joinpath("run.log").read_text()
    line = [ln for ln in contents.splitlines() if ln][-1]

    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO\s+\[step\.load_record\] "
        r"Running module core\.wfdb\.read$",
        line,
    )


def test_distinct_runs_do_not_leak_handlers(protocol_path):
    run_a = create_run(protocol_path, run_name="run-a")
    run_b = create_run(protocol_path, run_name="run-b")

    assert run_a.logger is not run_b.logger
    assert run_a.logger.name != run_b.logger.name

    run_a.logger.getChild("engine").info("only in a")
    run_b.logger.getChild("engine").info("only in b")

    for run_ctx in (run_a, run_b):
        for handler in run_ctx.logger.handlers:
            handler.flush()

    a_contents = run_a.logs_dir.joinpath("run.log").read_text()
    b_contents = run_b.logs_dir.joinpath("run.log").read_text()

    assert "only in a" in a_contents
    assert "only in b" not in a_contents
    assert "only in b" in b_contents
    assert "only in a" not in b_contents


def test_run_dir_layout_leaves_room_for_provenance(protocol_path):
    """provenance.yaml is written later (CARD-012) -- not created here,
    but nothing should prevent writing it into the run dir afterwards."""
    run_ctx = create_run(protocol_path, run_name="mitdb-baseline")

    assert not (run_ctx.run_dir / "provenance.yaml").exists()

    (run_ctx.run_dir / "provenance.yaml").write_text("run_id: mitdb-baseline\n")
    assert (run_ctx.run_dir / "provenance.yaml").is_file()
