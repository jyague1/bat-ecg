"""Tests for the ``core.wfdb.write`` module (CARD-020).

Covers the acceptance criteria from
``cards/backlog/CARD-020-core-wfdb-write.md``: writing a signal artifact
produces WFDB files at the specified path, the output artifact's metadata
matches the input signal's metadata, a missing input signal raises a
descriptive error, the module's ``citations: none`` passes discovery's
interface enforcement, and the module is discoverable as
``core.wfdb.write`` via the plugin system.

Also covers an end-to-end chain of ``core.wfdb.read`` -> ``core.wfdb.write``
through the real DAG executor (mirroring ``core.wfdb.read``'s own
end-to-end test in ``tests/test_core_wfdb_read.py``), proving the two
modules genuinely interoperate: a synthetic WFDB record is read into a
``raw_signal`` artifact by step 1, then re-exported to disk by step 2 as
``exported_signal``, with both artifacts ending up correctly registered and
real WFDB files present on disk under ``artifacts/``.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest
import wfdb

from bat.artifacts.registry import ArtifactRegistry
from bat.core.wfdb import read as wfdb_read
from bat.core.wfdb import write as wfdb_write
from bat.engine.executor import execute_protocol
from bat.engine.run import create_run
from bat.engine.schema import ArtifactDeclaration, ArtifactRef, Protocol, Step, Workflow
from bat.plugins.discovery import discover_plugins
from bat.plugins.interface import BATContext

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

N_CHANNELS = 2
N_SAMPLES = 1000
FS = 360


@pytest.fixture
def wfdb_record_path(tmp_path) -> str:
    """Write a small synthetic 2-channel WFDB record and return its path
    (without extension)."""
    record_dir = tmp_path / "records"
    record_dir.mkdir()
    rng = np.random.default_rng(7)
    signal = rng.standard_normal((N_SAMPLES, N_CHANNELS)).astype(float)
    wfdb.wrsamp(
        record_name="mitdb100",
        fs=FS,
        units=["mV", "mV"],
        sig_name=["MLII", "V5"],
        p_signal=signal,
        fmt=["16", "16"],
        write_dir=str(record_dir),
    )
    return str(record_dir / "mitdb100")


@pytest.fixture
def context(tmp_path) -> BATContext:
    run_dir = tmp_path / "run"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    return BATContext(run_dir=run_dir, artifacts_dir=artifacts_dir, logger=logging.getLogger("test"))


@pytest.fixture
def signal_artifact(wfdb_record_path, context):
    """A signal artifact as produced by ``core.wfdb.read``, ready to be
    consumed by ``core.wfdb.write``."""
    outputs = wfdb_read.run({}, {"path": wfdb_record_path}, context)
    return outputs["signal"]


# --------------------------------------------------------------------------
# run(): basic write
# --------------------------------------------------------------------------


def test_run_writes_wfdb_files_at_specified_path(signal_artifact, context):
    dest = context.artifacts_dir / "exported"
    outputs = wfdb_write.run({"signal": signal_artifact}, {"path": str(dest)}, context)

    assert set(outputs.keys()) == {"exported_signal"}
    artifact = outputs["exported_signal"]
    assert artifact.artifact_type == "signal"
    assert artifact.format == "wfdb"
    assert artifact.path == dest
    assert dest.is_dir()

    written_files = list(dest.iterdir())
    assert any(f.suffix == ".hea" for f in written_files)
    assert any(f.suffix == ".dat" for f in written_files)

    # And it round-trips back through wfdb correctly.
    reread = wfdb.rdrecord(str(dest / "exported_signal"))
    assert reread.n_sig == N_CHANNELS
    assert reread.fs == FS
    assert reread.sig_len == N_SAMPLES


def test_run_relative_path_resolved_from_run_dir(signal_artifact, context):
    outputs = wfdb_write.run(
        {"signal": signal_artifact}, {"path": "artifacts/exported_rel"}, context
    )
    artifact = outputs["exported_signal"]

    expected = context.run_dir / "artifacts" / "exported_rel"
    assert artifact.path == expected
    assert expected.is_dir()
    assert any(expected.iterdir())


# --------------------------------------------------------------------------
# Metadata preservation
# --------------------------------------------------------------------------


def test_output_metadata_matches_input_signal_metadata(signal_artifact, context):
    dest = context.artifacts_dir / "exported"
    outputs = wfdb_write.run({"signal": signal_artifact}, {"path": str(dest)}, context)
    artifact = outputs["exported_signal"]

    assert artifact.metadata == signal_artifact.metadata


# --------------------------------------------------------------------------
# Missing input
# --------------------------------------------------------------------------


def test_run_missing_input_signal_raises_descriptive_error(context):
    dest = context.artifacts_dir / "exported"
    with pytest.raises(ValueError) as exc_info:
        wfdb_write.run({}, {"path": str(dest)}, context)
    assert "core.wfdb.write" in str(exc_info.value)
    assert "signal" in str(exc_info.value)


# --------------------------------------------------------------------------
# Output-outside-artifacts-dir: warns, does not block
# --------------------------------------------------------------------------


def test_run_path_outside_artifacts_dir_logs_warning_but_still_writes(
    signal_artifact, context, tmp_path, caplog
):
    outside_dest = tmp_path / "outside" / "exported"
    with caplog.at_level(logging.WARNING, logger="test"):
        outputs = wfdb_write.run(
            {"signal": signal_artifact}, {"path": str(outside_dest)}, context
        )

    artifact = outputs["exported_signal"]
    assert artifact.path == outside_dest
    assert outside_dest.is_dir()
    assert any(outside_dest.iterdir())
    assert any("outside" in record.message for record in caplog.records)


# --------------------------------------------------------------------------
# Schema / discovery
# --------------------------------------------------------------------------


def test_schema_declares_no_citations():
    assert wfdb_write.schema.Meta.citations == "none"


def test_schema_name_matches_dotted_module_name():
    assert wfdb_write.schema.Meta.name == "core.wfdb.write"


def test_module_discoverable_as_core_wfdb_write():
    registry = discover_plugins(None)
    assert "core.wfdb.write" in registry
    assert registry["core.wfdb.write"] is wfdb_write


# --------------------------------------------------------------------------
# End-to-end: core.wfdb.read -> core.wfdb.write chained through the executor
# --------------------------------------------------------------------------


def test_end_to_end_read_then_write_chain(wfdb_record_path, tmp_path):
    """A two-step protocol: step 1 (``core.wfdb.read``) loads a synthetic
    WFDB record into ``raw_signal``; step 2 (``core.wfdb.write``) consumes
    ``raw_signal`` and re-exports it to disk as ``exported_signal``. Runs
    through the real executor with a real plugin registry and RunContext,
    confirming both modules interoperate end-to-end."""
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text("version: '0.1'\n")

    read_step = Step(
        id="load_record",
        name="load_record",
        module="core.wfdb.read",
        params={"path": wfdb_record_path},
        outputs={"raw_signal": ArtifactDeclaration(type="signal", format="wfdb")},
    )
    write_step = Step(
        id="export_record",
        name="export_record",
        module="core.wfdb.write",
        params={"path": "artifacts/exported"},
        inputs={"signal": ArtifactRef(artifact="raw_signal")},
        outputs={"exported_signal": ArtifactDeclaration(type="signal", format="wfdb")},
        depends_on=["load_record"],
    )
    protocol = Protocol(
        version="0.1", workflows=[Workflow(id="main", steps=[read_step, write_step])]
    )

    plugin_registry = discover_plugins(None)
    registry = ArtifactRegistry()
    run_ctx = create_run(protocol_path, run_name="e2e-read-write-test-run")

    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    assert registry.exists("raw_signal")
    assert registry.exists("exported_signal")

    raw_artifact = registry.get("raw_signal")
    exported_artifact = registry.get("exported_signal")

    assert exported_artifact.name == "exported_signal"
    assert exported_artifact.artifact_type == "signal"
    assert exported_artifact.format == "wfdb"
    assert exported_artifact.creator_module == "core.wfdb.write"
    assert exported_artifact.metadata == raw_artifact.metadata

    assert exported_artifact.path.is_relative_to(run_ctx.artifacts_dir)
    written_files = list(exported_artifact.path.iterdir())
    assert any(f.suffix == ".hea" for f in written_files)
    assert any(f.suffix == ".dat" for f in written_files)

    reread = wfdb.rdrecord(str(exported_artifact.path / "exported_signal"))
    assert reread.n_sig == N_CHANNELS
    assert reread.fs == FS
    assert reread.sig_len == N_SAMPLES
