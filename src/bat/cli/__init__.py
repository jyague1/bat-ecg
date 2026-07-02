"""BAT command-line interface.

This module exposes the Click group root (``main``) used as the ``bat``
console script entry point. Subcommands are added by later cards.
"""

import click

from bat import __version__


@click.group()
@click.version_option(version=__version__, prog_name="bat")
def main() -> None:
    """BAT: Biomedical Analysis Toolbox."""


if __name__ == "__main__":
    main()
