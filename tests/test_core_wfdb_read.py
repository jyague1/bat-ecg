"""Tests for the ``core.wfdb.read`` module (CARD-019).

Covers the acceptance criteria from
``cards/backlog/CARD-019-core-wfdb-read.md``: loading a valid WFDB record
returns a correctly-populated signal artifact, the ``channel_names`` param
filters channels, a missing file raises a descriptive error, the module's
``citations: none`` passes discovery's interface enforcement, and the
module is discoverable as ``core.wfdb.read`` via the plugin system.

Also covers the executor-level fix (CARD-019's "known architectural
issue"): a step can declare an output name (e.g. ``raw_signal``) that
differs from the module's own fixed schema output field name (``signal``);
:func:`bat.engine.executor._remap_outputs_to_step_names` must remap the
returned artifact to the step-declared name. That is exercised end-to-end
here with a real ``Protocol``/``Workflow``/``Step``, a real plugin registry
(via :func:`discover_plugins`), and a real ``RunContext``.
"""

from __future__ import annotations

import numpy as np
import pytest
import wfdb

from bat.artifacts.registry import ArtifactRegistry
from bat.core.wfdb import read as wfdb_read
from bat.engine.executor import execute_protocol
from bat.engine.run import create_run
from bat.engine.schema import ArtifactDeclaration, Protocol, Step, Workflow
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
    (without extension), as ``core.wfdb.read``'s ``path`` param expects."""
    record_dir = tmp_path / "records"
    record_dir.mkdir()
    rng = np.random.default_rng(42)
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
    import logging

    return BATContext(run_dir=run_dir, artifacts_dir=artifacts_dir, logger=logging.getLogger("test"))


# --------------------------------------------------------------------------
# run(): basic read
# --------------------------------------------------------------------------


def test_run_loads_record_and_returns_signal_artifact_with_correct_metadata(
    wfdb_record_path, context
):
    outputs = wfdb_read.run({}, {"path": wfdb_record_path}, context)

    assert set(outputs.keys()) == {"signal"}
    artifact = outputs["signal"]
    assert artifact.artifact_type == "signal"
    assert artifact.format == "wfdb"
    assert artifact.metadata["n_channels"] == N_CHANNELS
    assert artifact.metadata["fs"] == FS
    assert artifact.metadata["channel_names"] == ["MLII", "V5"]
    assert artifact.metadata["units"] == ["mV", "mV"]
    assert artifact.metadata["n_samples"] == N_SAMPLES


def test_run_writes_artifact_data_to_artifacts_dir(wfdb_record_path, context):
    outputs = wfdb_read.run({}, {"path": wfdb_record_path}, context)
    artifact = outputs["signal"]

    assert artifact.path.is_dir()
    assert artifact.path.is_relative_to(context.artifacts_dir)
    written_files = list(artifact.path.iterdir())
    assert any(f.suffix == ".hea" for f in written_files)
    assert any(f.suffix == ".dat" for f in written_files)

    # And it round-trips back through wfdb correctly.
    reread = wfdb.rdrecord(str(artifact.path / "signal"))
    assert reread.n_sig == N_CHANNELS
    assert reread.fs == FS
    assert reread.sig_len == N_SAMPLES


def test_run_channel_names_param_filters_channels(wfdb_record_path, context):
    outputs = wfdb_read.run(
        {}, {"path": wfdb_record_path, "channel_names": ["V5"]}, context
    )
    artifact = outputs["signal"]

    assert artifact.metadata["n_channels"] == 1
    assert artifact.metadata["channel_names"] == ["V5"]


def test_run_missing_file_raises_descriptive_error(context):
    missing_path = str(context.run_dir / "does" / "not" / "exist")
    with pytest.raises(FileNotFoundError) as exc_info:
        wfdb_read.run({}, {"path": missing_path}, context)
    assert "core.wfdb.read" in str(exc_info.value)
    assert missing_path in str(exc_info.value)


# --------------------------------------------------------------------------
# Schema / discovery
# --------------------------------------------------------------------------


def test_schema_declares_no_citations():
    assert wfdb_read.schema.Meta.citations == "none"


def test_schema_name_matches_dotted_module_name():
    assert wfdb_read.schema.Meta.name == "core.wfdb.read"


def test_module_discoverable_as_core_wfdb_read():
    registry = discover_plugins(None)
    assert "core.wfdb.read" in registry
    assert registry["core.wfdb.read"] is wfdb_read


# --------------------------------------------------------------------------
# End-to-end: executor output-name remap (the CARD-019 architectural fix)
# --------------------------------------------------------------------------


def test_end_to_end_step_declared_output_name_differs_from_schema_field(
    wfdb_record_path, tmp_path
):
    """A step declares ``raw_signal`` as its output, but the module's own
    schema always returns a dict keyed ``"signal"``. The executor must
    remap the returned artifact to the step-declared name (``raw_signal``)
    -- this is the exact scenario the CARD-019 executor fix targets."""
    protocol_path = tmp_path / "protocol.yaml"
    protocol_path.write_text("version: '0.1'\n")

    step = Step(
        id="load_record",
        name="load_record",
        module="core.wfdb.read",
        params={"path": wfdb_record_path},
        outputs={"raw_signal": ArtifactDeclaration(type="signal", format="wfdb")},
    )
    protocol = Protocol(version="0.1", workflows={"main": Workflow(steps=[step])})

    plugin_registry = discover_plugins(None)
    registry = ArtifactRegistry()
    run_ctx = create_run(protocol_path, run_name="e2e-test-run")

    execute_protocol(protocol, registry, plugin_registry, run_ctx)

    # Registered under the step-declared name, not the module's schema
    # output field name.
    assert registry.exists("raw_signal")
    assert not registry.exists("signal")

    artifact = registry.get("raw_signal")
    assert artifact.name == "raw_signal"
    assert artifact.artifact_type == "signal"
    assert artifact.format == "wfdb"
    assert artifact.metadata["n_channels"] == N_CHANNELS
    assert artifact.metadata["fs"] == FS
    assert artifact.metadata["n_samples"] == N_SAMPLES
    assert artifact.creator_module == "core.wfdb.read"

    # The on-disk data (written under the module's own "signal" dir name)
    # is still inside the run's artifacts directory, and check_step_outputs
    # (which ran as part of execute_protocol) already confirmed a real file
    # exists at artifact.path -- reconfirm here too.
    assert artifact.path.is_relative_to(run_ctx.artifacts_dir)
    assert any(artifact.path.iterdir())
