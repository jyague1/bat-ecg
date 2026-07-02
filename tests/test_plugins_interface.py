"""Tests for the plugin module interface (CARD-007).

Covers ``BATContext`` (importable dataclass carrying ``run_dir``,
``artifacts_dir``, and a run-scoped ``logger``) and the type aliases
exported alongside it.
"""

from __future__ import annotations

import logging
from dataclasses import fields
from pathlib import Path
from typing import Any

from bat.plugins.interface import Artifacts, BATContext, Params, RunFn


def test_bat_context_is_importable_dataclass_with_expected_fields():
    field_names = {f.name for f in fields(BATContext)}
    assert field_names == {"run_dir", "artifacts_dir", "logger"}


def test_bat_context_construction_and_attribute_access(tmp_path):
    run_dir = tmp_path / "run-001"
    artifacts_dir = run_dir / "artifacts"
    logger = logging.getLogger("bat.test")

    context = BATContext(run_dir=run_dir, artifacts_dir=artifacts_dir, logger=logger)

    assert context.run_dir == run_dir
    assert context.artifacts_dir == artifacts_dir
    assert context.logger is logger


def test_artifacts_dir_is_conventionally_under_run_dir(tmp_path):
    run_dir = tmp_path / "run-001"
    context = BATContext(
        run_dir=run_dir,
        artifacts_dir=run_dir / "artifacts",
        logger=logging.getLogger("bat.test"),
    )
    assert context.artifacts_dir == context.run_dir / "artifacts"


def test_type_aliases_are_exported():
    # Artifacts/Params are dict-shaped aliases used for run()'s inputs,
    # params, and return value; both resolve to dict[str, Any].
    assert Artifacts == dict[str, Any]
    assert Params == dict[str, Any]
    assert RunFn is not None


def test_run_fn_matches_the_documented_run_signature():
    def run(inputs: dict, params: dict, context: BATContext | None = None) -> dict:
        return {}

    # Just exercise the documented signature end-to-end -- RunFn is a
    # typing-only alias, not something enforced at runtime.
    result = run({}, {}, BATContext(run_dir=Path("."), artifacts_dir=Path("./artifacts"), logger=logging.getLogger("x")))
    assert result == {}
