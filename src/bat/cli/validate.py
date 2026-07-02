"""``bat validate`` command.

Stub only — structural/schema validation of protocol files is implemented in
a later card.
"""

import click


@click.command("validate")
@click.argument("protocol", type=click.Path())
def validate(protocol) -> None:
    """Validate the structure of a protocol file at PROTOCOL."""
    click.echo(f"bat validate: not implemented yet (protocol={protocol})")
