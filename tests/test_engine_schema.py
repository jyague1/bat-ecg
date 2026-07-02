"""Tests for the protocol schema and YAML loader (CARD-003).

Covers the acceptance criteria from
``cards/backlog/CARD-003-protocol-schema-parser.md``: a valid protocol
parses correctly, missing required fields / duplicate step ids / duplicate
artifact names / invalid ``depends_on`` references all raise descriptive
validation errors at parse time.
"""

import pytest
from pydantic import ValidationError

from bat.engine.loader import ProtocolError, load_protocol
from bat.engine.schema import Protocol

VALID_PROTOCOL = """
version: "0.1"

vars:
  record: "100"
  fs: 360

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

  features:
    depends_on:
      - preprocess
    steps:
      - id: export_signal
        name: Export cleaned signal
        module: core.wfdb.write
        inputs:
          signal:
            artifact: raw_signal
        params:
          path: "{{ record }}"
        outputs:
          exported_signal:
            type: signal
            format: wfdb
"""


def write_protocol(tmp_path, contents, filename="protocol.yaml"):
    path = tmp_path / filename
    path.write_text(contents)
    return path


def test_valid_protocol_parses_correctly(tmp_path):
    path = write_protocol(tmp_path, VALID_PROTOCOL)

    protocol = load_protocol(path)

    assert isinstance(protocol, Protocol)
    assert protocol.version == "0.1"
    assert protocol.vars == {"record": "100", "fs": 360}
    assert list(protocol.workflows.keys()) == ["preprocess", "features"]

    preprocess = protocol.workflows["preprocess"]
    assert preprocess.depends_on == []
    assert len(preprocess.steps) == 1
    load_step = preprocess.steps[0]
    assert load_step.id == "load_record"
    assert load_step.module == "core.wfdb.read"
    assert load_step.outputs["raw_signal"].type == "signal"
    assert load_step.outputs["raw_signal"].format == "wfdb"

    features = protocol.workflows["features"]
    assert features.depends_on == ["preprocess"]
    export_step = features.steps[0]
    assert export_step.inputs["signal"].artifact == "raw_signal"


def test_load_protocol_missing_file_raises(tmp_path):
    missing = tmp_path / "does_not_exist.yaml"

    with pytest.raises(ProtocolError):
        load_protocol(missing)


def test_missing_required_field_raises_validation_error():
    # `module` is required on Step but omitted here.
    data = {
        "version": "0.1",
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        # "module" omitted
                    }
                ]
            }
        },
    }

    with pytest.raises(ValidationError):
        Protocol.model_validate(data)


def test_missing_required_field_via_loader_raises_protocol_error(tmp_path):
    protocol_yaml = """
version: "0.1"

workflows:
  preprocess:
    steps:
      - id: load_record
        name: Load WFDB record
"""
    path = write_protocol(tmp_path, protocol_yaml)

    with pytest.raises(ProtocolError, match="module"):
        load_protocol(path)


def test_duplicate_step_ids_raise_validation_error():
    data = {
        "version": "0.1",
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        "module": "core.wfdb.read",
                        "outputs": {
                            "raw_signal": {"type": "signal", "format": "wfdb"}
                        },
                    },
                ]
            },
            "features": {
                "steps": [
                    {
                        "id": "load_record",  # duplicate across workflows
                        "name": "Export cleaned signal",
                        "module": "core.wfdb.write",
                        "outputs": {
                            "exported_signal": {"type": "signal", "format": "wfdb"}
                        },
                    },
                ]
            },
        },
    }

    with pytest.raises(ValidationError, match="duplicate step id"):
        Protocol.model_validate(data)


def test_duplicate_artifact_names_raise_validation_error():
    data = {
        "version": "0.1",
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        "module": "core.wfdb.read",
                        "outputs": {
                            "raw_signal": {"type": "signal", "format": "wfdb"}
                        },
                    },
                    {
                        "id": "load_record_again",
                        "name": "Load WFDB record again",
                        "module": "core.wfdb.read",
                        "outputs": {
                            "raw_signal": {  # duplicate artifact name
                                "type": "signal",
                                "format": "wfdb",
                            }
                        },
                    },
                ]
            }
        },
    }

    with pytest.raises(ValidationError, match="duplicate artifact name"):
        Protocol.model_validate(data)


def test_invalid_workflow_depends_on_raises_validation_error():
    data = {
        "version": "0.1",
        "workflows": {
            "features": {
                "depends_on": ["does_not_exist"],
                "steps": [
                    {
                        "id": "export_signal",
                        "name": "Export cleaned signal",
                        "module": "core.wfdb.write",
                    }
                ],
            }
        },
    }

    with pytest.raises(ValidationError, match="unknown workflow reference"):
        Protocol.model_validate(data)


def test_invalid_step_depends_on_raises_validation_error():
    data = {
        "version": "0.1",
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        "module": "core.wfdb.read",
                        "depends_on": ["nonexistent_step"],
                    }
                ]
            }
        },
    }

    with pytest.raises(ValidationError, match="unknown step reference"):
        Protocol.model_validate(data)


def test_unresolved_artifact_input_raises_validation_error():
    data = {
        "version": "0.1",
        "workflows": {
            "features": {
                "steps": [
                    {
                        "id": "export_signal",
                        "name": "Export cleaned signal",
                        "module": "core.wfdb.write",
                        "inputs": {
                            "signal": {"artifact": "never_declared"},
                        },
                    }
                ]
            }
        },
    }

    with pytest.raises(ValidationError, match="unknown artifact reference"):
        Protocol.model_validate(data)


def test_workflow_requires_at_least_one_step():
    data = {
        "version": "0.1",
        "workflows": {
            "preprocess": {"steps": []},
        },
    }

    with pytest.raises(ValidationError):
        Protocol.model_validate(data)


def test_invalid_artifact_type_raises_validation_error():
    data = {
        "version": "0.1",
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        "module": "core.wfdb.read",
                        "outputs": {
                            "raw_signal": {
                                "type": "not_a_real_type",
                                "format": "wfdb",
                            }
                        },
                    }
                ]
            }
        },
    }

    with pytest.raises(ValidationError):
        Protocol.model_validate(data)


def test_on_error_defaults_and_error_artifact():
    data = {
        "version": "0.1",
        "workflows": {
            "preprocess": {
                "steps": [
                    {
                        "id": "load_record",
                        "name": "Load WFDB record",
                        "module": "core.wfdb.read",
                        "on_error": {
                            "action": "continue",
                            "output": {
                                "load_failure": {"type": "error", "format": "yaml"}
                            },
                        },
                    }
                ]
            }
        },
    }

    protocol = Protocol.model_validate(data)
    step = protocol.workflows["preprocess"].steps[0]
    assert step.on_error.action == "continue"
    assert step.on_error.output["load_failure"].type == "error"


def test_load_protocol_invalid_yaml_raises_protocol_error(tmp_path):
    path = write_protocol(tmp_path, "version: '0.1'\nworkflows: [unterminated")

    with pytest.raises(ProtocolError):
        load_protocol(path)


def test_load_protocol_non_mapping_top_level_raises_protocol_error(tmp_path):
    path = write_protocol(tmp_path, "- just\n- a\n- list\n")

    with pytest.raises(ProtocolError):
        load_protocol(path)
