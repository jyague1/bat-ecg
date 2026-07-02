"""Tests for the plugin module schema (CARD-007).

Covers the acceptance criteria from
``cards/backlog/CARD-007-plugin-interface-schema.md`` that are local to
``bat.plugins.schema`` itself (not discovery-time enforcement, which is
covered in ``tests/test_plugins_discovery.py``):

- ``InputField``/``OutputField`` carry artifact type and format metadata,
  recoverable from the resulting Pydantic model's ``model_fields``.
- ``ModuleSchema.to_json_schema()`` returns a valid JSON Schema-flavored
  dict for any conforming schema.
- Citation validation helper (``citations_are_valid``) accepts the literal
  ``"none"`` and non-empty ``list[str]``, and rejects everything else.
- Invalid ``artifact_type`` values are rejected.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from bat.plugins.schema import (
    ARTIFACT_TYPES,
    DEFAULT_ARTIFACT_FORMATS,
    InputField,
    ModuleSchema,
    OutputField,
    citations_are_valid,
)


class DetectRPeaksSchema(ModuleSchema):
    class Meta:
        name = "lab.ecg.detect_rpeaks"
        description = "Detect R-peaks in an ECG signal using Pan-Tompkins."
        citations = ["Pan J, Tompkins W. A real-time QRS detection algorithm. IEEE TBME. 1985."]
        examples = [{"module": "lab.ecg.detect_rpeaks", "params": {"min_distance_ms": 200.0}}]

    class Params(BaseModel):
        min_distance_ms: float = 200.0

    class Inputs(BaseModel):
        signal: InputField(artifact_type="signal", artifact_format="wfdb")

    class Outputs(BaseModel):
        rpeaks: OutputField(artifact_type="annotations", artifact_format="wfdb")


class NoCitationsIoSchema(ModuleSchema):
    class Meta:
        name = "lab.io.load"
        description = "Load a signal from disk."
        citations = "none"

    class Params(BaseModel):
        pass

    class Inputs(BaseModel):
        pass

    class Outputs(BaseModel):
        signal: OutputField(artifact_type="signal")  # default format


# --- InputField / OutputField ------------------------------------------


def test_input_field_carries_artifact_type_and_format():
    field = DetectRPeaksSchema.Inputs.model_fields["signal"]
    assert field.json_schema_extra == {"artifact_type": "signal", "artifact_format": "wfdb"}


def test_output_field_carries_artifact_type_and_format():
    field = DetectRPeaksSchema.Outputs.model_fields["rpeaks"]
    assert field.json_schema_extra == {"artifact_type": "annotations", "artifact_format": "wfdb"}


def test_output_field_uses_default_format_when_omitted():
    field = NoCitationsIoSchema.Outputs.model_fields["signal"]
    assert field.json_schema_extra["artifact_type"] == "signal"
    assert field.json_schema_extra["artifact_format"] == DEFAULT_ARTIFACT_FORMATS["signal"]


@pytest.mark.parametrize("artifact_type", sorted(ARTIFACT_TYPES))
def test_default_format_defined_for_every_artifact_type(artifact_type):
    assert artifact_type in DEFAULT_ARTIFACT_FORMATS


def test_invalid_artifact_type_rejected():
    with pytest.raises(ValueError):
        InputField(artifact_type="not-a-real-type", artifact_format="wfdb")


# --- to_json_schema -----------------------------------------------------


def test_to_json_schema_returns_dict_with_meta_and_sections():
    schema = DetectRPeaksSchema.to_json_schema()

    assert isinstance(schema, dict)
    assert schema["name"] == "lab.ecg.detect_rpeaks"
    assert schema["description"]
    assert schema["citations"] == [
        "Pan J, Tompkins W. A real-time QRS detection algorithm. IEEE TBME. 1985."
    ]
    for section in ("params", "inputs", "outputs"):
        assert isinstance(schema[section], dict)
        assert "properties" in schema[section]

    assert "min_distance_ms" in schema["params"]["properties"]
    assert "signal" in schema["inputs"]["properties"]
    assert "rpeaks" in schema["outputs"]["properties"]


def test_to_json_schema_input_field_extra_surfaces_in_json_schema():
    schema = DetectRPeaksSchema.to_json_schema()
    signal_property = schema["inputs"]["properties"]["signal"]
    assert signal_property["artifact_type"] == "signal"
    assert signal_property["artifact_format"] == "wfdb"


def test_to_json_schema_with_citations_none():
    schema = NoCitationsIoSchema.to_json_schema()
    assert schema["citations"] == "none"


# --- citations_are_valid -------------------------------------------------


@pytest.mark.parametrize(
    "citations",
    [
        "none",
        ["Some Citation. Journal. 2020."],
        ["One citation.", "Another citation."],
    ],
)
def test_citations_are_valid_accepts(citations):
    assert citations_are_valid(citations) is True


@pytest.mark.parametrize(
    "citations",
    [
        None,
        [],
        "",
        "some free text that is not the literal 'none'",
        [1, 2, 3],
        ["ok citation", 42],
    ],
)
def test_citations_are_valid_rejects(citations):
    assert citations_are_valid(citations) is False
