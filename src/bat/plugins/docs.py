"""Markdown documentation generation for BAT plugins (CARD-018).

``bat plugins docs`` renders full Markdown reference documentation for every
module in a discovered plugin registry, derived entirely from each module's
``schema`` (a :class:`bat.plugins.schema.ModuleSchema` subclass) -- the same
introspection surface ``bat plugins list`` (CARD-017) uses, but rendered as
Markdown tables/sections rather than terminal-formatted text.

See ``cards/backlog/CARD-018-bat-plugins-docs-command.md`` for the exact
output structure this module reproduces.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import yaml

from bat.engine.provenance import build_environment_record

__all__ = ["generate_docs"]

#: Display names for common Python annotations, used in the Parameters
#: table's Type column. ``str`` reads better as "string" in prose docs;
#: other common types are already clear under their Python name.
_TYPE_DISPLAY_NAMES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "float",
    bool: "boolean",
}


def _type_name(annotation: Any) -> str:
    """Render a Pydantic field's annotation as a short, human-readable type
    name for the Parameters table (e.g. ``string`` rather than ``str``)."""
    if annotation in _TYPE_DISPLAY_NAMES:
        return _TYPE_DISPLAY_NAMES[annotation]
    return getattr(annotation, "__name__", str(annotation))


def _render_table(headers: list[str], rows: list[tuple[str, ...]]) -> str:
    """Render a padded, GitHub-flavored Markdown table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: tuple[str, ...]) -> str:
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) + " |"

    lines = [fmt_row(tuple(headers)), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    lines.extend(fmt_row(row) for row in rows)
    return "\n".join(lines)


def _params_table(schema: type) -> str:
    """Render a module schema's ``Params`` fields as a Markdown table with
    Name/Type/Required/Default/Description columns. Returns ``"(none)"`` if
    the module declares no params."""
    fields = schema.Params.model_fields
    if not fields:
        return "(none)"

    rows = []
    for name, info in fields.items():
        required = info.is_required()
        default = "" if required else str(info.default)
        rows.append(
            (
                name,
                _type_name(info.annotation),
                "yes" if required else "no",
                default,
                info.description or "",
            )
        )
    return _render_table(["Name", "Type", "Required", "Default", "Description"], rows)


def _artifacts_table(model: type) -> str:
    """Render an ``Inputs``/``Outputs`` model's fields as a Markdown table
    with Name/Type/Format columns. Returns ``"(none)"`` if the model
    declares no fields."""
    fields = model.model_fields
    if not fields:
        return "(none)"

    rows = []
    for name, info in fields.items():
        extra = info.json_schema_extra or {}
        rows.append(
            (
                name,
                extra.get("artifact_type", "?"),
                extra.get("artifact_format", "?"),
            )
        )
    return _render_table(["Name", "Type", "Format"], rows)


def _source_label(namespace: str, plugin_info: dict) -> str:
    """Render a namespace's source as ``<namespace> <version> (installed)``
    or ``<namespace> (local)``, per the card's example output.

    Reuses :func:`bat.engine.provenance.build_environment_record` (CARD-012)
    for installed-vs-local/version detection, as CARD-017's
    ``bat plugins list`` also does.
    """
    if plugin_info["source"] == "installed":
        version = plugin_info["version"] or "unknown"
        return f"{namespace} {version} (installed)"
    return f"{namespace} (local)"


def _citations_lines(citations: Any) -> list[str]:
    """Render ``Meta.citations`` as the lines following ``**Citations:**``.

    The literal ``"none"`` (or a falsy value) renders as the single line
    ``**Citations:** none``; a non-empty list renders as ``**Citations:**``
    followed by one bulleted line per citation.
    """
    if citations == "none" or not citations:
        return ["**Citations:** none"]
    return ["**Citations:**"] + [f"- {citation}" for citation in citations]


def _render_examples(examples: list[dict]) -> list[str]:
    """Render each ``Meta.examples`` entry as its own fenced YAML block.

    Each example dict is dumped wrapped in a one-item list (via
    ``yaml.safe_dump([example], sort_keys=False)``) so it renders as a
    single ``- id: ...`` workflow-step-shaped YAML block, matching the
    card's example output.
    """
    blocks = ["#### Examples"]
    for example in examples:
        yaml_text = yaml.safe_dump([example], sort_keys=False).rstrip("\n")
        blocks.append(f"```yaml\n{yaml_text}\n```")
    return blocks


def _render_module(
    module_name: str, module: Any, source_by_namespace: dict[str, dict]
) -> list[str]:
    """Render a single module's documentation as a list of Markdown blocks
    (joined with blank lines by the caller)."""
    schema = module.schema
    meta = schema.Meta
    namespace = module_name.split(".", 1)[0]

    meta_block = "\n".join(
        [f"**Source:** {_source_label(namespace, source_by_namespace[namespace])}"]
        + _citations_lines(getattr(meta, "citations", None))
    )

    blocks = [
        f"### `{module_name}`",
        getattr(meta, "description", ""),
        meta_block,
        "#### Parameters",
        _params_table(schema),
        "#### Inputs",
        _artifacts_table(schema.Inputs),
        "#### Outputs",
        _artifacts_table(schema.Outputs),
    ]

    examples = getattr(meta, "examples", None) or []
    if examples:
        blocks.extend(_render_examples(examples))

    return blocks


def generate_docs(registry: dict) -> str:
    """Generate full Markdown documentation for all modules in ``registry``.

    Modules are grouped by top-level collection namespace (e.g. ``core``,
    ``lab``) under a ``## <namespace>`` heading, sorted alphabetically both
    by namespace and by module name within a namespace, for deterministic
    output. Each module renders as a ``### `<module.name>` `` section per
    the card's structure: description, source/citations, and
    Parameters/Inputs/Outputs tables, plus an Examples section when the
    module declares any.
    """
    env = build_environment_record(registry)
    source_by_namespace = {p["name"]: p for p in env["plugins"]}

    date_str = datetime.now().strftime("%Y-%m-%d")
    blocks: list[str] = [
        "# BAT Plugin Reference",
        f"Generated by `bat plugins docs` on {date_str}.",
        "---",
    ]

    namespaces = sorted({name.split(".", 1)[0] for name in registry})
    for namespace in namespaces:
        blocks.append(f"## {namespace}")
        module_names = sorted(n for n in registry if n.split(".", 1)[0] == namespace)
        for module_name in module_names:
            blocks.extend(_render_module(module_name, registry[module_name], source_by_namespace))
            blocks.append("---")

    return "\n\n".join(blocks)
