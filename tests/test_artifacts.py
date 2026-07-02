"""Tests for the artifact model, registry, and disk storage (CARD-008).

Covers the acceptance criteria from
``cards/backlog/CARD-008-artifact-model.md``:

- Registering an artifact with a duplicate name raises ``ArtifactConflictError``
- ``registry.get()`` returns the correct artifact
- ``meta.yaml`` is written correctly to disk
- Error artifacts can be registered and stored
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from bat.artifacts.model import (
    ARTIFACT_TYPES,
    DEFAULT_FORMATS,
    Artifact,
    ArtifactConflictError,
)
from bat.artifacts.registry import ArtifactNotFoundError, ArtifactRegistry
from bat.artifacts.storage import (
    META_FILENAME,
    artifact_dir,
    read_data_yaml,
    read_meta,
    write_data_yaml,
    write_meta,
)


def make_artifact(run_dir: Path, name="raw_signal", **overrides) -> Artifact:
    defaults = dict(
        name=name,
        artifact_type="signal",
        format="wfdb",
        path=artifact_dir(run_dir, name),
        creator_module="core.wfdb.read",
        creator_step="load_record",
        creator_workflow="preprocess",
        params={"path": "data/100"},
        timestamp=datetime(2026, 6, 23, 15, 30, 15, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Artifact(**defaults)


# --------------------------------------------------------------------------
# Artifact / DEFAULT_FORMATS
# --------------------------------------------------------------------------


def test_artifact_default_formats_match_card_spec():
    assert DEFAULT_FORMATS == {
        "signal": "wfdb",
        "annotations": "wfdb",
        "features": "parquet",
        "metadata": "yaml",
        "error": "yaml",
        "model": "onnx",
        "report": "html",
    }
    assert ARTIFACT_TYPES == frozenset(DEFAULT_FORMATS)


def test_artifact_captures_all_provenance_fields(tmp_path):
    run_dir = tmp_path / "runs" / "2026-06-23_153012"
    artifact = make_artifact(
        run_dir,
        metadata={"fs": 360},
        input_artifact_names=["upstream"],
        input_hashes={"upstream": "deadbeef"},
    )

    assert artifact.name == "raw_signal"
    assert artifact.artifact_type == "signal"
    assert artifact.format == "wfdb"
    assert artifact.path == artifact_dir(run_dir, "raw_signal")
    assert artifact.metadata == {"fs": 360}
    assert artifact.creator_module == "core.wfdb.read"
    assert artifact.creator_step == "load_record"
    assert artifact.creator_workflow == "preprocess"
    assert artifact.params == {"path": "data/100"}
    assert artifact.input_artifact_names == ["upstream"]
    assert artifact.input_hashes == {"upstream": "deadbeef"}
    assert artifact.timestamp == datetime(2026, 6, 23, 15, 30, 15, tzinfo=timezone.utc)


def test_artifact_path_coerced_to_path_object(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    artifact = make_artifact(run_dir, path=str(artifact_dir(run_dir, "raw_signal")))
    assert isinstance(artifact.path, Path)


def test_artifact_defaults_are_independent_between_instances(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    a = make_artifact(run_dir, name="a")
    b = make_artifact(run_dir, name="b")
    a.metadata["mutated"] = True
    assert "mutated" not in b.metadata


# --------------------------------------------------------------------------
# ArtifactRegistry
# --------------------------------------------------------------------------


def test_register_and_get_returns_correct_artifact(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    registry = ArtifactRegistry()
    artifact = make_artifact(run_dir)

    registry.register(artifact)

    assert registry.get("raw_signal") is artifact


def test_register_duplicate_name_raises_artifact_conflict_error(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    registry = ArtifactRegistry()
    registry.register(make_artifact(run_dir, name="raw_signal"))

    duplicate = make_artifact(run_dir, name="raw_signal", creator_step="other_step")

    with pytest.raises(ArtifactConflictError):
        registry.register(duplicate)

    # the original registration must survive the failed conflicting register
    assert registry.get("raw_signal").creator_step == "load_record"


def test_get_missing_artifact_raises_not_found(tmp_path):
    registry = ArtifactRegistry()
    with pytest.raises(ArtifactNotFoundError):
        registry.get("does_not_exist")


def test_exists(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    registry = ArtifactRegistry()
    assert registry.exists("raw_signal") is False
    registry.register(make_artifact(run_dir))
    assert registry.exists("raw_signal") is True


def test_all_returns_every_registered_artifact(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    registry = ArtifactRegistry()
    a = make_artifact(run_dir, name="a")
    b = make_artifact(run_dir, name="b")
    registry.register(a)
    registry.register(b)

    assert registry.all() == [a, b]


# --------------------------------------------------------------------------
# Disk storage: meta.yaml
# --------------------------------------------------------------------------


def test_write_meta_creates_artifact_directory_and_meta_yaml(tmp_path):
    run_dir = tmp_path / "runs" / "2026-06-23_153012"
    artifact = make_artifact(run_dir)

    meta_file = write_meta(run_dir, artifact)

    assert meta_file == artifact_dir(run_dir, "raw_signal") / META_FILENAME
    assert meta_file.exists()


def test_meta_yaml_matches_card_example_structure(tmp_path):
    run_dir = tmp_path / "runs" / "2026-06-23_153012"
    artifact = make_artifact(run_dir)

    write_meta(run_dir, artifact)

    with (artifact_dir(run_dir, "raw_signal") / META_FILENAME).open() as fh:
        meta = yaml.safe_load(fh)

    assert meta == {
        "name": "raw_signal",
        "type": "signal",
        "format": "wfdb",
        "path": "runs/2026-06-23_153012/artifacts/raw_signal/",
        "creator_module": "core.wfdb.read",
        "creator_step": "load_record",
        "creator_workflow": "preprocess",
        "timestamp": "2026-06-23T15:30:15Z",
        "params": {"path": "data/100"},
        "input_artifact_names": [],
        "input_hashes": {},
        "metadata": {},
    }


def test_read_meta_round_trips_write_meta(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    artifact = make_artifact(run_dir, metadata={"fs": 360})

    write_meta(run_dir, artifact)
    meta = read_meta(run_dir, "raw_signal")

    assert meta["name"] == "raw_signal"
    assert meta["metadata"] == {"fs": 360}


def test_write_meta_refuses_to_overwrite_existing_meta_yaml(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    artifact = make_artifact(run_dir)
    write_meta(run_dir, artifact)

    with pytest.raises(ArtifactConflictError):
        write_meta(run_dir, artifact)


# --------------------------------------------------------------------------
# Error artifacts
# --------------------------------------------------------------------------


def test_error_artifact_can_be_registered(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    registry = ArtifactRegistry()
    error_artifact = make_artifact(
        run_dir,
        name="load_failure",
        artifact_type="error",
        format=DEFAULT_FORMATS["error"],
        creator_step="load_record",
    )

    registry.register(error_artifact)

    assert registry.get("load_failure").artifact_type == "error"
    assert registry.get("load_failure").format == "yaml"


def test_error_artifact_stored_as_yaml_on_disk(tmp_path):
    run_dir = tmp_path / "runs" / "run1"
    error_artifact = make_artifact(
        run_dir,
        name="load_failure",
        artifact_type="error",
        format="yaml",
        creator_step="load_record",
    )

    write_meta(run_dir, error_artifact)
    error_payload = {
        "message": "FileNotFoundError: data/100.hea not found",
        "traceback": "Traceback (most recent call last): ...",
        "step_id": "load_record",
        "timestamp": "2026-06-23T15:30:15Z",
    }
    data_file = write_data_yaml(run_dir, "load_failure", error_payload)

    assert data_file.exists()
    assert data_file.parent == artifact_dir(run_dir, "load_failure")

    with (artifact_dir(run_dir, "load_failure") / META_FILENAME).open() as fh:
        meta = yaml.safe_load(fh)
    assert meta["type"] == "error"
    assert meta["format"] == "yaml"

    loaded_payload = read_data_yaml(run_dir, "load_failure")
    assert loaded_payload == error_payload


def test_error_is_a_valid_artifact_type():
    assert "error" in ARTIFACT_TYPES
    assert DEFAULT_FORMATS["error"] == "yaml"
