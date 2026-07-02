"""Static import resolution for BAT protocol files.

Protocols (and the files they import) may declare a top-level ``imports``
list naming other YAML files whose ``vars`` and ``workflows`` should be
inlined before schema validation. Imports are resolved entirely at parse
time: import paths must be static strings (no ``{{ var }}`` expressions),
and the imported content is merged in before the raw dict is handed to the
Pydantic schema. See
``cards/backlog/CARD-004-static-import-resolution.md`` for the full spec.

Merge rules:

- Imported ``vars`` are merged into the enclosing file's ``vars`` at the
  *lowest* precedence -- the enclosing file's own ``vars`` win on conflict.
  This mirrors the CLI > vars-file > protocol vars > imported vars
  precedence chain (CARD-005 implements the higher tiers).
- Imported ``workflows`` are merged into the enclosing file's ``workflows``
  dict; the enclosing file's own workflow definitions win on conflict.
- Imports are resolved relative to the directory of the file that declares
  the ``imports`` key.
- Imports are processed depth-first: an imported file may itself declare
  further imports, which are resolved before merging that file's content
  into its importer.
- Circular imports (an import chain that revisits a file it is already in
  the middle of resolving) are rejected.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

#: Matches Jinja-style variable expressions, e.g. "{{ record }}".
_TEMPLATE_EXPR_RE = re.compile(r"{{.*?}}")


class ImportResolutionError(Exception):
    """Raised when a protocol's ``imports`` cannot be statically resolved."""


def resolve_imports(raw: dict[str, Any], base_path: Path) -> dict[str, Any]:
    """Recursively inline ``imports`` declared in ``raw`` before validation.

    Args:
        raw: The raw parsed YAML dict for a protocol (or an imported file),
            before Pydantic validation.
        base_path: Directory of the file ``raw`` was parsed from; relative
            import paths declared in ``raw`` are resolved against this
            directory.

    Returns:
        A new dict with all imports resolved and inlined: imported ``vars``
        merged at lowest precedence (``raw``'s own ``vars`` win), imported
        ``workflows`` merged into ``raw``'s ``workflows`` dict, and the
        ``imports`` key itself removed (it is not part of the protocol
        schema). If ``raw`` has no ``imports`` key, an equivalent dict is
        returned unchanged.

    Raises:
        ImportResolutionError: If an import entry is not a static string
            path, an import path contains a ``{{ ... }}`` expression, an
            imported file is missing / unreadable / not a YAML mapping, or
            a circular import chain is detected.
    """
    return _resolve(raw, Path(base_path), stack=())


def _resolve(
    raw: dict[str, Any], base_path: Path, stack: tuple[Path, ...]
) -> dict[str, Any]:
    imports = raw.get("imports") or []

    if not isinstance(imports, list):
        raise ImportResolutionError(
            "'imports' must be a list of file paths, got "
            f"{type(imports).__name__}"
        )

    merged_vars: dict[str, Any] = {}
    merged_workflows: dict[str, Any] = {}

    for entry in imports:
        if not isinstance(entry, str):
            raise ImportResolutionError(
                f"import entries must be static string paths, got {entry!r}"
            )

        if _TEMPLATE_EXPR_RE.search(entry):
            raise ImportResolutionError(
                f"import path {entry!r} contains a variable expression "
                "('{{ ... }}'); import paths must be static strings in v1 "
                "(dynamic includes are not supported)"
            )

        import_path = (base_path / entry).resolve()

        if import_path in stack:
            chain = " -> ".join(str(p) for p in (*stack, import_path))
            raise ImportResolutionError(f"circular import detected: {chain}")

        if not import_path.is_file():
            raise ImportResolutionError(
                f"imported file not found: {entry!r} (resolved to "
                f"{import_path}, imported from {base_path})"
            )

        try:
            imported_text = import_path.read_text()
        except OSError as exc:
            raise ImportResolutionError(
                f"could not read imported file {import_path}: {exc}"
            ) from exc

        try:
            imported_raw = yaml.safe_load(imported_text)
        except yaml.YAMLError as exc:
            raise ImportResolutionError(
                f"invalid YAML in imported file {import_path}: {exc}"
            ) from exc

        if imported_raw is None:
            imported_raw = {}

        if not isinstance(imported_raw, dict):
            raise ImportResolutionError(
                f"imported file {import_path} must contain a YAML mapping "
                f"at the top level, got {type(imported_raw).__name__}"
            )

        # Depth-first: resolve the imported file's own imports before
        # merging its content into the importer.
        resolved_imported = _resolve(
            imported_raw, import_path.parent, stack=(*stack, import_path)
        )

        merged_vars.update(resolved_imported.get("vars") or {})
        merged_workflows.update(resolved_imported.get("workflows") or {})

    result = dict(raw)
    result.pop("imports", None)

    own_vars = raw.get("vars") or {}
    own_workflows = raw.get("workflows") or {}

    # Imported values are lowest precedence: the enclosing file's own
    # vars/workflows win on key conflicts.
    final_vars = {**merged_vars, **own_vars}
    final_workflows = {**merged_workflows, **own_workflows}

    if final_vars:
        result["vars"] = final_vars
    if final_workflows:
        result["workflows"] = final_workflows

    return result
