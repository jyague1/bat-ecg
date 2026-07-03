"""Tests for the ``bat validate`` command (CARD-015).

These exercise the CLI wiring on top of
:func:`bat.engine.validation.validate_protocol`: output formatting and exit
codes for valid and invalid protocols.
"""

from pathlib import Path

from click.testing import CliRunner

from bat.cli import main

VALID_PROTOCOL = """\
version: "0.1"
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


def write(name: str, content: str) -> Path:
    path = Path(name)
    path.write_text(content)
    return path


def test_valid_protocol_prints_ok_and_exits_zero():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", VALID_PROTOCOL)

        result = runner.invoke(main, ["validate", "protocol.yaml"])

        assert result.exit_code == 0
        assert result.output.strip() == "protocol.yaml: OK"


def test_missing_module_field_prints_error_and_exits_one():
    protocol = """\
version: "0.1"
workflows:
  - id: preprocess
    steps:
      - id: load_record
        name: Load WFDB record
"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", protocol)

        result = runner.invoke(main, ["validate", "protocol.yaml"])

        assert result.exit_code == 1
        assert "protocol.yaml: INVALID" in result.output
        assert "Errors:" in result.output
        assert "missing required field 'module'" in result.output


def test_duplicate_step_ids_prints_error_and_exits_one():
    protocol = """\
version: "0.1"
workflows:
  - id: preprocess
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
      - id: load_record
        name: Load WFDB record again
        module: core.wfdb.read
"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", protocol)

        result = runner.invoke(main, ["validate", "protocol.yaml"])

        assert result.exit_code == 1
        assert "protocol.yaml: INVALID" in result.output
        assert "duplicate step id" in result.output


def test_invalid_depends_on_prints_error_and_exits_one():
    protocol = """\
version: "0.1"
workflows:
  - id: preprocess
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
  - id: features
    depends_on: [nonexistent]
    steps:
      - id: extract_features
        name: Extract features
        module: core.features.extract
"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", protocol)

        result = runner.invoke(main, ["validate", "protocol.yaml"])

        assert result.exit_code == 1
        assert "protocol.yaml: INVALID" in result.output
        assert "does not refer to a known workflow" in result.output


def test_nonexistent_file_prints_error_and_exits_one():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["validate", "missing.yaml"])

        assert result.exit_code == 1
        assert "missing.yaml: INVALID" in result.output
        assert "not found" in result.output


def test_invalid_yaml_prints_error_and_exits_one():
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", "version: [unterminated\n  - broken")

        result = runner.invoke(main, ["validate", "protocol.yaml"])

        assert result.exit_code == 1
        assert "protocol.yaml: INVALID" in result.output


def test_two_independent_structural_problems_both_reported():
    """Acceptance criteria: all structural errors are reported, not just the first."""
    protocol = """\
version: "0.1"
workflows:
  - id: preprocess
    steps:
      - id: load_record
        name: Load WFDB record
  - id: features
    depends_on: [nonexistent]
    steps:
      - id: extract_features
        name: Extract features
        module: core.features.extract
"""
    runner = CliRunner()
    with runner.isolated_filesystem():
        write("protocol.yaml", protocol)

        result = runner.invoke(main, ["validate", "protocol.yaml"])

        assert result.exit_code == 1
        assert "missing required field 'module'" in result.output
        assert "does not refer to a known workflow" in result.output


def test_bat_init_generated_protocol_passes_validate():
    """The starter protocol from `bat init` (CARD-014) should validate cleanly."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        init_result = runner.invoke(main, ["init", "my-project"])
        assert init_result.exit_code == 0

        result = runner.invoke(
            main, ["validate", str(Path("my-project", "protocol.yaml"))]
        )

        assert result.exit_code == 0
        assert "OK" in result.output
