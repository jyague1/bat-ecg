"""Tests for the variable system (CARD-005).

Covers the acceptance criteria from
``cards/backlog/CARD-005-variable-system.md``: ``{{ var }}`` substitution in
string values (including whitespace variants and partial substitution),
variable precedence across CLI vars / CLI vars-file / protocol vars /
imported vars, missing-variable errors with field location, and that
non-string values are left untouched. Also covers end-to-end wiring through
``load_protocol``.
"""

from pathlib import Path

import pytest

from bat.engine.loader import ProtocolError, load_protocol
from bat.engine.variables import (
    VariableResolutionError,
    build_var_context,
    substitute_vars,
)


def write(base: Path, relpath: str, contents: str) -> Path:
    path = base / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    return path


# --- substitute_vars ---------------------------------------------------


def test_substitution_replaces_var_in_string_values():
    raw = {"params": {"path": "{{ record }}"}}

    result = substitute_vars(raw, {"record": "100"})

    assert result == {"params": {"path": "100"}}


def test_whitespace_variants_both_work():
    raw = {"a": "{{ record }}", "b": "{{record}}"}

    result = substitute_vars(raw, {"record": "100"})

    assert result == {"a": "100", "b": "100"}


def test_partial_substitution_within_a_string():
    raw = {"path": "data/{{ record }}/ecg"}

    result = substitute_vars(raw, {"record": "100"})

    assert result == {"path": "data/100/ecg"}


def test_non_string_values_are_not_modified():
    raw = {
        "count": 3,
        "fs": 360.0,
        "enabled": True,
        "nothing": None,
        "items": [1, 2.5, "{{ record }}", False],
        "nested": {"count": 7, "path": "{{ record }}"},
    }

    result = substitute_vars(raw, {"record": "100"})

    assert result["count"] == 3
    assert result["fs"] == 360.0
    assert result["enabled"] is True
    assert result["nothing"] is None
    assert result["items"] == [1, 2.5, "100", False]
    assert result["nested"] == {"count": 7, "path": "100"}


def test_missing_variable_raises_descriptive_error_with_field_location():
    raw = {
        "workflows": {
            "preprocess": {
                "steps": [{"params": {"path": "data/{{ record }}"}}],
            }
        }
    }

    with pytest.raises(VariableResolutionError) as exc_info:
        substitute_vars(raw, {})

    message = str(exc_info.value)
    assert "record" in message
    assert "workflows.preprocess.steps.[0].params.path" in message


def test_multiple_vars_in_one_string():
    raw = {"path": "{{ record }}_{{ fs }}"}

    result = substitute_vars(raw, {"record": "100", "fs": "360"})

    assert result == {"path": "100_360"}


# --- build_var_context precedence --------------------------------------


def test_cli_vars_override_protocol_vars():
    context = build_var_context(
        protocol_vars={"record": "100"},
        imported_vars={},
        vars_file=None,
        cli_vars={"record": "999"},
    )

    assert context["record"] == "999"


def test_cli_vars_file_overrides_protocol_vars(tmp_path):
    vars_file = write(tmp_path, "vars.yaml", 'record: "200"\nfs: 500\n')

    context = build_var_context(
        protocol_vars={"record": "100", "fs": 360},
        imported_vars={},
        vars_file=vars_file,
        cli_vars={},
    )

    assert context["record"] == "200"
    assert context["fs"] == 500


def test_protocol_vars_override_imported_vars():
    context = build_var_context(
        protocol_vars={"record": "100"},
        imported_vars={"record": "should_be_overridden", "default_fs": 360},
        vars_file=None,
        cli_vars={},
    )

    assert context == {"record": "100", "default_fs": 360}


def test_full_precedence_chain(tmp_path):
    vars_file = write(tmp_path, "vars.yaml", "record: file_record\nfs: 500\n")

    context = build_var_context(
        protocol_vars={"record": "protocol_record", "fs": 360, "gain": 200},
        imported_vars={
            "record": "imported_record",
            "fs": 999,
            "gain": 1,
            "site": "imported_site",
        },
        vars_file=vars_file,
        cli_vars={"record": "cli_record"},
    )

    # CLI > vars-file > protocol > imported
    assert context["record"] == "cli_record"
    assert context["fs"] == 500  # from vars-file, beats protocol vars
    assert context["gain"] == 200  # from protocol vars, beats imported
    assert context["site"] == "imported_site"  # only source is imports


def test_vars_file_not_found_raises_error(tmp_path):
    with pytest.raises(VariableResolutionError, match="not found"):
        build_var_context(
            protocol_vars={},
            imported_vars={},
            vars_file=tmp_path / "does_not_exist.yaml",
            cli_vars={},
        )


# --- end-to-end through load_protocol -----------------------------------


def test_load_protocol_substitutes_protocol_vars(tmp_path):
    protocol_yaml = """
version: "0.1"

vars:
  record: "100"

workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}/ecg"
"""
    path = write(tmp_path, "protocol.yaml", protocol_yaml)

    protocol = load_protocol(path)

    step = protocol.workflows["preprocess"].steps[0]
    assert step.params["path"] == "data/100/ecg"


def test_load_protocol_cli_vars_override_protocol_vars(tmp_path):
    protocol_yaml = """
version: "0.1"

vars:
  record: "100"

workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}/ecg"
"""
    path = write(tmp_path, "protocol.yaml", protocol_yaml)

    protocol = load_protocol(path, cli_vars={"record": "200"})

    step = protocol.workflows["preprocess"].steps[0]
    assert step.params["path"] == "data/200/ecg"


def test_load_protocol_vars_file_overrides_protocol_vars(tmp_path):
    protocol_yaml = """
version: "0.1"

vars:
  record: "100"

workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}/ecg"
"""
    path = write(tmp_path, "protocol.yaml", protocol_yaml)
    vars_file = write(tmp_path, "vars/mitdb.yaml", 'record: "300"\n')

    protocol = load_protocol(path, vars_file=vars_file)

    step = protocol.workflows["preprocess"].steps[0]
    assert step.params["path"] == "data/300/ecg"


def test_load_protocol_imported_vars_used_when_not_overridden(tmp_path):
    write(tmp_path, "vars/common.yaml", "vars:\n  fs: 360\n")
    protocol_yaml = """
version: "0.1"

imports:
  - vars/common.yaml

vars:
  record: "100"

workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}"
          fs: "{{ fs }}"
"""
    path = write(tmp_path, "protocol.yaml", protocol_yaml)

    protocol = load_protocol(path)

    step = protocol.workflows["preprocess"].steps[0]
    assert step.params["path"] == "data/100"
    assert step.params["fs"] == "360"


def test_load_protocol_missing_variable_raises_protocol_error(tmp_path):
    protocol_yaml = """
version: "0.1"

workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}"
"""
    path = write(tmp_path, "protocol.yaml", protocol_yaml)

    with pytest.raises(ProtocolError, match="record"):
        load_protocol(path)
