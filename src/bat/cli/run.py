"""``bat run`` and ``bat dry-run`` commands.

Both commands share the same argument/option surface: a protocol path plus
options for naming the run and supplying protocol variables. ``bat run
--dry-run`` and ``bat dry-run`` are equivalent — they are kept as separate
commands (rather than one calling the other) so that ``bat dry-run --help``
shows a signature without the (implicit, always-true) ``--dry-run`` flag.

No business logic is implemented here yet; execution and validation land in
later cards (CARD-014 through CARD-018).
"""

import click


def _common_run_options(f):
    """Options shared by ``bat run`` and ``bat dry-run``."""
    f = click.option(
        "--run-name",
        default=None,
        help="Name for the run directory (e.g. mitdb-baseline).",
    )(f)
    f = click.option(
        "--var",
        "variables",
        multiple=True,
        metavar="KEY=VALUE",
        help="Set a protocol variable as key=value. May be repeated.",
    )(f)
    f = click.option(
        "--vars-file",
        "vars_file",
        type=click.Path(),
        default=None,
        help="Path to a YAML file of protocol variables.",
    )(f)
    return f


@click.command("run")
@click.argument("protocol", type=click.Path())
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate and print the run plan without executing it "
    "(equivalent to `bat dry-run`).",
)
@_common_run_options
def run(protocol, dry_run, run_name, variables, vars_file) -> None:
    """Run a protocol defined in PROTOCOL."""
    mode = "dry-run" if dry_run else "run"
    click.echo(f"bat {mode}: not implemented yet (protocol={protocol})")


@click.command("dry-run")
@click.argument("protocol", type=click.Path())
@_common_run_options
def dry_run(protocol, run_name, variables, vars_file) -> None:
    """Dry run a protocol defined in PROTOCOL (alias for `bat run --dry-run`)."""
    click.echo(f"bat dry-run: not implemented yet (protocol={protocol})")
