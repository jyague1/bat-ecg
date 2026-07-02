"""``bat run`` and ``bat dry-run`` commands (CARD-016).

Both commands share the same argument/option surface: a protocol path plus
options for naming the run and supplying protocol variables. ``bat run
--dry-run`` and ``bat dry-run`` are equivalent -- they share the same
underlying :func:`~bat.engine.runner.dry_run_protocol` call, but are kept as
separate ``click`` commands (rather than one calling the other) so that
``bat dry-run --help`` shows a signature without the (implicit,
always-true) ``--dry-run`` flag.

All business logic (loading the protocol, discovering plugins, validating
module references, creating the run directory, executing the DAG, writing
provenance) lives in :mod:`bat.engine.runner`; this module is only
responsible for argument parsing and formatting output.
"""

from __future__ import annotations

from pathlib import Path

import click

from bat.engine.executor import CycleError
from bat.engine.loader import ProtocolError
from bat.engine.run import RunError
from bat.engine.runner import (
    ExecutionPlan,
    MissingModulesError,
    RunResult,
    dry_run_protocol,
    run_protocol,
)
from bat.plugins.discovery import PluginDiscoveryError

#: Exceptions raised by the runner for failures that happen *before* (or
#: independent of) actual step execution -- surfaced to the user as a plain
#: error message and exit code 1.
_PREFLIGHT_ERRORS = (ProtocolError, PluginDiscoveryError, RunError, CycleError)


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


def _parse_cli_vars(variables: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated ``--var KEY=VALUE`` options into a flat dict."""
    cli_vars: dict[str, str] = {}
    for item in variables:
        if "=" not in item:
            raise click.ClickException(
                f"Invalid --var value {item!r}: expected KEY=VALUE"
            )
        key, _, value = item.partition("=")
        if not key:
            raise click.ClickException(
                f"Invalid --var value {item!r}: expected KEY=VALUE"
            )
        cli_vars[key] = value
    return cli_vars


def _display_run_dir(run_ctx) -> str:
    """Render a run directory the way the card's examples show it, e.g.
    ``runs/2026-06-23_153012/``."""
    return f"{run_ctx.run_dir}/"


def _print_missing_modules(exc: MissingModulesError) -> None:
    click.echo("Error: protocol references modules not found in the plugin registry:")
    for module in exc.missing_modules:
        click.echo(f"  - {module}")


def _print_execution_plan(protocol: str, plan: ExecutionPlan) -> None:
    click.echo(f"Dry run: {protocol}")
    click.echo(f"Run directory: {_display_run_dir(plan.run_ctx)}")
    click.echo()
    click.echo("Execution plan:")
    for workflow_id, steps in plan.workflows:
        click.echo(f"  [workflow] {workflow_id}")
        for step_id, module in steps:
            click.echo(f"    [step] {step_id:<20} {module}")
        click.echo()


def _print_run_summary(result: RunResult) -> None:
    run_dir = _display_run_dir(result.run_ctx)

    if result.succeeded:
        click.echo(f"Run complete: {run_dir}")
        click.echo(f"  Workflows: {result.workflow_count}")
        click.echo(f"  Steps:     {result.step_count}")
        click.echo(f"  Artifacts: {result.artifact_count}")
        click.echo(f"  Duration:  {result.duration_seconds:.1f}s")
        return

    click.echo(f"Run failed: {run_dir}")
    if result.failed_step:
        click.echo(
            f"  Failed at step: {result.failed_step} "
            f"(workflow: {result.failed_workflow})"
        )
    click.echo(f"  See logs: {run_dir}logs/run.log")


def _do_dry_run(protocol: str, run_name, variables, vars_file) -> None:
    cli_vars = _parse_cli_vars(variables)
    try:
        plan = dry_run_protocol(
            Path(protocol),
            run_name=run_name,
            vars_file=vars_file,
            cli_vars=cli_vars,
        )
    except MissingModulesError as exc:
        _print_missing_modules(exc)
        raise SystemExit(1) from exc
    except _PREFLIGHT_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    _print_execution_plan(protocol, plan)


def _do_run(protocol: str, run_name, variables, vars_file) -> None:
    cli_vars = _parse_cli_vars(variables)
    try:
        result = run_protocol(
            Path(protocol),
            run_name=run_name,
            vars_file=vars_file,
            cli_vars=cli_vars,
        )
    except MissingModulesError as exc:
        _print_missing_modules(exc)
        raise SystemExit(1) from exc
    except _PREFLIGHT_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    _print_run_summary(result)
    if not result.succeeded:
        raise SystemExit(1)


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
    if dry_run:
        _do_dry_run(protocol, run_name, variables, vars_file)
    else:
        _do_run(protocol, run_name, variables, vars_file)


@click.command("dry-run")
@click.argument("protocol", type=click.Path())
@_common_run_options
def dry_run(protocol, run_name, variables, vars_file) -> None:
    """Dry run a protocol defined in PROTOCOL (alias for `bat run --dry-run`)."""
    _do_dry_run(protocol, run_name, variables, vars_file)
