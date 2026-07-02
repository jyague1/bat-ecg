"""YAML loader for BAT protocol files.

Reads a protocol YAML file from disk, statically inlines any ``imports`` it
declares (see :mod:`bat.engine.imports`), and parses the result into a
validated :class:`bat.engine.schema.Protocol` object. All schema and
cross-reference validation (unique step ids, unique artifact names, valid
``depends_on`` references, resolvable artifact inputs) happens inside the
Pydantic models in :mod:`bat.engine.schema`; this module is only
responsible for I/O, import resolution, and surfacing readable errors.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from bat.engine.imports import ImportResolutionError, resolve_imports
from bat.engine.schema import Protocol


class ProtocolError(Exception):
    """Raised when a protocol file cannot be read or fails validation."""


def load_protocol(path: str | Path) -> Protocol:
    """Load and validate a BAT protocol file.

    Args:
        path: Path to the protocol YAML file.

    Returns:
        A validated :class:`~bat.engine.schema.Protocol` object.

    Raises:
        ProtocolError: If the file is missing, is not valid YAML, does not
            contain a YAML mapping, or fails schema/validation rules. The
            error message includes the offending field path (for validation
            failures) and a human-readable description.
    """
    path = Path(path)

    if not path.is_file():
        raise ProtocolError(f"Protocol file not found: {path}")

    try:
        raw_text = path.read_text()
    except OSError as exc:
        raise ProtocolError(f"Could not read protocol file {path}: {exc}") from exc

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ProtocolError(f"Invalid YAML in protocol file {path}: {exc}") from exc

    if data is None:
        raise ProtocolError(f"Protocol file {path} is empty")

    if not isinstance(data, dict):
        raise ProtocolError(
            f"Protocol file {path} must contain a YAML mapping at the top "
            f"level, got {type(data).__name__}"
        )

    try:
        data = resolve_imports(data, base_path=path.parent)
    except ImportResolutionError as exc:
        raise ProtocolError(
            f"Could not resolve imports for protocol file {path}: {exc}"
        ) from exc

    try:
        return Protocol.model_validate(data)
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            field_path = ".".join(str(part) for part in error["loc"]) or "<root>"
            messages.append(f"{field_path}: {error['msg']}")
        details = "\n".join(f"  - {m}" for m in messages)
        raise ProtocolError(
            f"Protocol file {path} failed validation:\n{details}"
        ) from exc
