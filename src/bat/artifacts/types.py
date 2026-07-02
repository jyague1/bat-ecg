"""Single source of truth for BAT artifact types and default formats.

Kept as a tiny leaf module with no heavy imports so every layer that needs
the artifact-type vocabulary -- the artifact model
(:mod:`bat.artifacts.model`), the protocol schema
(:mod:`bat.engine.schema`), and the plugin schema
(:mod:`bat.plugins.schema`) -- can share one definition instead of each
redeclaring the list (which previously lived in three places and could
silently drift).

See ``cards/backlog/CARD-008-artifact-model.md`` and
``CARD-007-plugin-interface-schema.md`` for the artifact-type spec.
"""

from __future__ import annotations

from typing import Literal, get_args

#: The canonical artifact-type type. Used directly as a Pydantic field
#: annotation (e.g. ``ArtifactDeclaration.type``) and as the source of
#: truth for :data:`ARTIFACT_TYPES` below.
ArtifactType = Literal[
    "signal", "annotations", "features", "metadata", "model", "report", "error"
]

#: Default on-disk format for each artifact type.
DEFAULT_FORMATS: dict[str, str] = {
    "signal": "wfdb",
    "annotations": "wfdb",
    "features": "parquet",
    "metadata": "yaml",
    "model": "onnx",
    "report": "html",
    "error": "yaml",
}

#: Valid artifact-type values, derived from :data:`ArtifactType` so the
#: two cannot drift apart.
ARTIFACT_TYPES: frozenset[str] = frozenset(get_args(ArtifactType))

# Guard against drift: every declared type must have a default format and
# vice versa. A failure here means ArtifactType and DEFAULT_FORMATS were
# edited inconsistently.
assert ARTIFACT_TYPES == frozenset(DEFAULT_FORMATS), (
    "ArtifactType and DEFAULT_FORMATS have drifted; keep their members in sync"
)
