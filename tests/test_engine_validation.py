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
  - id: preprocess
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}"
        outputs:
          signal: raw_signal
      - id: filter_record
        name: Filter record
        module: core.wfdb.filter
        depends_on: [load_record]
        inputs:
          signal: raw_signal
        outputs:
          signal: filtered_signal
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
  - id: preprocess
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
  - id: preprocess
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
  - id: preprocess
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
  - id: preprocess
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
  - id: preprocess
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        outputs:
          signal: raw_signal
      - id: load_record_again
        name: Load WFDB record again
        module: core.wfdb.read
        outputs:
          signal: raw_signal
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any(
        "duplicate artifact name" in e and "raw_signal" in e for e in errors
    )


def test_missing_version_reported(tmp_path):
    protocol = """\
workflows:
  - id: preprocess
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
workflows: []
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any("workflows" in e and "non-empty" in e for e in errors)


def test_workflow_with_no_steps_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  - id: preprocess
    steps: []
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any("steps" in e and "at least one step" in e for e in errors)


def test_workflow_missing_id_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  - steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any("workflows[0]" in e and "missing required field 'id'" in e for e in errors)


def test_duplicate_workflow_ids_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  - id: preprocess
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
  - id: preprocess
    steps:
      - id: load_record_again
        name: Load WFDB record again
        module: core.wfdb.read
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)
    assert any("duplicate workflow id" in e and "preprocess" in e for e in errors)


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
  - id: imported
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
  - id: preprocess
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
  - id: preprocess
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


# --------------------------------------------------------------------------
# Dependency cycles (improvement: cycle detection)
# --------------------------------------------------------------------------


def test_workflow_dependency_cycle_is_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  - id: first
    depends_on: [second]
    steps:
      - id: s1
        name: S1
        module: core.wfdb.read
  - id: second
    depends_on: [first]
    steps:
      - id: s2
        name: S2
        module: core.wfdb.read
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)

    assert any(
        e.startswith("workflows: dependency cycle detected:") for e in errors
    ), f"workflow cycle not reported in {errors}"


def test_step_dependency_cycle_is_reported(tmp_path):
    protocol = """\
version: "0.1"
workflows:
  - id: main
    steps:
      - id: a
        name: A
        module: core.wfdb.read
        depends_on: [b]
      - id: b
        name: B
        module: core.wfdb.read
        depends_on: [a]
"""
    path = write(tmp_path, "protocol.yaml", protocol)
    errors = validate_protocol(path)

    assert any(
        e.startswith("workflows.main.steps: dependency cycle detected:") for e in errors
    ), f"step cycle not reported in {errors}"


def test_valid_protocol_has_no_cycle_errors(tmp_path):
    path = write(tmp_path, "protocol.yaml", VALID_PROTOCOL)
    errors = validate_protocol(path)

    assert not any("cycle detected" in e for e in errors), errors
