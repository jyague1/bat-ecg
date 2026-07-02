"""``bat init`` command.

Stub only — project scaffolding is implemented in a later card.
"""

import click


@click.command("init")
@click.argument("project_name")
def init(project_name) -> None:
    """Scaffold a new BAT project directory named PROJECT_NAME."""
    click.echo(f"bat init: not implemented yet (project_name={project_name})")
