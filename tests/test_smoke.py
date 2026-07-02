"""Smoke test: the package installs and the CLI is invokable."""

from click.testing import CliRunner

import bat
from bat.cli import main


def test_import_bat():
    assert bat.__version__


def test_cli_help_exits_zero():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
