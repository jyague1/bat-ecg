"""BAT command-line interface.

This module exposes the Click group root (``main``) used as the ``bat``
console script entry point. Subcommands are stubs wired up by CARD-002; the
underlying business logic is implemented by later cards.
"""

import click

from bat import __version__
from bat.cli.init import init
from bat.cli.plugins import plugins
from bat.cli.run import dry_run, run
from bat.cli.validate import validate


@click.group()
@click.version_option(version=__version__, prog_name="bat")
def main() -> None:
    """BAT: Biomedical Analysis Toolbox."""


main.add_command(run)
main.add_command(dry_run)
main.add_command(validate)
main.add_command(plugins)
main.add_command(init)


if __name__ == "__main__":
    main()
