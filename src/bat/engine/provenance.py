"""Run provenance for BAT.

At the end of every run (success, failure, or partial completion) BAT
writes a ``provenance.yaml`` manifest to the run directory, recording:

- the run's identity, timing, and overall status;
- the *environment* the run executed in (Python version, the installed
  ``batecg`` version, and the version/source of every plugin namespace
  that was used);
- a per-workflow, per-step breakdown of status and timing; and
- every artifact produced, with its type/format/path, the step and module
  that created it, and best-effort SHA-256 hashes of its input artifacts'
  files.

This module owns the data model for that manifest (:class:`RunProvenance`
and its nested :class:`WorkflowRecord` / :class:`StepRecord` /
:class:`ArtifactRecord`) plus the serialization (:func:`write_provenance`)
and environment-capture (:func:`build_environment_record`) logic.

Populating a :class:`RunProvenance` from an actual protocol run (deciding
per-step/per-workflow status, and calling :func:`write_provenance` at the
right point in :func:`bat.engine.executor.execute_protocol`) is the job of
a later card (CARD-016) -- this module only defines the shape and knows
how to serialize it.

See ``cards/backlog/CARD-012-provenance-tracking.md`` for the full spec.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from bat._util import format_utc
from bat.artifacts.model import Artifact
from bat.engine.run import RunContext

__all__ = [
    "StepRecord",
    "WorkflowRecord",
    "ArtifactRecord",
    "RunProvenance",
    "write_provenance",
    "build_environment_record",
    "build_artifact_record",
    "compute_input_hashes",
]

#: Best-effort ceiling on how large a file we'll hash. Files larger than
#: this are recorded as ``None`` rather than spending a long time (and a
#: lot of I/O) hashing them.
_MAX_HASHABLE_BYTES = 500 * 1024 * 1024  # 500 MiB

#: Chunk size used when streaming a file through SHA-256.
_HASH_CHUNK_BYTES = 1024 * 1024


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class StepRecord:
    """Provenance for a single executed step."""

    step_id: str
    status: str  # "success", "failed", "skipped", ...
    module: str
    module_version: str | None
    started_at: datetime
    finished_at: datetime | None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRecord:
    """Provenance for a single executed workflow (a sequence of steps)."""

    workflow_id: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    steps: list[StepRecord] = field(default_factory=list)


@dataclass
class ArtifactRecord:
    """Provenance for a single produced artifact.

    Mirrors the subset of :class:`bat.artifacts.model.Artifact` that
    belongs in ``provenance.yaml``. Built from an :class:`Artifact` via
    :func:`build_artifact_record` (or :meth:`from_artifact`), rather than
    reusing :class:`Artifact` directly, so that provenance's on-disk shape
    doesn't have to be dictated by the in-memory artifact model.
    """

    name: str
    artifact_type: str
    format: str
    path: Path
    creator_step: str
    creator_module: str
    timestamp: datetime
    input_hashes: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_artifact(
        cls, artifact: Artifact, input_hashes: dict[str, Any] | None = None
    ) -> ArtifactRecord:
        """Build an :class:`ArtifactRecord` from a CARD-008 :class:`Artifact`.

        ``input_hashes`` defaults to ``artifact.input_hashes`` (whatever
        the artifact already carries); callers may pass an explicitly
        computed mapping instead (e.g. built via :func:`compute_input_hashes`
        over the artifact's declared input artifacts).
        """
        return cls(
            name=artifact.name,
            artifact_type=artifact.artifact_type,
            format=artifact.format,
            path=Path(artifact.path),
            creator_step=artifact.creator_step,
            creator_module=artifact.creator_module,
            timestamp=artifact.timestamp,
            input_hashes=dict(
                input_hashes if input_hashes is not None else artifact.input_hashes
            ),
        )


@dataclass
class RunProvenance:
    """The full provenance record for a single run.

    Serialized to ``provenance.yaml`` by :func:`write_provenance`.
    """

    run_id: str
    protocol_path: Path
    started_at: datetime
    finished_at: datetime | None
    status: str  # "success", "failed", "partial"
    environment: dict
    workflow_records: list[WorkflowRecord] = field(default_factory=list)
    artifact_records: list[ArtifactRecord] = field(default_factory=list)


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    """Render ``dt`` as a UTC ``YYYY-MM-DDTHH:MM:SSZ`` string, or ``None``."""
    if dt is None:
        return None
    return format_utc(dt)


def _step_to_dict(step: StepRecord) -> dict:
    return {
        "status": step.status,
        "module": step.module,
        "module_version": step.module_version,
        "started_at": _iso(step.started_at),
        "finished_at": _iso(step.finished_at),
        "inputs": list(step.inputs),
        "outputs": list(step.outputs),
        "params": dict(step.params),
    }


def _workflow_to_dict(workflow: WorkflowRecord) -> dict:
    return {
        "status": workflow.status,
        "started_at": _iso(workflow.started_at),
        "finished_at": _iso(workflow.finished_at),
        "steps": {step.step_id: _step_to_dict(step) for step in workflow.steps},
    }


def _artifact_path_str(path: Path, run_dir: Path) -> str:
    """Render an artifact's path relative to the run directory, as the card's
    example shows (``artifacts/raw_signal/``), falling back to the absolute
    path if it isn't actually under ``run_dir`` (e.g. in isolated unit tests
    that don't go through the full run-directory layout).
    """
    try:
        rel = path.relative_to(run_dir)
        return f"{rel.as_posix()}/"
    except ValueError:
        return f"{path.as_posix()}/"


def _artifact_to_dict(artifact: ArtifactRecord, run_dir: Path) -> dict:
    return {
        "type": artifact.artifact_type,
        "format": artifact.format,
        "path": _artifact_path_str(Path(artifact.path), run_dir),
        "creator_step": artifact.creator_step,
        "creator_module": artifact.creator_module,
        "timestamp": _iso(artifact.timestamp),
        "input_hashes": artifact.input_hashes,
    }


def _provenance_to_dict(run_ctx: RunContext, provenance: RunProvenance) -> dict:
    return {
        "run_id": provenance.run_id,
        "protocol": Path(provenance.protocol_path).name,
        "started_at": _iso(provenance.started_at),
        "finished_at": _iso(provenance.finished_at),
        "status": provenance.status,
        "environment": provenance.environment,
        "workflows": {
            wf.workflow_id: _workflow_to_dict(wf) for wf in provenance.workflow_records
        },
        "artifacts": {
            a.name: _artifact_to_dict(a, run_ctx.run_dir) for a in provenance.artifact_records
        },
    }


def write_provenance(run_ctx: RunContext, provenance: RunProvenance) -> None:
    """Write ``provenance.yaml`` to ``run_ctx.run_dir``.

    Overwrites any existing file at that path -- callers (CARD-016) write
    this once, at the very end of a run, regardless of whether the run
    succeeded, failed, or partially completed.
    """
    data = _provenance_to_dict(run_ctx, provenance)
    provenance_path = run_ctx.run_dir / "provenance.yaml"
    with provenance_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


# --------------------------------------------------------------------------
# Environment capture
# --------------------------------------------------------------------------


def _entry_point_version(entry_point: importlib.metadata.EntryPoint) -> str | None:
    """Best-effort version lookup for an installed plugin's entry point.

    Prefers ``entry_point.dist`` (the :class:`~importlib.metadata.Distribution`
    the entry point was actually loaded from, when discovered via the real
    ``importlib.metadata.entry_points()``); falls back to looking up the
    top-level package name from the entry point's ``value`` (useful when an
    ``EntryPoint`` was constructed directly, e.g. in tests, and has no
    ``dist`` attached).
    """
    dist = entry_point.dist
    if dist is not None:
        try:
            return dist.version
        except Exception:
            pass

    top_level = entry_point.value.split(":", 1)[0].split(".", 1)[0]
    try:
        return importlib.metadata.version(top_level)
    except importlib.metadata.PackageNotFoundError:
        return None


def build_environment_record(plugin_registry: dict) -> dict:
    """Capture the run's environment: Python version, ``bat`` version, and
    every plugin namespace's version/source.

    ``plugin_registry`` is the flat dotted-name -> module dict returned by
    :func:`bat.plugins.discovery.discover_plugins`. Each *top-level*
    namespace present in the registry (e.g. ``"core"`` for
    ``"core.wfdb.read"``) is reported once: if the namespace matches the
    name of an installed ``bat.plugins`` entry point (re-queried here via
    :func:`importlib.metadata.entry_points`, independent of how discovery
    happened to import it), it's "installed" and its version is looked up
    via :mod:`importlib.metadata`; otherwise it's "local" with
    ``version: null``.
    """
    python_version = "{}.{}.{}".format(*sys.version_info[:3])
    try:
        bat_version = importlib.metadata.version("batecg")
    except importlib.metadata.PackageNotFoundError:
        bat_version = None

    namespaces = sorted({name.split(".", 1)[0] for name in plugin_registry})

    entry_points_by_name: dict[str, importlib.metadata.EntryPoint] = {}
    for entry_point in importlib.metadata.entry_points(group="bat.plugins"):
        entry_points_by_name.setdefault(entry_point.name, entry_point)

    plugins = []
    for namespace in namespaces:
        matched_ep = entry_points_by_name.get(namespace)
        if matched_ep is not None:
            plugins.append(
                {
                    "name": namespace,
                    "version": _entry_point_version(matched_ep),
                    "source": "installed",
                }
            )
        else:
            plugins.append({"name": namespace, "version": None, "source": "local"})

    return {
        "python_version": python_version,
        "bat_version": bat_version,
        "plugins": plugins,
    }


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------


def _hash_file(path: Path) -> str | None:
    """SHA-256 hex digest of ``path``, or ``None`` if hashing isn't feasible
    (missing file, unreadable, or too large -- see :data:`_MAX_HASHABLE_BYTES`).
    """
    try:
        if path.stat().st_size > _MAX_HASHABLE_BYTES:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def compute_input_hashes(artifact: Artifact) -> dict[str, str | None]:
    """Compute SHA-256 hashes of every regular file directly under
    ``artifact.path``.

    Returns a dict of filename -> hex digest (or ``None`` for a file that
    couldn't be hashed, e.g. too large or unreadable). Single-file
    artifacts get a single-entry dict; multi-file artifacts (e.g. WFDB's
    ``.dat`` + ``.hea``) get one entry per file. If ``artifact.path``
    doesn't exist or isn't a directory containing files, returns ``{}``.

    This is a best-effort utility for computing the hashes recorded under
    an artifact's ``input_hashes`` in provenance -- typically called once
    per *input* artifact of the step that produced a given artifact.
    """
    path = Path(artifact.path)

    if path.is_file():
        return {path.name: _hash_file(path)}

    if not path.is_dir():
        return {}

    return {entry.name: _hash_file(entry) for entry in sorted(path.iterdir()) if entry.is_file()}


def build_artifact_record(
    artifact: Artifact, registry: Any | None = None
) -> ArtifactRecord:
    """Build an :class:`ArtifactRecord` for ``artifact``.

    If ``registry`` (an :class:`~bat.artifacts.registry.ArtifactRegistry`)
    is given, ``input_hashes`` is (re)computed from the artifact's declared
    input artifacts (via :func:`compute_input_hashes`), keyed by input
    artifact name. Otherwise, ``artifact.input_hashes`` is used as-is.
    """
    if registry is None:
        return ArtifactRecord.from_artifact(artifact)

    input_hashes: dict[str, Any] = {}
    for input_name in artifact.input_artifact_names:
        try:
            input_artifact = registry.get(input_name)
        except Exception:
            input_hashes[input_name] = None
            continue
        input_hashes[input_name] = compute_input_hashes(input_artifact)

    return ArtifactRecord.from_artifact(artifact, input_hashes=input_hashes)
