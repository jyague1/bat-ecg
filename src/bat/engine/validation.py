"""Structural validation for BAT protocol files (``bat validate``).

This module implements a deliberately separate, simpler validation pass
from :func:`bat.engine.loader.load_protocol`. The loader's job is to
produce a fully-substituted, fully-validated :class:`~bat.engine.schema.
Protocol` object for execution, and it stops at the *first* error it
encounters (Pydantic model validators raise on first failure).

``validate_protocol`` instead walks the raw, post-import (but NOT
variable-substituted) protocol dict directly, so that:

- ``{{ var }}`` tokens do not need to be defined to validate successfully.
- Every structural problem found is collected and reported, not just the
  first one.

Static imports are still resolved (see :mod:`bat.engine.imports`) before
the structural checks run, since ``bat validate`` documents that imported
files are read and inlined before validation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from bat.engine.imports import ImportResolutionError, resolve_imports


def validate_protocol(path: str | Path) -> list[str]:
    """Structurally validate a BAT protocol file.

    Args:
        path: Path to the protocol YAML file.

    Returns:
        A list of human-readable error message strings. An empty list means
        the protocol is structurally valid.
    """
    path = Path(path)

    if not path.is_file():
        return [f"Protocol file not found: {path}"]

    try:
        raw_text = path.read_text()
    except OSError as exc:
        return [f"Could not read protocol file {path}: {exc}"]

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        return [f"Invalid YAML in protocol file {path}: {exc}"]

    if data is None:
        return [f"Protocol file {path} is empty"]

    if not isinstance(data, dict):
        return [
            f"Protocol file {path} must contain a YAML mapping at the top "
            f"level, got {type(data).__name__}"
        ]

    try:
        data, _imported_vars = resolve_imports(data, base_path=path.parent)
    except ImportResolutionError as exc:
        return [f"Could not resolve imports for protocol file {path}: {exc}"]

    return _check_structure(data)


def _check_structure(data: dict[str, Any]) -> list[str]:
    """Walk the raw (post-import) protocol dict and collect all structural errors."""
    errors: list[str] = []

    version = data.get("version")
    if "version" not in data:
        errors.append("version: missing required field 'version'")
    elif not isinstance(version, str):
        errors.append(
            f"version: expected a string, got {type(version).__name__}"
        )

    workflows = data.get("workflows")
    if "workflows" not in data or workflows is None:
        errors.append("workflows: missing required field 'workflows'")
        return errors

    if not isinstance(workflows, dict):
        errors.append(
            f"workflows: expected a mapping, got {type(workflows).__name__}"
        )
        return errors

    if not workflows:
        errors.append("workflows: must be non-empty")
        return errors

    workflow_names = set(workflows.keys())

    # Track step ids and artifact names across the whole protocol for
    # uniqueness checks.
    seen_step_ids: dict[str, str] = {}
    seen_artifact_names: dict[str, str] = {}

    for wf_name, workflow in workflows.items():
        wf_path = f"workflows.{wf_name}"

        if not isinstance(workflow, dict):
            errors.append(
                f"{wf_path}: expected a mapping, got {type(workflow).__name__}"
            )
            continue

        # depends_on at workflow level.
        wf_depends_on = workflow.get("depends_on") or []
        if isinstance(wf_depends_on, list):
            for idx, dep in enumerate(wf_depends_on):
                if dep not in workflow_names:
                    errors.append(
                        f"{wf_path}.depends_on[{idx}]: {dep!r} does not "
                        "refer to a known workflow"
                    )
        else:
            errors.append(
                f"{wf_path}.depends_on: expected a list, got "
                f"{type(wf_depends_on).__name__}"
            )

        steps = workflow.get("steps")
        if "steps" not in workflow or steps is None:
            errors.append(f"{wf_path}.steps: missing required field 'steps'")
            continue

        if not isinstance(steps, list):
            errors.append(
                f"{wf_path}.steps: expected a list, got {type(steps).__name__}"
            )
            continue

        if not steps:
            errors.append(f"{wf_path}.steps: must contain at least one step")
            continue

        # Step ids within this workflow, for depends_on validation.
        step_ids_in_workflow: set[str] = set()
        for step in steps:
            if isinstance(step, dict):
                step_id = step.get("id")
                if isinstance(step_id, str):
                    step_ids_in_workflow.add(step_id)

        for idx, step in enumerate(steps):
            step_path = f"{wf_path}.steps[{idx}]"

            if not isinstance(step, dict):
                errors.append(
                    f"{step_path}: expected a mapping, got "
                    f"{type(step).__name__}"
                )
                continue

            for field in ("id", "name", "module"):
                if field not in step or step.get(field) in (None, ""):
                    errors.append(
                        f"{step_path}: missing required field {field!r}"
                    )

            step_id = step.get("id")
            step_label = step_id if isinstance(step_id, str) else f"[{idx}]"

            if isinstance(step_id, str):
                if step_id in seen_step_ids:
                    errors.append(
                        f"{step_path}: duplicate step id {step_id!r} "
                        f"(already declared in workflow "
                        f"{seen_step_ids[step_id]!r})"
                    )
                else:
                    seen_step_ids[step_id] = wf_name

            # depends_on at step level.
            step_depends_on = step.get("depends_on") or []
            if isinstance(step_depends_on, list):
                for dep_idx, dep in enumerate(step_depends_on):
                    if dep not in step_ids_in_workflow:
                        errors.append(
                            f"{wf_path}.steps.{step_label}.depends_on"
                            f"[{dep_idx}]: {dep!r} does not refer to a "
                            f"known step id in workflow {wf_name!r}"
                        )
            else:
                errors.append(
                    f"{wf_path}.steps.{step_label}.depends_on: expected a "
                    f"list, got {type(step_depends_on).__name__}"
                )

            # Artifact names declared in outputs must be unique.
            outputs = step.get("outputs") or {}
            if isinstance(outputs, dict):
                for artifact_name in outputs:
                    if artifact_name in seen_artifact_names:
                        errors.append(
                            f"{wf_path}.steps.{step_label}.outputs: "
                            f"duplicate artifact name {artifact_name!r} "
                            "(already declared by step "
                            f"{seen_artifact_names[artifact_name]!r})"
                        )
                    else:
                        seen_artifact_names[artifact_name] = step_label
            else:
                errors.append(
                    f"{wf_path}.steps.{step_label}.outputs: expected a "
                    f"mapping, got {type(outputs).__name__}"
                )

            on_error = step.get("on_error")
            if isinstance(on_error, dict):
                on_error_output = on_error.get("output") or {}
                if isinstance(on_error_output, dict):
                    for artifact_name in on_error_output:
                        if artifact_name in seen_artifact_names:
                            errors.append(
                                f"{wf_path}.steps.{step_label}.on_error."
                                f"output: duplicate artifact name "
                                f"{artifact_name!r} (already declared by "
                                f"step {seen_artifact_names[artifact_name]!r})"
                            )
                        else:
                            seen_artifact_names[artifact_name] = step_label

    return errors
