"""Tests for :func:`bat.engine.validation.validate_protocol` (CARD-015).

``validate_protocol`` is a deliberately separate, simpler validation pass
from :func:`bat.engine.loader.load_protocol`: it walks the raw, imports-
resolved-but-not-substituted protocol dict directly, collecting *all*
structural errors instead of stopping at the first one, and it does not
require ``{{ var }}`` tokens to be defined.
"""

from pathlib import Path

from bat.engine.validation import validate_protocol

VALID_PROTOCOL = """\
version: "0.1"
workflows:
  preprocess:
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
      - id: filter_record
        name: Filter record
        module: core.wfdb.filter
        depends_on: [load_record]
        inputs:
          signal:
            artifact: raw_signal
        outputs:
          filtered_signal:
            type: signal
            format: wfdb
"""


def write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_valid_protocol_returns_no_errors(tmp_path):
    path = write(tmp_path, "protocol.yaml", VALID_PROTOCOL)
    assert validate_protocol(path) == []


def test_undefined_var_tokens_do_not_cause_errors(tmp_path):
    """Validation must not require {{ var }} tokens to be defined."""
    protocol = """\
version: "0.1"
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}"
          extra: "{{ undefined_var_never_declared_anywhere }}"
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    assert validate_protocol(path) == []


def test_missing_module_field_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any(
        "missing required field 'module'" in e and "steps[0]" in e for e in errors
    )


def test_duplicate_step_ids_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
      - id: load_record
        name: Load WFDB record again
        module: core.wfdb.read
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any("duplicate step id" in e and "load_record" in e for e in errors)


def test_invalid_workflow_depends_on_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
  features:
    depends_on: [nonexistent]
    steps:
      - id: extract_features
        name: Extract features
        module: core.features.extract
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any(
        "workflows.features.depends_on[0]" in e
        and "nonexistent" in e
        and "does not refer to a known workflow" in e
        for e in errors
    )


def test_invalid_step_depends_on_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        depends_on: [nonexistent_step]
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any(
        "depends_on[0]" in e
        and "nonexistent_step" in e
        and "does not refer to a known step" in e
        for e in errors
    )


def test_duplicate_artifact_names_reported(tmp_path):
    protocol = """\
version: "0.1"
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
      - id: load_record_again
        name: Load WFDB record again
        module: core.wfdb.read
        outputs:
          raw_signal:
            type: signal
            format: wfdb
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any(
        "duplicate artifact name" in e and "raw_signal" in e for e in errors
    )


def test_missing_version_reported(tmp_path):
    protocol = """\
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any("version" in e for e in errors)


def test_empty_workflows_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows: {}
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any("workflows" in e and "non-empty" in e for e in errors)


def test_workflow_with_no_steps_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  preprocess:
    steps: []
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any("steps" in e and "at least one step" in e for e in errors)


def test_nonexistent_file_reported(tmp_path):
    path = tmp_path / "does-not-exist.yaml"
    errors = validate_protocol(path)
    assert len(errors) == 1
    assert "not found" in errors[0]


def test_invalid_yaml_reported(tmp_path):
    path = write(tmp_path, "protocol.yaml", "version: [unterminated\n  - broken")
    errors = validate_protocol(path)
    assert len(errors) == 1
    assert "Invalid YAML" in errors[0] or "YAML" in errors[0]


def test_imports_are_resolved_before_validation(tmp_path):
    """Imported workflows are inlined, so their steps are validated too."""
    write(
        tmp_path,
        "shared.yaml",
        """\
workflows:
  imported:
    steps:
      - id: imported_step
        name: Imported step
""",
    )
    protocol = """\
version: "0.1"
imports:
  - shared.yaml
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    # The imported workflow's step is missing 'module' and should be caught.
    assert any(
        "missing required field 'module'" in e and "imported" in e for e in errors
    )


def test_import_resolution_failure_reported(tmp_path):
    protocol = """\
version: "0.1"
imports:
  - does-not-exist.yaml
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert len(errors) == 1
    assert "import" in errors[0].lower()


def test_two_independent_structural_problems_both_reported(tmp_path):
    """Acceptance criteria: all structural errors are reported, not just the first."""
    protocol = """\
version: "0.1"
workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
  features:
    depends_on: [nonexistent]
    steps:
      - id: extract_features
        name: Extract features
        module: core.features.extract
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)

    missing_module_error = any(
        "missing required field 'module'" in e and "preprocess" in e for e in errors
    )
    bad_depends_on_error = any(
        "workflows.features.depends_on[0]" in e
        and "nonexistent" in e
        and "does not refer to a known workflow" in e
        for e in errors
    )

    assert missing_module_error, f"missing-module error not found in {errors}"
    assert bad_depends_on_error, f"bad depends_on error not found in {errors}"
    assert len(errors) >= 2
