"""Smoke tests for the CLI stubs wired up in CARD-002.

These only check that each command/subcommand is reachable, exits 0, and
prints a "not implemented" stub message — no business logic is exercised.

``run``/``dry-run`` grew real behavior in CARD-016 (see
``tests/test_cli_run.py``), so their stub-specific assertions have been
removed here; only the generic ``--help`` reachability checks remain.
"""

from click.testing import CliRunner

from bat.cli import main


def test_run_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["run", "--help"])
    assert result.exit_code == 0


def test_dry_run_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["dry-run", "--help"])
    assert result.exit_code == 0


def test_validate_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "--help"])
    assert result.exit_code == 0


def test_plugins_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["plugins", "--help"])
    assert result.exit_code == 0


def test_plugins_list_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["plugins", "list", "--help"])
    assert result.exit_code == 0


def test_plugins_docs_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["plugins", "docs", "--help"])
    assert result.exit_code == 0


def test_init_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["init", "--help"])
    assert result.exit_code == 0


def test_plugins_list_stub_exits_zero():
    runner = CliRunner()
    result = runner.invoke(
        main, ["plugins", "list", "--verbose", "--module", "foo.bar"]
    )
    assert result.exit_code == 0
    assert "not implemented" in result.output


def test_plugins_docs_stub_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["plugins", "docs"])
    assert result.exit_code == 0
    assert "not implemented" in result.output


def test_run_missing_protocol_arg_errors():
    runner = CliRunner()
    result = runner.invoke(main, ["run"])
    assert result.exit_code != 0


def test_init_missing_project_name_errors():
    runner = CliRunner()
    result = runner.invoke(main, ["init"])
    assert result.exit_code != 0
