"""Tests for static import resolution (CARD-004).

Covers the acceptance criteria from
``cards/backlog/CARD-004-static-import-resolution.md``: a protocol with no
imports passes through unchanged, imported vars/workflows are merged with
the correct precedence, relative import paths resolve correctly, circular
imports and variable expressions in import paths are rejected, and a
missing import file raises a descriptive error. Import resolution needs
real files on disk to resolve relative paths against, so these tests write
files into ``tmp_path``.
"""

from pathlib import Path

import pytest

from bat.engine.imports import ImportResolutionError, resolve_imports
from bat.engine.loader import ProtocolError, load_protocol


def write(base: Path, relpath: str, contents: str) -> Path:
    path = base / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    return path


def test_no_imports_passes_through_unchanged(tmp_path):
    raw = {
        "version": "0.1",
        "vars": {"record": "100"},
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        "module": "core.wfdb.read",
                    }
                ]
            }
        },
    }

    result, imported_vars = resolve_imports(raw, base_path=tmp_path)

    assert result == raw
    assert imported_vars == {}


def test_no_imports_key_present_still_returns_equivalent_dict(tmp_path):
    raw = {"version": "0.1", "workflows": {}}

    result, imported_vars = resolve_imports(raw, base_path=tmp_path)

    assert result == raw
    assert "imports" not in result
    assert imported_vars == {}


def test_imported_vars_merged_with_correct_precedence(tmp_path):
    write(
        tmp_path,
        "vars/common.yaml",
        """
vars:
  default_fs: 360
  record: "should_be_overridden"
""",
    )

    raw = {
        "version": "0.1",
        "imports": ["vars/common.yaml"],
        "vars": {"record": "100"},
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        "module": "core.wfdb.read",
                    }
                ]
            }
        },
    }

    result, imported_vars = resolve_imports(raw, base_path=tmp_path)

    # protocol-level "record" wins; imported "default_fs" is present.
    assert result["vars"] == {"default_fs": 360, "record": "100"}
    assert "imports" not in result
    # imported_vars reflects the *pre-override* imported values only.
    assert imported_vars == {"default_fs": 360, "record": "should_be_overridden"}


def test_imported_workflows_merged_into_protocol(tmp_path):
    write(
        tmp_path,
        "workflows/preprocess.yaml",
        """
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        outputs:
          raw_signal:
            type: signal
            format: wfdb
""",
    )

    raw = {
        "version": "0.1",
        "imports": ["workflows/preprocess.yaml"],
        "workflows": {
            "features": {
                "steps": [
                    {
                        "id": "export_signal",
                        "name": "Export signal",
                        "module": "core.wfdb.write",
                        "inputs": {"signal": {"artifact": "raw_signal"}},
                        "outputs": {
                            "exported_signal": {"type": "signal", "format": "wfdb"}
                        },
                    }
                ]
            }
        },
    }

    result, imported_vars = resolve_imports(raw, base_path=tmp_path)

    assert set(result["workflows"].keys()) == {"preprocess", "features"}
    assert result["workflows"]["preprocess"]["steps"][0]["id"] == "load_record"
    assert result["workflows"]["features"]["steps"][0]["id"] == "export_signal"
    assert imported_vars == {}


def test_relative_import_paths_resolve_from_declaring_file(tmp_path):
    # protocol.yaml (in tmp_path) imports "nested/child.yaml", which itself
    # imports "grandchild.yaml" relative to *its own* directory (nested/),
    # not relative to tmp_path.
    write(
        tmp_path,
        "nested/grandchild.yaml",
        """
vars:
  deep_var: "deep_value"
""",
    )
    write(
        tmp_path,
        "nested/child.yaml",
        """
imports:
  - grandchild.yaml
vars:
  mid_var: "mid_value"
""",
    )

    raw = {
        "version": "0.1",
        "imports": ["nested/child.yaml"],
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        "module": "core.wfdb.read",
                    }
                ]
            }
        },
    }

    result, imported_vars = resolve_imports(raw, base_path=tmp_path)

    assert result["vars"] == {"deep_var": "deep_value", "mid_var": "mid_value"}
    assert imported_vars == {"deep_var": "deep_value", "mid_var": "mid_value"}


def test_circular_imports_raise_error(tmp_path):
    write(tmp_path, "a.yaml", "imports:\n  - b.yaml\nvars:\n  a: 1\n")
    write(tmp_path, "b.yaml", "imports:\n  - a.yaml\nvars:\n  b: 2\n")

    raw = {
        "version": "0.1",
        "imports": ["a.yaml"],
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        "module": "core.wfdb.read",
                    }
                ]
            }
        },
    }

    with pytest.raises(ImportResolutionError, match="circular import"):
        resolve_imports(raw, base_path=tmp_path)


def test_import_path_with_variable_expression_raises_error(tmp_path):
    raw = {
        "version": "0.1",
        "imports": ["vars/{{ record }}.yaml"],
        "workflows": {},
    }

    with pytest.raises(ImportResolutionError, match=r"variable expression"):
        resolve_imports(raw, base_path=tmp_path)


def test_missing_import_file_raises_descriptive_error(tmp_path):
    raw = {
        "version": "0.1",
        "imports": ["does_not_exist.yaml"],
        "workflows": {},
    }

    with pytest.raises(ImportResolutionError, match="does_not_exist.yaml"):
        resolve_imports(raw, base_path=tmp_path)


def test_import_entry_must_be_a_string(tmp_path):
    raw = {
        "version": "0.1",
        "imports": [{"not": "a string"}],
        "workflows": {},
    }

    with pytest.raises(ImportResolutionError, match="static string"):
        resolve_imports(raw, base_path=tmp_path)


def test_imports_key_not_a_list_raises_error(tmp_path):
    raw = {
        "version": "0.1",
        "imports": "vars/common.yaml",
        "workflows": {},
    }

    with pytest.raises(ImportResolutionError, match="list"):
        resolve_imports(raw, base_path=tmp_path)


# --- Integration tests through load_protocol -------------------------------


def test_load_protocol_resolves_imports_end_to_end(tmp_path):
    write(
        tmp_path,
        "vars/common.yaml",
        """
vars:
  default_fs: 360
""",
    )
    write(
        tmp_path,
        "workflows/preprocess.yaml",
        """
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        outputs:
          raw_signal:
            type: signal
            format: wfdb
""",
    )

    protocol_yaml = """
version: "0.1"

imports:
  - vars/common.yaml
  - workflows/preprocess.yaml

vars:
  record: "100"

workflows:
  features:
    depends_on:
      - preprocess
    steps:
      - id: export_signal
        name: Export signal
        module: core.wfdb.write
        inputs:
          signal:
            artifact: raw_signal
        outputs:
          exported_signal:
            type: signal
            format: wfdb
"""
    protocol_path = write(tmp_path, "protocol.yaml", protocol_yaml)

    protocol = load_protocol(protocol_path)

    assert protocol.vars == {"default_fs": 360, "record": "100"}
    assert set(protocol.workflows.keys()) == {"preprocess", "features"}
    assert protocol.workflows["preprocess"].steps[0].id == "load_record"
    assert protocol.workflows["features"].depends_on == ["preprocess"]


def test_load_protocol_missing_import_raises_protocol_error(tmp_path):
    protocol_yaml = """
version: "0.1"

imports:
  - does_not_exist.yaml

workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
"""
    protocol_path = write(tmp_path, "protocol.yaml", protocol_yaml)

    with pytest.raises(ProtocolError, match="does_not_exist.yaml"):
        load_protocol(protocol_path)


def test_load_protocol_circular_import_raises_protocol_error(tmp_path):
    write(tmp_path, "a.yaml", "imports:\n  - b.yaml\nvars:\n  a: 1\n")
    write(tmp_path, "b.yaml", "imports:\n  - a.yaml\nvars:\n  b: 2\n")

    protocol_yaml = """
version: "0.1"

imports:
  - a.yaml

workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
"""
    protocol_path = write(tmp_path, "protocol.yaml", protocol_yaml)

    with pytest.raises(ProtocolError, match="circular import"):
        load_protocol(protocol_path)
