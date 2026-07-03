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

The structural rules here intentionally mirror the Pydantic validators in
:mod:`bat.engine.schema` plus the cycle check in
:func:`bat.engine.loader.load_protocol`. ``tests/test_validation_parity.py``
guards the two from drifting on the rules they share.
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

    if not isinstance(workflows, list):
        errors.append(
            f"workflows: expected a list, got {type(workflows).__name__}"
        )
        return errors

    if not workflows:
        errors.append("workflows: must be non-empty")
        return errors

    # Workflow ids, for depends_on validation.
    workflow_ids: set[str] = set()
    for workflow in workflows:
        if isinstance(workflow, dict):
            workflow_id = workflow.get("id")
            if isinstance(workflow_id, str):
                workflow_ids.add(workflow_id)

    # Track step ids and artifact names across the whole protocol for
    # uniqueness checks.
    seen_workflow_ids: dict[str, int] = {}
    seen_step_ids: dict[str, str] = {}
    seen_artifact_names: dict[str, str] = {}

    for wf_idx, workflow in enumerate(workflows):
        if not isinstance(workflow, dict):
            errors.append(
                f"workflows[{wf_idx}]: expected a mapping, got "
                f"{type(workflow).__name__}"
            )
            continue

        workflow_id = workflow.get("id")
        if not isinstance(workflow_id, str) or not workflow_id:
            errors.append(f"workflows[{wf_idx}]: missing required field 'id'")

        wf_name = workflow_id if isinstance(workflow_id, str) else f"[{wf_idx}]"
        wf_path = f"workflows.{wf_name}"

        if isinstance(workflow_id, str):
            if workflow_id in seen_workflow_ids:
                errors.append(
                    f"{wf_path}: duplicate workflow id {workflow_id!r}"
                )
            else:
                seen_workflow_ids[workflow_id] = wf_idx

        # depends_on at workflow level.
        wf_depends_on = workflow.get("depends_on") or []
        if isinstance(wf_depends_on, list):
            for dep_idx, dep in enumerate(wf_depends_on):
                if dep not in workflow_ids:
                    errors.append(
                        f"{wf_path}.depends_on[{dep_idx}]: {dep!r} does not "
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

            # outputs maps a module output field name -> the chosen artifact
            # name; artifact names (the values) must be unique.
            outputs = step.get("outputs") or {}
            if isinstance(outputs, dict):
                for artifact_name in outputs.values():
                    if not isinstance(artifact_name, str):
                        continue
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

    errors.extend(_cycle_errors(workflows))

    return errors


def _detect_cycle(nodes: list[str], deps: dict[str, list[str]]) -> list[str] | None:
    """Return one concrete cycle (as a node path) in a ``depends_on`` graph, or None.

    A standalone DFS three-colouring so that :mod:`bat.engine.validation`
    stays independent of the executor (its whole point is a lightweight,
    raw-dict pass). ``deps`` targets that aren't in ``nodes`` are ignored by
    the caller before this runs, so missing-reference errors are reported
    separately and don't produce spurious cycle noise here.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for dep in deps.get(node, []):
            if dep not in color:
                continue
            if color[dep] == GRAY:
                return path[path.index(dep):] + [dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found is not None:
                    return found
        path.pop()
        color[node] = BLACK
        return None

    for node in nodes:
        if color[node] == WHITE:
            found = visit(node)
            if found is not None:
                return found
    return None


def _cycle_errors(workflows: list[Any]) -> list[str]:
    """Collect dependency-cycle errors for the workflow graph and each step graph.

    Tolerant of malformed input (non-dict workflows/steps, non-list
    ``depends_on``, ids that aren't strings): such entries are simply
    skipped, since they're already reported by the structural checks. Only
    ``depends_on`` targets that name a known node participate, so a cycle is
    reported once and never conflated with a missing-reference error.
    """
    errors: list[str] = []

    workflow_names = [
        wf["id"]
        for wf in workflows
        if isinstance(wf, dict) and isinstance(wf.get("id"), str)
    ]
    workflow_names_set = set(workflow_names)
    workflow_deps: dict[str, list[str]] = {}
    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        name = workflow.get("id")
        if not isinstance(name, str):
            continue
        raw = workflow.get("depends_on")
        workflow_deps[name] = (
            [d for d in raw if d in workflow_names_set]
            if isinstance(raw, list)
            else []
        )
    cycle = _detect_cycle(workflow_names, workflow_deps)
    if cycle is not None:
        errors.append(
            "workflows: dependency cycle detected: " + " -> ".join(cycle)
        )

    for workflow in workflows:
        if not isinstance(workflow, dict):
            continue
        wf_name = workflow.get("id")
        if not isinstance(wf_name, str):
            continue
        steps = workflow.get("steps")
        if not isinstance(steps, list):
            continue

        step_ids = [
            step["id"]
            for step in steps
            if isinstance(step, dict) and isinstance(step.get("id"), str)
        ]
        step_deps: dict[str, list[str]] = {}
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_id = step.get("id")
            if not isinstance(step_id, str):
                continue
            raw = step.get("depends_on")
            step_deps[step_id] = (
                [d for d in raw if d in step_ids] if isinstance(raw, list) else []
            )
        cycle = _detect_cycle(step_ids, step_deps)
        if cycle is not None:
            errors.append(
                f"workflows.{wf_name}.steps: dependency cycle detected: "
                + " -> ".join(cycle)
            )

    return errors
