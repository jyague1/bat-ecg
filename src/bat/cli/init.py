"""``bat init`` command.

Scaffolds a new BAT project directory containing the standard
``plugins/``, ``vars/``, and ``runs/`` subdirectories plus a starter
``protocol.yaml`` that runs out of the box (see CARD-014).
"""

from pathlib import Path

import click

#: Starter protocol written into every newly-scaffolded project.
STARTER_PROTOCOL = """\
version: "0.1"

# Variables can be overridden at runtime:
#   bat run protocol.yaml --var record=101
vars:
  record: "100"

workflows:
  - id: load
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}"
        outputs:
          raw_signal:
            type: signal
            format: wfdb
"""

#: Subdirectories created inside every newly-scaffolded project.
PROJECT_SUBDIRS = ("plugins", "vars", "runs")


@click.command("init")
@click.argument("project_name")
def init(project_name: str) -> None:
    """Scaffold a new BAT project directory named PROJECT_NAME."""
    project_dir = Path(project_name)

    if project_dir.exists():
        raise click.ClickException(
            f"Directory already exists: {project_dir}/ "
            "(refusing to overwrite an existing project)"
        )

    project_dir.mkdir(parents=True)
    for subdir in PROJECT_SUBDIRS:
        (project_dir / subdir).mkdir()

    (project_dir / "protocol.yaml").write_text(STARTER_PROTOCOL)

    click.echo(f"Created project: {project_name}/")
    click.echo()
    click.echo("Get started:")
    click.echo(f"  cd {project_name}")
    click.echo("  bat run protocol.yaml --dry-run")
