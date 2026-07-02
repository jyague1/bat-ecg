"""Guards that artifact-type constants and the UTC timestamp formatter have
a single source of truth (improvement: de-duplication).

Artifact types were previously declared independently in three modules and
default formats in two; the UTC timestamp format was reimplemented in three
places. These tests fail if any of those definitions drift apart again.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import get_args

from bat._util import UTC_TIMESTAMP_FORMAT, format_utc
from bat.artifacts import model
from bat.artifacts.types import ARTIFACT_TYPES, DEFAULT_FORMATS, ArtifactType
from bat.plugins import schema as plugins_schema


def test_artifact_types_match_the_literal():
    assert ARTIFACT_TYPES == frozenset(get_args(ArtifactType))


def test_artifact_types_match_default_formats():
    assert ARTIFACT_TYPES == frozenset(DEFAULT_FORMATS)


def test_model_reexports_the_shared_constants():
    assert model.ARTIFACT_TYPES is ARTIFACT_TYPES
    assert model.DEFAULT_FORMATS is DEFAULT_FORMATS


def test_plugins_schema_reuses_shared_defaults():
    # The plugin schema keeps the DEFAULT_ARTIFACT_FORMATS name it has
    # always exported, but it is the same object as the shared defaults.
    assert plugins_schema.DEFAULT_ARTIFACT_FORMATS is DEFAULT_FORMATS
    assert plugins_schema.ARTIFACT_TYPES is ARTIFACT_TYPES


def test_format_utc_naive_is_treated_as_utc():
    dt = datetime(2026, 6, 23, 15, 30, 15)
    assert format_utc(dt) == "2026-06-23T15:30:15Z"


def test_format_utc_aware_is_converted_to_utc():
    # 15:30 at +02:00 is 13:30 UTC.
    tz = timezone.utc
    dt_utc = datetime(2026, 6, 23, 13, 30, 15, tzinfo=tz)
    assert format_utc(dt_utc) == "2026-06-23T13:30:15Z"
    assert UTC_TIMESTAMP_FORMAT.endswith("Z")
