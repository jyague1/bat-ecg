"""``{{ var }}`` substitution for BAT protocol files.

Protocols may reference variables anywhere a string value appears (params,
paths, names, ...) using ``{{ var_name }}`` syntax. Only simple, flat
substitution is supported in v1 -- no expressions, filters, conditionals, or
variable-referencing-variable chains.

Variables can come from four sources, with this precedence (highest to
lowest):

    CLI vars > CLI vars-file > protocol vars > imported vars

See ``cards/backlog/CARD-005-variable-system.md`` for the full spec.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

#: Matches a single "{{ var_name }}" expression, capturing the variable
#: name. Whitespace inside the braces is optional and ignored.
_VAR_RE = re.compile(r"{{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*}}")


class VariableResolutionError(Exception):
    """Raised when variable substitution fails.

    This covers both undefined ``{{ var }}`` references encountered during
    substitution and problems loading a ``--vars-file``.
    """


def build_var_context(
    protocol_vars: dict[str, Any],
    imported_vars: dict[str, Any],
    vars_file: Path | None,
    cli_vars: dict[str, str],
) -> dict[str, Any]:
    """Merge all variable sources into a single substitution context.

    Sources are merged in precedence order, lowest first, so later sources
    overwrite earlier ones on key conflicts:

        imported vars -> protocol vars -> vars-file vars -> CLI vars

    Args:
        protocol_vars: The protocol's own top-level ``vars`` mapping (after
            import resolution -- i.e. already reflecting "protocol vars win
            over imported vars" for any key declared in both places).
        imported_vars: Vars merged in purely from the protocol's ``imports``,
            before the protocol's own vars were applied.
        vars_file: Optional path to a YAML file (``--vars-file``) containing
            a flat mapping of variable name to value.
        cli_vars: Vars passed via repeated ``--var name=value`` CLI flags.

    Returns:
        The final merged variable context.

    Raises:
        VariableResolutionError: If ``vars_file`` is given but missing,
            unreadable, not valid YAML, or not a YAML mapping at the top
            level.
    """
    context: dict[str, Any] = {}
    context.update(imported_vars or {})
    context.update(protocol_vars or {})

    if vars_file is not None:
        vars_file = Path(vars_file)

        if not vars_file.is_file():
            raise VariableResolutionError(f"vars file not found: {vars_file}")

        try:
            file_text = vars_file.read_text()
        except OSError as exc:
            raise VariableResolutionError(
                f"could not read vars file {vars_file}: {exc}"
            ) from exc

        try:
            file_vars = yaml.safe_load(file_text)
        except yaml.YAMLError as exc:
            raise VariableResolutionError(
                f"invalid YAML in vars file {vars_file}: {exc}"
            ) from exc

        if file_vars is None:
            file_vars = {}

        if not isinstance(file_vars, dict):
            raise VariableResolutionError(
                f"vars file {vars_file} must contain a YAML mapping at the "
                f"top level, got {type(file_vars).__name__}"
            )

        context.update(file_vars)

    context.update(cli_vars or {})

    return context


def substitute_vars(raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Recursively replace ``{{ var }}`` references in ``raw`` with values from ``context``.

    Walks dicts and lists recursively. Only string values are inspected for
    ``{{ var }}`` tokens; other value types (int, float, bool, None, and
    nested dicts/lists as containers) are otherwise passed through
    unmodified. Partial substitution within a string is supported, e.g.
    ``"data/{{ record }}/ecg"``.

    Args:
        raw: The raw protocol dict (after import resolution), before
            Pydantic validation.
        context: The merged variable context, as returned by
            :func:`build_var_context`.

    Returns:
        A new dict with all ``{{ var }}`` references replaced.

    Raises:
        VariableResolutionError: If a string contains a ``{{ var }}``
            reference to a variable not present in ``context``. The error
            names the missing variable and the field (dotted path) where it
            was found.
    """
    result = _substitute(raw, context, path=())
    assert isinstance(result, dict)
    return result


def _substitute(value: Any, context: dict[str, Any], path: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return _substitute_string(value, context, path)
    if isinstance(value, dict):
        return {k: _substitute(v, context, (*path, str(k))) for k, v in value.items()}
    if isinstance(value, list):
        return [
            _substitute(item, context, (*path, f"[{i}]")) for i, item in enumerate(value)
        ]
    return value


def _substitute_string(s: str, context: dict[str, Any], path: tuple[str, ...]) -> str:
    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in context:
            field = ".".join(path) or "<root>"
            raise VariableResolutionError(
                f"undefined variable {name!r} referenced at {field!r} "
                f"(value: {s!r})"
            )
        return str(context[name])

    return _VAR_RE.sub(repl, s)
