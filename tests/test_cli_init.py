"""Tests for the ``bat init`` command (CARD-014).

Verifies project scaffolding: directory structure, collision handling, and
that the generated ``protocol.yaml`` is valid YAML that successfully loads
through the real protocol loader (``bat validate`` is still a stub as of
this card, so ``load_protocol`` is the practical equivalent check).
"""

from pathlib import Path

import yaml
from click.testing import CliRunner

from bat.cli import main
from bat.engine.loader import load_protocol


def test_init_creates_expected_directory_structure():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["init", "my-project"])
        assert result.exit_code == 0

        project_dir = Path("my-project")
        assert project_dir.is_dir()
        assert (project_dir / "plugins").is_dir()
        assert (project_dir / "vars").is_dir()
        assert (project_dir / "runs").is_dir()
        assert (project_dir / "protocol.yaml").is_file()


def test_init_prints_success_message_with_next_steps():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["init", "my-project"])
        assert result.exit_code == 0
        assert "Created project: my-project/" in result.output
        assert "cd my-project" in result.output
        assert "bat run protocol.yaml --dry-run" in result.output


def test_init_existing_directory_raises_error():
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("my-project").mkdir()

        result = runner.invoke(main, ["init", "my-project"])

        assert result.exit_code != 0
        assert "already exists" in result.output


def test_init_existing_directory_does_not_overwrite():
    runner = CliRunner()
    with runner.isolated_filesystem():
        project_dir = Path("my-project")
        project_dir.mkdir()
        sentinel = project_dir / "keep-me.txt"
        sentinel.write_text("do not touch")

        result = runner.invoke(main, ["init", "my-project"])

        assert result.exit_code != 0
        assert sentinel.read_text() == "do not touch"
        assert not (project_dir / "protocol.yaml").exists()


def test_init_generated_protocol_is_valid_yaml():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["init", "my-project"])
        assert result.exit_code == 0

        content = Path("my-project", "protocol.yaml").read_text()
        data = yaml.safe_load(content)

        assert data["version"] == "0.1"
        assert data["vars"]["record"] == "100"
        assert "load" in [w["id"] for w in data["workflows"]]


def test_init_generated_protocol_loads_via_engine_loader():
    """Practical equivalent of "passes bat validate" while validate is a stub."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["init", "my-project"])
        assert result.exit_code == 0

        protocol = load_protocol(Path("my-project", "protocol.yaml"))

        assert protocol.version == "0.1"
        workflow = next(w for w in protocol.workflows if w.id == "load")
        step = workflow.steps[0]
        assert step.id == "load_record"
        assert step.module == "core.wfdb.read"
        assert step.params["path"] == "data/100"
        assert step.outputs["signal"] == "raw_signal"
