"""``bat plugins`` command group.

Groups plugin-discovery related subcommands: ``bat plugins list`` and
``bat plugins docs``. Discovery and documentation generation are stubs here
and are implemented by later cards.
"""

import click


@click.group("plugins")
def plugins() -> None:
    """Inspect and document available BAT plugins."""


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
    """List discovered plugins."""
    click.echo("bat plugins list: not implemented yet")


@plugins.command("docs")
def docs() -> None:
    """Generate plugin documentation."""
    click.echo("bat plugins docs: not implemented yet")
