"""Parity guard between the two protocol validators (improvement).

BAT validates protocols in two places that independently encode the same
structural rules:

- :func:`bat.engine.loader.load_protocol` — builds a fully-substituted,
  fully-validated :class:`~bat.engine.schema.Protocol` (Pydantic model
  validators + cycle detection), stopping at the first error.
- :func:`bat.engine.validation.validate_protocol` — a raw-dict walk that
  skips variable substitution and collects *all* structural errors, backing
  ``bat validate``.

They can silently drift. This test pins the shared rules: for each case,
``load_protocol`` raising and ``validate_protocol`` returning ≥1 error must
agree.

Two intentional asymmetries are deliberately *not* treated as parity
violations, and are covered separately below:

1. Undefined ``{{ var }}`` references fail ``load_protocol`` (which
   substitutes) but not ``validate_protocol`` (which does not).
2. ``validate_protocol`` checks a structural subset -- e.g. it does not do
   Pydantic type coercion or ``extra="forbid"`` -- so some inputs that
   ``load_protocol`` rejects are accepted by ``validate_protocol``. The
   shared-rule cases below avoid that subset boundary.
"""

from __future__ import annotations

import pytest

from bat.engine.executor import CycleError
from bat.engine.loader import ProtocolError, load_protocol
from bat.engine.validation import validate_protocol

# Cases both validators must agree are VALID (no undefined vars, so
# load_protocol's substitution also succeeds).
VALID_CASES = {
    "minimal": """\
version: "0.1"
workflows:
  - id: main
    steps:
      - id: s1
        name: Step one
        module: core.wfdb.read
        outputs:
          signal: out1
""",
    "two_workflows_with_deps": """\
version: "0.1"
workflows:
  - id: a
    steps:
      - id: s1
        name: One
        module: core.wfdb.read
        outputs:
          signal: art1
  - id: b
    depends_on: [a]
    steps:
      - id: s2
        name: Two
        module: core.wfdb.write
        depends_on: []
        inputs:
          signal: art1
        outputs:
          exported_signal: art2
""",
}

# Cases that violate a rule BOTH validators enforce: load_protocol must
# raise and validate_protocol must return at least one error.
SHARED_INVALID_CASES = {
    "missing_version": """\
workflows:
  - id: main
    steps:
      - id: s1
        name: One
        module: core.wfdb.read
""",
    "empty_workflows": """\
version: "0.1"
workflows: []
""",
    "missing_module": """\
version: "0.1"
workflows:
  - id: main
    steps:
      - id: s1
        name: One
""",
    "duplicate_step_ids": """\
version: "0.1"
workflows:
  - id: main
    steps:
      - id: dup
        name: One
        module: core.wfdb.read
  - id: other
    steps:
      - id: dup
        name: Two
        module: core.wfdb.read
""",
    "duplicate_artifact_names": """\
version: "0.1"
workflows:
  - id: main
    steps:
      - id: s1
        name: One
        module: core.wfdb.read
        outputs:
          signal: shared
      - id: s2
        name: Two
        module: core.wfdb.read
        outputs:
          signal: shared
""",
    "duplicate_workflow_ids": """\
version: "0.1"
workflows:
  - id: main
    steps:
      - id: s1
        name: One
        module: core.wfdb.read
  - id: main
    steps:
      - id: s2
        name: Two
        module: core.wfdb.read
""",
    "bad_workflow_depends_on": """\
version: "0.1"
workflows:
  - id: main
    depends_on: [nonexistent]
    steps:
      - id: s1
        name: One
        module: core.wfdb.read
""",
    "bad_step_depends_on": """\
version: "0.1"
workflows:
  - id: main
    steps:
      - id: s1
        name: One
        module: core.wfdb.read
        depends_on: [nonexistent]
""",
    "workflow_cycle": """\
version: "0.1"
workflows:
  - id: first
    depends_on: [second]
    steps:
      - id: s1
        name: One
        module: core.wfdb.read
  - id: second
    depends_on: [first]
    steps:
      - id: s2
        name: Two
        module: core.wfdb.read
""",
    "step_cycle": """\
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
""",
}


def _write(tmp_path, content):
    path = tmp_path / "protocol.yaml"
    path.write_text(content)
    return path


@pytest.mark.parametrize("name", sorted(VALID_CASES))
def test_valid_cases_agree(name, tmp_path):
    path = _write(tmp_path, VALID_CASES[name])

    errors = validate_protocol(path)
    assert errors == [], f"validate_protocol reported errors for a valid case: {errors}"

    # load_protocol must not raise on a valid, fully-defined protocol.
    load_protocol(path)


@pytest.mark.parametrize("name", sorted(SHARED_INVALID_CASES))
def test_shared_invalid_cases_agree(name, tmp_path):
    path = _write(tmp_path, SHARED_INVALID_CASES[name])

    errors = validate_protocol(path)
    assert errors, f"validate_protocol accepted an invalid case ({name})"

    with pytest.raises((ProtocolError, CycleError)):
        load_protocol(path)


def test_undefined_variable_is_a_documented_asymmetry(tmp_path):
    """An undefined ``{{ var }}`` fails load_protocol but not validate_protocol.

    This is intended (validate_protocol does not substitute variables), and
    exists to document the boundary the parity cases above avoid.
    """
    content = """\
version: "0.1"
workflows:
  - id: main
    steps:
      - id: s1
        name: One
        module: core.wfdb.read
        params:
          path: "data/{{ undefined_var }}"
        outputs:
          signal: out1
"""
    path = _write(tmp_path, content)

    assert validate_protocol(path) == []
    with pytest.raises(ProtocolError):
        load_protocol(path)
