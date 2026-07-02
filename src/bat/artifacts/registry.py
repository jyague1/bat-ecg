"""In-memory registry of artifacts produced during a single BAT run.

The registry is the single source of truth for "which artifacts exist" --
the engine consults it (never the raw contents of ``artifacts/`` on disk)
when validating step outputs and resolving step inputs. See
``cards/backlog/CARD-008-artifact-model.md``.
"""

from __future__ import annotations

from bat.artifacts.model import Artifact, ArtifactConflictError

__all__ = ["ArtifactRegistry", "ArtifactNotFoundError"]


class ArtifactNotFoundError(Exception):
    """Raised by :meth:`ArtifactRegistry.get` when the name is not registered."""


class ArtifactRegistry:
    """Tracks all artifacts produced during a single run.

    Scoped to a single run. Enforces immutability: once a name has been
    registered it can never be replaced, so a run can never silently
    overwrite an earlier artifact.
    """

    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    def register(self, artifact: Artifact) -> None:
        """Register a new artifact.

        Raises:
            ArtifactConflictError: If an artifact with this name is already
                registered.
        """
        if artifact.name in self._artifacts:
            raise ArtifactConflictError(
                f"artifact {artifact.name!r} is already registered "
                "(artifact names are immutable once produced)"
            )
        self._artifacts[artifact.name] = artifact

    def get(self, name: str) -> Artifact:
        """Retrieve an artifact by name.

        Raises:
            ArtifactNotFoundError: If no artifact with this name is registered.
        """
        try:
            return self._artifacts[name]
        except KeyError:
            raise ArtifactNotFoundError(
                f"no artifact registered with name {name!r}"
            ) from None

    def exists(self, name: str) -> bool:
        """Return whether an artifact with this name is registered."""
        return name in self._artifacts

    def all(self) -> list[Artifact]:
        """Return all registered artifacts, in registration order."""
        return list(self._artifacts.values())
