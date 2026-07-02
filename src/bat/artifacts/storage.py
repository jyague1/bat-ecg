"""Disk storage helpers for artifacts.

Artifacts live under ``runs/<run-id>/artifacts/<artifact-name>/``. Each
artifact directory contains the artifact's data file(s) (format-dependent,
and out of scope for this module -- writing e.g. wfdb or parquet payloads
is the job of the format-specific reader/writer modules) plus a
``meta.yaml`` file holding the artifact's provenance metadata.

This module only concerns itself with:

- Computing/creating the artifact directory layout on disk
- Serializing an :class:`~bat.artifacts.model.Artifact` to/from ``meta.yaml``
- Generic YAML data file read/write, used directly by ``metadata`` and
  ``error`` typed artifacts (whose format is ``yaml``)

See ``cards/backlog/CARD-008-artifact-model.md`` for the full spec. The
precise contents of an error artifact's data file (message/traceback/
step_id/timestamp keys) are refined in CARD-011; here we only guarantee
that an ``error``-typed artifact works end-to-end through the same
plumbing as any other artifact.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from bat._util import format_utc

from bat.artifacts.model import Artifact, ArtifactConflictError

__all__ = [
    "META_FILENAME",
    "artifact_dir",
    "meta_path",
    "write_meta",
    "read_meta",
    "write_data_yaml",
    "read_data_yaml",
]

META_FILENAME = "meta.yaml"

#: Default filename for generic YAML-format artifact data (metadata/error).
DEFAULT_DATA_FILENAME = "data.yaml"


def artifact_dir(run_dir: Path | str, artifact_name: str) -> Path:
    """Return the directory an artifact's files live under.

    ``<run_dir>/artifacts/<artifact_name>/``
    """
    return Path(run_dir) / "artifacts" / artifact_name


def meta_path(run_dir: Path | str, artifact_name: str) -> Path:
    """Return the path to an artifact's ``meta.yaml``."""
    return artifact_dir(run_dir, artifact_name) / META_FILENAME


def _format_timestamp(timestamp: datetime) -> str:
    """Render a timestamp as UTC ``YYYY-MM-DDTHH:MM:SSZ``."""
    return format_utc(timestamp)


def _display_path(run_dir: Path, artifact_name: str) -> str:
    """Render the artifact directory the way the card's example shows it.

    ``run_dir`` is expected to look like ``<project_root>/runs/<run-id>``,
    so the displayed path is ``runs/<run-id>/artifacts/<artifact-name>/``,
    relative to ``project_root``. If ``run_dir`` doesn't fit that shape
    (e.g. in isolated unit tests), fall back to the plain directory path.
    """
    directory = artifact_dir(run_dir, artifact_name)
    project_root = run_dir.parent.parent
    try:
        rel = directory.relative_to(project_root)
        return rel.as_posix() + "/"
    except ValueError:
        return directory.as_posix() + "/"


def artifact_to_meta_dict(artifact: Artifact, run_dir: Path | str) -> dict[str, Any]:
    """Build the ``meta.yaml``-serializable dict for an artifact."""
    run_dir = Path(run_dir)
    return {
        "name": artifact.name,
        "type": artifact.artifact_type,
        "format": artifact.format,
        "path": _display_path(run_dir, artifact.name),
        "creator_module": artifact.creator_module,
        "creator_step": artifact.creator_step,
        "creator_workflow": artifact.creator_workflow,
        "timestamp": _format_timestamp(artifact.timestamp),
        "params": artifact.params,
        "input_artifact_names": list(artifact.input_artifact_names),
        "input_hashes": dict(artifact.input_hashes),
        "metadata": artifact.metadata,
    }


def write_meta(run_dir: Path | str, artifact: Artifact) -> Path:
    """Write ``meta.yaml`` for ``artifact`` under ``run_dir``.

    Creates the artifact's directory if needed. Refuses to overwrite an
    existing ``meta.yaml`` for the same artifact name, enforcing on-disk
    immutability to match :meth:`ArtifactRegistry.register`.

    Raises:
        ArtifactConflictError: If a ``meta.yaml`` already exists for this
            artifact name under ``run_dir``.
    """
    run_dir = Path(run_dir)
    directory = artifact_dir(run_dir, artifact.name)
    path = directory / META_FILENAME
    if path.exists():
        raise ArtifactConflictError(
            f"meta.yaml already exists for artifact {artifact.name!r} at "
            f"{path} (artifacts are immutable once written)"
        )
    directory.mkdir(parents=True, exist_ok=True)
    meta = artifact_to_meta_dict(artifact, run_dir)
    path.write_text(yaml.safe_dump(meta, sort_keys=False))
    return path


def read_meta(run_dir: Path | str, artifact_name: str) -> dict[str, Any]:
    """Read and parse an artifact's ``meta.yaml``."""
    path = meta_path(run_dir, artifact_name)
    with path.open("r") as fh:
        return yaml.safe_load(fh) or {}


def write_data_yaml(
    run_dir: Path | str,
    artifact_name: str,
    data: dict[str, Any],
    filename: str = DEFAULT_DATA_FILENAME,
) -> Path:
    """Write a YAML data file for a ``yaml``-format artifact.

    Used directly for ``metadata`` and ``error`` typed artifacts, whose
    default format is ``yaml``. Generic on purpose: the exact key shape of
    an error artifact's payload is refined in CARD-011.
    """
    directory = artifact_dir(run_dir, artifact_name)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def read_data_yaml(
    run_dir: Path | str,
    artifact_name: str,
    filename: str = DEFAULT_DATA_FILENAME,
) -> dict[str, Any]:
    """Read a YAML data file previously written by :func:`write_data_yaml`."""
    path = artifact_dir(run_dir, artifact_name) / filename
    with path.open("r") as fh:
        return yaml.safe_load(fh) or {}
