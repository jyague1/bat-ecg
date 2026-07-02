"""YAML loader for BAT protocol files.

Reads a protocol YAML file from disk, statically inlines any ``imports`` it
declares (see :mod:`bat.engine.imports`), substitutes ``{{ var }}``
references using the merged variable context (see
:mod:`bat.engine.variables`), and parses the result into a validated
:class:`bat.engine.schema.Protocol` object. All schema and cross-reference
validation (unique step ids, unique artifact names, valid ``depends_on``
references, resolvable artifact inputs) happens inside the Pydantic models
in :mod:`bat.engine.schema`; this module is only responsible for I/O,
import resolution, variable substitution, and surfacing readable errors.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from bat.engine.executor import check_acyclic
from bat.engine.imports import ImportResolutionError, resolve_imports
from bat.engine.schema import Protocol
from bat.engine.variables import (
    VariableResolutionError,
    build_var_context,
    substitute_vars,
)


class ProtocolError(Exception):
    """Raised when a protocol file cannot be read or fails validation."""


def load_protocol(
    path: str | Path,
    vars_file: str | Path | None = None,
    cli_vars: dict[str, str] | None = None,
) -> Protocol:
    """Load and validate a BAT protocol file.

    Args:
        path: Path to the protocol YAML file.
        vars_file: Optional path to a YAML file (``--vars-file``) providing
            a flat mapping of variable name to value.
        cli_vars: Optional mapping of variables passed via repeated
            ``--var name=value`` CLI flags. Highest precedence.

    Returns:
        A validated :class:`~bat.engine.schema.Protocol` object, with all
        ``{{ var }}`` references substituted.

    Raises:
        ProtocolError: If the file is missing, is not valid YAML, does not
            contain a YAML mapping, fails variable substitution (e.g. a
            referenced variable is undefined, or ``vars_file`` cannot be
            read), or fails schema/validation rules. The error message
            includes the offending field path (for validation failures) and
            a human-readable description.
        CycleError: If any workflow-level or step-level ``depends_on`` graph
            contains a cycle. Detected here (not in the Pydantic schema,
            which only checks that ``depends_on`` targets exist) so every
            caller of ``load_protocol`` -- the runner, and ``bat validate``'s
            counterpart -- rejects cyclic protocols consistently.
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
        data, imported_vars = resolve_imports(data, base_path=path.parent)
    except ImportResolutionError as exc:
        raise ProtocolError(
            f"Could not resolve imports for protocol file {path}: {exc}"
        ) from exc

    try:
        context = build_var_context(
            data.get("vars", {}),
            imported_vars,
            Path(vars_file) if vars_file is not None else None,
            cli_vars or {},
        )
        data = substitute_vars(data, context)
    except VariableResolutionError as exc:
        raise ProtocolError(
            f"Could not substitute variables in protocol file {path}: {exc}"
        ) from exc

    try:
        protocol = Protocol.model_validate(data)
    except ValidationError as exc:
        messages = []
        for error in exc.errors():
            field_path = ".".join(str(part) for part in error["loc"]) or "<root>"
            messages.append(f"{field_path}: {error['msg']}")
        details = "\n".join(f"  - {m}" for m in messages)
        raise ProtocolError(
            f"Protocol file {path} failed validation:\n{details}"
        ) from exc

    # Cycle detection is not expressible as a Pydantic field/model validator
    # (it needs the whole workflow/step graph), so it runs here, after schema
    # validation has confirmed every depends_on target exists. Raises
    # CycleError, which the CLI already treats as a pre-flight error.
    check_acyclic(protocol)
    return protocol
