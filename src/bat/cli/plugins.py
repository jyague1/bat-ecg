"""``bat plugins`` command group.

Groups plugin-discovery related subcommands: ``bat plugins list`` and
``bat plugins docs``. ``list`` shows what plugin modules are currently
discoverable in the environment (CARD-017); ``docs`` renders full Markdown
reference documentation for every discovered module (CARD-018).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from bat.engine.provenance import build_environment_record
from bat.plugins.discovery import PluginDiscoveryError, discover_plugins
from bat.plugins.docs import generate_docs


@click.group("plugins")
def plugins() -> None:
    """Inspect and document available BAT plugins."""


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------


def _type_name(annotation: Any) -> str:
    """Render a Pydantic field's annotation as a short, human-readable type
    name (e.g. ``float`` rather than ``<class 'float'>``)."""
    return getattr(annotation, "__name__", str(annotation))


def _format_params(schema: type) -> str:
    """Render a module schema's ``Params`` fields as a compact, comma
    separated summary, e.g. ``path (str, required), gain (float, default:
    1.0)``. Returns ``"(none)"`` if the module declares no params."""
    fields = schema.Params.model_fields
    if not fields:
        return "(none)"

    parts = []
    for name, info in fields.items():
        type_name = _type_name(info.annotation)
        if info.is_required():
            parts.append(f"{name} ({type_name}, required)")
        else:
            parts.append(f"{name} ({type_name}, default: {info.default})")
    return ", ".join(parts)


def _format_artifacts(model: type) -> str:
    """Render an ``Inputs``/``Outputs`` model's fields as a compact, comma
    separated summary, e.g. ``signal (type: signal, format: wfdb)``.
    Returns ``"(none)"`` if the model declares no fields."""
    fields = model.model_fields
    if not fields:
        return "(none)"

    parts = []
    for name, info in fields.items():
        extra = info.json_schema_extra or {}
        artifact_type = extra.get("artifact_type", "?")
        artifact_format = extra.get("artifact_format", "?")
        parts.append(f"{name} (type: {artifact_type}, format: {artifact_format})")
    return ", ".join(parts)


def _format_citations(citations: Any) -> str:
    """Render ``Meta.citations`` (either the literal ``"none"`` or a
    non-empty list of citation strings) as a single display line."""
    if citations == "none" or not citations:
        return "none"
    return "; ".join(citations)


def _source_label(module_name: str, module: Any) -> str:
    """Render a single module's source as ``installed (<namespace>
    <version>)`` or ``local (<namespace>)``.

    Reuses :func:`bat.engine.provenance.build_environment_record` (CARD-012)
    to determine whether the module's top-level namespace is installed
    (via the ``bat.plugins`` entry point group) or local, rather than
    reimplementing that detection here.
    """
    env = build_environment_record({module_name: module})
    plugin_info = env["plugins"][0]
    if plugin_info["source"] == "installed":
        version = plugin_info["version"] or "unknown"
        return f"installed ({plugin_info['name']} {version})"
    return f"local ({plugin_info['name']})"


def format_module_detail(module_name: str, module: Any) -> str:
    """Render the full verbose detail block for a single plugin module."""
    schema = module.schema
    meta = schema.Meta

    lines = [
        module_name,
        f"  Description : {getattr(meta, 'description', '')}",
        f"  Source      : {_source_label(module_name, module)}",
        f"  Citations   : {_format_citations(getattr(meta, 'citations', None))}",
        f"  Inputs      : {_format_artifacts(schema.Inputs)}",
        f"  Params      : {_format_params(schema)}",
        f"  Outputs     : {_format_artifacts(schema.Outputs)}",
    ]
    return "\n".join(lines)


def _format_compact_list(registry: dict) -> str:
    """Render the default (non-verbose) grouped-by-namespace listing."""
    env = build_environment_record(registry)

    modules_by_namespace: dict[str, list[str]] = {}
    for name in sorted(registry):
        namespace = name.split(".", 1)[0]
        modules_by_namespace.setdefault(namespace, []).append(name)

    installed = [p for p in env["plugins"] if p["source"] == "installed"]
    local = [p for p in env["plugins"] if p["source"] == "local"]

    sections: list[str] = []

    if installed:
        width = max(len(p["name"]) for p in installed) + 2
        lines = ["Installed plugins:"]
        for p in installed:
            version = p["version"] or "unknown"
            modules = ", ".join(modules_by_namespace[p["name"]])
            lines.append(f"  {p['name']:<{width}}({version})   {modules}")
        sections.append("\n".join(lines))

    if local:
        width = max(len(p["name"]) for p in local) + 2
        lines = ["Local plugins (plugins/):"]
        for p in local:
            modules = ", ".join(modules_by_namespace[p["name"]])
            lines.append(f"  {p['name']:<{width}}{modules}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def format_plugin_list(registry: dict, verbose: bool) -> str:
    """Render the full ``bat plugins list`` output for every module in
    ``registry``.

    ``verbose=False`` groups modules by top-level namespace (installed vs.
    local, with versions -- see :func:`_format_compact_list``);
    ``verbose=True`` shows the full detail block (see
    :func:`format_module_detail`) for every module, sorted by name.
    """
    if not registry:
        return "No plugins discovered."

    if verbose:
        return "\n\n".join(
            format_module_detail(name, module)
            for name, module in sorted(registry.items())
        )

    return _format_compact_list(registry)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


@plugins.command("list")
@click.option(
    "--verbose",
    is_flag=True,
    default=False,
    help="Show detailed information for each discovered plugin.",
)
@click.option(
    "--module",
    "module",
    default=None,
    help="Filter the listing to a specific module by dotted name.",
)
def list_(verbose, module) -> None:
    """List discovered plugins.

    Runs plugin discovery (CARD-006) against the ``plugins/`` directory in
    the current working directory (if any) plus any installed ``bat.plugins``
    entry points. Does not require a protocol file.
    """
    plugins_dir = Path.cwd() / "plugins"
    try:
        registry = discover_plugins(plugins_dir)
    except PluginDiscoveryError as exc:
        raise click.ClickException(str(exc)) from exc

    if module is not None:
        if module not in registry:
            raise click.ClickException(
                f"Unknown plugin module {module!r}: not found in the "
                f"discovered plugin registry."
            )
        click.echo(format_module_detail(module, registry[module]))
        return

    click.echo(format_plugin_list(registry, verbose))


@plugins.command("docs")
def docs() -> None:
    """Generate Markdown documentation for all discovered plugin modules.

    Runs plugin discovery (CARD-006) against the ``plugins/`` directory in
    the current working directory (if any) plus any installed ``bat.plugins``
    entry points -- same convention as ``bat plugins list``, no protocol
    file required. The generated Markdown is printed to stdout; redirect to
    a file to save it, e.g. ``bat plugins docs > docs/plugins.md``.
    """
    plugins_dir = Path.cwd() / "plugins"
    try:
        registry = discover_plugins(plugins_dir)
    except PluginDiscoveryError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(generate_docs(registry))
