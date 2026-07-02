"""Tests for provenance tracking (CARD-012).

Covers the acceptance criteria from
``cards/backlog/CARD-012-provenance-tracking.md``:

- ``write_provenance`` writes a valid YAML file to ``run_dir/provenance.yaml``
- Plugin versions are captured from ``importlib.metadata``
- Local plugins have ``version: null``
- ``status`` correctly round-trips ``"failed"`` / ``"partial"`` values
- SHA-256 hashes are computed for artifact files
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from bat.artifacts.model import Artifact
from bat.engine.provenance import (
    ArtifactRecord,
    RunProvenance,
    StepRecord,
    WorkflowRecord,
    build_environment_record,
    compute_input_hashes,
    write_provenance,
)
from bat.engine.run import create_run


def make_run_ctx(tmp_path):
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text("workflows: {}\n")
    return create_run(protocol_path)


def make_provenance(run_ctx, **overrides) -> RunProvenance:
    defaults = dict(
        run_id=run_ctx.run_id,
        protocol_path=Path("protocol.yaml"),
        started_at=datetime(2026, 6, 23, 15, 30, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 23, 15, 31, 4, tzinfo=timezone.utc),
        status="success",
        environment={"python_version": "3.14.0", "bat_version": "0.1.0", "plugins": []},
        workflow_records=[],
        artifact_records=[],
    )
    defaults.update(overrides)
    return RunProvenance(**defaults)


# --------------------------------------------------------------------------
# write_provenance
# --------------------------------------------------------------------------


def test_write_provenance_writes_valid_yaml_file(tmp_path):
    run_ctx = make_run_ctx(tmp_path)
    step = StepRecord(
        step_id="load_record",
        status="success",
        module="core.wfdb.read",
        module_version="0.1.0",
        started_at=datetime(2026, 6, 23, 15, 30, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 23, 15, 30, 14, tzinfo=timezone.utc),
        inputs=[],
        outputs=["raw_signal"],
        params={"path": "data/100"},
    )
    workflow = WorkflowRecord(
        workflow_id="preprocess",
        status="success",
        started_at=datetime(2026, 6, 23, 15, 30, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 23, 15, 30, 45, tzinfo=timezone.utc),
        steps=[step],
    )
    artifact_dir = run_ctx.artifacts_dir / "raw_signal"
    artifact_dir.mkdir()
    artifact = ArtifactRecord(
        name="raw_signal",
        artifact_type="signal",
        format="wfdb",
        path=artifact_dir,
        creator_step="load_record",
        creator_module="core.wfdb.read",
        timestamp=datetime(2026, 6, 23, 15, 30, 14, tzinfo=timezone.utc),
        input_hashes={},
    )
    provenance = make_provenance(
        run_ctx,
        environment={
            "python_version": "3.14.0",
            "bat_version": "0.1.0",
            "plugins": [{"name": "core", "version": "0.1.0", "source": "installed"}],
        },
        workflow_records=[workflow],
        artifact_records=[artifact],
    )

    write_provenance(run_ctx, provenance)

    provenance_path = run_ctx.run_dir / "provenance.yaml"
    assert provenance_path.is_file()

    data = yaml.safe_load(provenance_path.read_text())

    assert data["run_id"] == run_ctx.run_id
    assert data["protocol"] == "protocol.yaml"
    assert data["started_at"] == "2026-06-23T15:30:12Z"
    assert data["finished_at"] == "2026-06-23T15:31:04Z"
    assert data["status"] == "success"

    assert data["environment"]["python_version"] == "3.14.0"
    assert data["environment"]["bat_version"] == "0.1.0"
    assert data["environment"]["plugins"] == [
        {"name": "core", "version": "0.1.0", "source": "installed"}
    ]

    assert set(data["workflows"]) == {"preprocess"}
    wf_data = data["workflows"]["preprocess"]
    assert wf_data["status"] == "success"
    assert wf_data["started_at"] == "2026-06-23T15:30:12Z"
    assert wf_data["finished_at"] == "2026-06-23T15:30:45Z"
    assert set(wf_data["steps"]) == {"load_record"}
    step_data = wf_data["steps"]["load_record"]
    assert step_data["status"] == "success"
    assert step_data["module"] == "core.wfdb.read"
    assert step_data["module_version"] == "0.1.0"
    assert step_data["inputs"] == []
    assert step_data["outputs"] == ["raw_signal"]
    assert step_data["params"] == {"path": "data/100"}

    assert set(data["artifacts"]) == {"raw_signal"}
    artifact_data = data["artifacts"]["raw_signal"]
    assert artifact_data["type"] == "signal"
    assert artifact_data["format"] == "wfdb"
    assert artifact_data["path"] == "artifacts/raw_signal/"
    assert artifact_data["creator_step"] == "load_record"
    assert artifact_data["creator_module"] == "core.wfdb.read"
    assert artifact_data["input_hashes"] == {}


def test_write_provenance_overwrites_existing_file(tmp_path):
    run_ctx = make_run_ctx(tmp_path)
    write_provenance(run_ctx, make_provenance(run_ctx, status="success"))
    write_provenance(run_ctx, make_provenance(run_ctx, status="failed"))

    data = yaml.safe_load((run_ctx.run_dir / "provenance.yaml").read_text())
    assert data["status"] == "failed"


# --------------------------------------------------------------------------
# status round-tripping
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["success", "failed", "partial"])
def test_status_round_trips(tmp_path, status):
    run_ctx = make_run_ctx(tmp_path)
    provenance = make_provenance(run_ctx, status=status)

    write_provenance(run_ctx, provenance)

    data = yaml.safe_load((run_ctx.run_dir / "provenance.yaml").read_text())
    assert data["status"] == status


def test_status_failed_when_step_failed_without_handling(tmp_path):
    run_ctx = make_run_ctx(tmp_path)
    step = StepRecord(
        step_id="detect_rpeaks",
        status="failed",
        module="lab.ecg.detect_rpeaks",
        module_version="0.3.1",
        started_at=datetime(2026, 6, 23, 15, 30, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 23, 15, 30, 20, tzinfo=timezone.utc),
    )
    workflow = WorkflowRecord(
        workflow_id="preprocess",
        status="failed",
        started_at=datetime(2026, 6, 23, 15, 30, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 23, 15, 30, 20, tzinfo=timezone.utc),
        steps=[step],
    )
    provenance = make_provenance(run_ctx, status="failed", workflow_records=[workflow])

    write_provenance(run_ctx, provenance)

    data = yaml.safe_load((run_ctx.run_dir / "provenance.yaml").read_text())
    assert data["status"] == "failed"
    assert data["workflows"]["preprocess"]["steps"]["detect_rpeaks"]["status"] == "failed"


def test_status_partial_when_step_continued_after_error(tmp_path):
    run_ctx = make_run_ctx(tmp_path)
    failed_step = StepRecord(
        step_id="detect_rpeaks",
        status="failed",
        module="lab.ecg.detect_rpeaks",
        module_version="0.3.1",
        started_at=datetime(2026, 6, 23, 15, 30, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 23, 15, 30, 20, tzinfo=timezone.utc),
        outputs=["rpeaks_error"],
    )
    continued_step = StepRecord(
        step_id="compute_report",
        status="success",
        module="core.report.summarize",
        module_version="0.1.0",
        started_at=datetime(2026, 6, 23, 15, 30, 21, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 23, 15, 30, 30, tzinfo=timezone.utc),
    )
    workflow = WorkflowRecord(
        workflow_id="preprocess",
        status="partial",
        started_at=datetime(2026, 6, 23, 15, 30, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 6, 23, 15, 30, 30, tzinfo=timezone.utc),
        steps=[failed_step, continued_step],
    )
    provenance = make_provenance(run_ctx, status="partial", workflow_records=[workflow])

    write_provenance(run_ctx, provenance)

    data = yaml.safe_load((run_ctx.run_dir / "provenance.yaml").read_text())
    assert data["status"] == "partial"
    steps = data["workflows"]["preprocess"]["steps"]
    assert steps["detect_rpeaks"]["status"] == "failed"
    assert steps["compute_report"]["status"] == "success"


# --------------------------------------------------------------------------
# build_environment_record
# --------------------------------------------------------------------------


@pytest.fixture()
def no_installed_plugins(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [])


def test_environment_record_captures_python_and_bat_version(no_installed_plugins):
    record = build_environment_record({})

    assert record["python_version"] == "{}.{}.{}".format(*sys.version_info[:3])
    assert record["bat_version"] == importlib.metadata.version("batecg")
    assert record["plugins"] == []


def test_installed_plugin_version_captured_from_importlib_metadata(monkeypatch):
    entry_point = importlib.metadata.EntryPoint(
        name="lab", value="lab_ecg_plugin", group="bat.plugins"
    )
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [entry_point])
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "0.3.1" if name == "lab_ecg_plugin" else (_ for _ in ()).throw(
            importlib.metadata.PackageNotFoundError(name)
        ),
    )

    record = build_environment_record({"lab.ecg.detect_rpeaks": object()})

    plugins = {p["name"]: p for p in record["plugins"]}
    assert plugins["lab"] == {"name": "lab", "version": "0.3.1", "source": "installed"}


def test_local_plugin_has_null_version(no_installed_plugins):
    record = build_environment_record(
        {"custom_lab.rpeak_detector": object(), "custom_lab.other": object()}
    )

    plugins = {p["name"]: p for p in record["plugins"]}
    assert plugins == {
        "custom_lab": {"name": "custom_lab", "version": None, "source": "local"}
    }


def test_mixed_installed_and_local_plugins(monkeypatch):
    entry_point = importlib.metadata.EntryPoint(
        name="core", value="batecg", group="bat.plugins"
    )
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [entry_point])

    record = build_environment_record(
        {"core.wfdb.read": object(), "custom_lab.rpeak_detector": object()}
    )

    plugins = {p["name"]: p for p in record["plugins"]}
    assert plugins["core"]["source"] == "installed"
    assert plugins["core"]["version"] == importlib.metadata.version("batecg")
    assert plugins["custom_lab"] == {
        "name": "custom_lab",
        "version": None,
        "source": "local",
    }


# --------------------------------------------------------------------------
# compute_input_hashes
# --------------------------------------------------------------------------


def make_artifact(path: Path, **overrides) -> Artifact:
    defaults = dict(
        name="raw_signal",
        artifact_type="signal",
        format="wfdb",
        path=path,
        creator_module="core.wfdb.read",
        creator_step="load_record",
        creator_workflow="preprocess",
    )
    defaults.update(overrides)
    return Artifact(**defaults)


def test_compute_input_hashes_single_file(tmp_path):
    artifact_dir = tmp_path / "raw_signal"
    artifact_dir.mkdir()
    file_path = artifact_dir / "raw_signal.hea"
    file_path.write_bytes(b"header contents")

    artifact = make_artifact(artifact_dir)
    hashes = compute_input_hashes(artifact)

    expected = hashlib.sha256(b"header contents").hexdigest()
    assert hashes == {"raw_signal.hea": expected}


def test_compute_input_hashes_multi_file(tmp_path):
    artifact_dir = tmp_path / "raw_signal"
    artifact_dir.mkdir()
    (artifact_dir / "raw_signal.dat").write_bytes(b"binary signal data")
    (artifact_dir / "raw_signal.hea").write_bytes(b"header contents")

    artifact = make_artifact(artifact_dir)
    hashes = compute_input_hashes(artifact)

    assert hashes == {
        "raw_signal.dat": hashlib.sha256(b"binary signal data").hexdigest(),
        "raw_signal.hea": hashlib.sha256(b"header contents").hexdigest(),
    }


def test_compute_input_hashes_missing_path_returns_empty(tmp_path):
    artifact = make_artifact(tmp_path / "does-not-exist")
    assert compute_input_hashes(artifact) == {}


def test_compute_input_hashes_ignores_subdirectories(tmp_path):
    artifact_dir = tmp_path / "raw_signal"
    artifact_dir.mkdir()
    (artifact_dir / "raw_signal.dat").write_bytes(b"data")
    (artifact_dir / "nested").mkdir()

    artifact = make_artifact(artifact_dir)
    hashes = compute_input_hashes(artifact)

    assert hashes == {"raw_signal.dat": hashlib.sha256(b"data").hexdigest()}
