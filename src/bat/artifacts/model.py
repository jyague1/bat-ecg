"""Artifact data model for BAT runs.

An :class:`Artifact` is the unit of data exchange between steps in a BAT
protocol. Steps do not pass data to one another directly -- they declare
named outputs as artifacts, and downstream steps consume them by name.
Artifacts are:

- Globally unique by name across an entire protocol run
- Immutable once registered (see :class:`bat.artifacts.registry.ArtifactRegistry`)
- Typed (``signal``, ``annotations``, ``features``, ``metadata``, ``model``,
  ``report``, ``error``), each with a default on-disk format
- Provenance-tracked (creator step/module/workflow, params, input hashes,
  timestamp)

See ``cards/backlog/CARD-008-artifact-model.md`` for the full spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Re-exported from the shared source of truth (bat.artifacts.types) so
# existing references to bat.artifacts.model.DEFAULT_FORMATS /
# ARTIFACT_TYPES keep working.
from bat.artifacts.types import ARTIFACT_TYPES, DEFAULT_FORMATS

__all__ = ["ARTIFACT_TYPES", "DEFAULT_FORMATS", "Artifact", "ArtifactConflictError"]


class ArtifactConflictError(Exception):
    """Raised when an artifact name is already claimed.

    Artifacts are immutable once registered (or, in :mod:`bat.artifacts.storage`,
    once written to disk): a name can only ever be produced once within a run.
    """


@dataclass
class Artifact:
    """A single, immutable, typed unit of data produced by a step.

    Attributes:
        name: Globally unique name declared in the protocol.
        artifact_type: One of :data:`ARTIFACT_TYPES` (signal, annotations,
            features, metadata, model, report, error).
        format: On-disk format (wfdb, parquet, yaml, onnx, html, ...).
        path: Absolute path on disk to the artifact's directory.
        metadata: Arbitrary key-value metadata.
        creator_module: Dotted module name of the step that produced this
            artifact.
        creator_step: ID of the step that produced this artifact.
        creator_workflow: ID of the workflow the creator step belongs to.
        params: Params passed to the creator step.
        input_artifact_names: Names of input artifacts consumed to produce
            this one.
        input_hashes: sha256 hashes of input artifacts, keyed by name, where
            it was possible to compute them.
        timestamp: UTC creation time.
    """

    name: str
    artifact_type: str
    format: str
    path: Path
    metadata: dict[str, Any] = field(default_factory=dict)
    creator_module: str = ""
    creator_step: str = ""
    creator_workflow: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    input_artifact_names: list[str] = field(default_factory=list)
    input_hashes: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        self.path = Path(self.path)
