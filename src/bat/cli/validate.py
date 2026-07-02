"""``bat validate`` command.

Performs a quick structural check of a protocol YAML file (CARD-015): it
parses the YAML, resolves static imports, and validates the top-level
schema shape -- without executing modules, checking that input files
exist, or fully resolving all dependencies. See
:func:`bat.engine.validation.validate_protocol` for the underlying check.
"""

import click

from bat.engine.validation import validate_protocol


@click.command("validate")
@click.argument("protocol", type=click.Path())
def validate(protocol) -> None:
    """Validate the structure of a protocol file at PROTOCOL."""
    errors = validate_protocol(protocol)

    if not errors:
        click.echo(f"{protocol}: OK")
        return

    click.echo(f"{protocol}: INVALID")
    click.echo()
    click.echo("Errors:")
    for error in errors:
        click.echo(f"  - {error}")
    raise SystemExit(1)
