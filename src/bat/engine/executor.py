"""DAG execution engine for BAT protocols.

Both workflows (within a protocol) and steps (within a workflow) form
directed acyclic graphs via ``depends_on`` declarations. Execution is
always single-threaded: :func:`topological_sort` produces one linear
execution order per DAG (workflows, then steps within each workflow),
using YAML declaration order as the tiebreaker whenever more than one node
is ready to run. :func:`execute_protocol` walks those orders, resolving
each step's inputs from the :class:`~bat.artifacts.registry.ArtifactRegistry`,
invoking the step's plugin module, and registering its declared outputs.

Full step-level error-handling semantics (retry policies, richer error
artifacts, workflow-level ``on_error``, ...) are the subject of CARD-011
and live in ``bat.engine.errors``, which does not exist yet. This module
only implements the minimal inline behavior CARD-010 itself requires: a
step with no ``on_error`` (or ``on_error.action == "stop"``) re-raises and
stops the run; a step with ``on_error.action == "continue"`` catches the
exception, writes a small error artifact for each declared
``on_error.output`` name, and lets the topological walk continue.

See ``cards/backlog/CARD-010-dag-execution-engine.md`` for the full spec.
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any

from bat.artifacts import storage
from bat.artifacts.model import Artifact
from bat.artifacts.registry import ArtifactRegistry
from bat.engine.run import RunContext
from bat.engine.schema import Protocol, Step
from bat.plugins.interface import BATContext

__all__ = ["topological_sort", "execute_protocol", "CycleError", "ExecutorError"]


class CycleError(Exception):
    """Raised by :func:`topological_sort` when the graph contains a cycle."""


class ExecutorError(Exception):
    """Raised for execution-time errors that are not step-graph cycles.

    Covers a step referencing a module that isn't in the plugin registry,
    and a step whose ``module.run()`` did not produce all of its declared
    outputs.
    """


# --------------------------------------------------------------------------
# Topological sort
# --------------------------------------------------------------------------


def topological_sort(nodes: list[str], depends_on: dict[str, list[str]]) -> list[str]:
    """Return ``nodes`` in a valid topological execution order.

    Uses Kahn's algorithm. Whenever more than one node is ready to run
    (in-degree zero), the node that appears earliest in ``nodes`` (i.e.
    YAML declaration order) is chosen next, so the result is deterministic
    and matches declaration order as closely as the dependency graph
    allows.

    Raises:
        CycleError: If the graph contains a cycle. The error message names
            one concrete cycle found among the unresolved nodes.
    """
    order_index = {name: i for i, name in enumerate(nodes)}
    in_degree = {name: 0 for name in nodes}
    dependents: dict[str, list[str]] = {name: [] for name in nodes}

    for name in nodes:
        for dep in depends_on.get(name, []):
            in_degree[name] += 1
            dependents[dep].append(name)

    ready = [name for name in nodes if in_degree[name] == 0]
    ready.sort(key=lambda n: order_index[n])

    result: list[str] = []
    while ready:
        current = ready.pop(0)
        result.append(current)
        newly_ready = []
        for dependent in dependents[current]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                newly_ready.append(dependent)
        if newly_ready:
            ready.extend(newly_ready)
            ready.sort(key=lambda n: order_index[n])

    if len(result) != len(nodes):
        remaining = [name for name in nodes if name not in result]
        cycle = _find_cycle(remaining, depends_on)
        raise CycleError(
            "Cycle detected in dependency graph: " + " -> ".join(cycle)
        )

    return result


def _find_cycle(remaining: list[str], depends_on: dict[str, list[str]]) -> list[str]:
    """Find and return one concrete cycle among ``remaining`` nodes.

    ``remaining`` is the set of nodes Kahn's algorithm could not resolve
    (i.e. every node here is part of a cycle, or depends -- transitively --
    on one). Restricts DFS edges to ``remaining`` so the walk can't escape
    into already-resolved (acyclic) parts of the graph.
    """
    remaining_set = set(remaining)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {name: WHITE for name in remaining}
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        color[node] = GRAY
        path.append(node)
        for dep in depends_on.get(node, []):
            if dep not in remaining_set:
                continue
            if color[dep] == GRAY:
                start = path.index(dep)
                return path[start:] + [dep]
            if color[dep] == WHITE:
                found = visit(dep)
                if found is not None:
                    return found
        path.pop()
        color[node] = BLACK
        return None

    for name in remaining:
        if color[name] == WHITE:
            found = visit(name)
            if found is not None:
                return found

    # Should be unreachable given every node in `remaining` failed to
    # resolve, but fall back to just naming the unresolved set.
    return remaining


# --------------------------------------------------------------------------
# Protocol execution
# --------------------------------------------------------------------------


def execute_protocol(
    protocol: Protocol,
    registry: ArtifactRegistry,
    plugin_registry: dict,
    run_ctx: RunContext,
) -> None:
    """Execute every workflow and step in ``protocol``, in topological order.

    Workflows are sorted by their ``depends_on``, then the steps within
    each workflow are sorted by their own ``depends_on``. Each step's
    inputs are resolved from ``registry`` before its module is invoked; its
    declared outputs are validated and registered afterward. See the
    module docstring for the (minimal, CARD-010-scoped) ``on_error``
    handling.
    """
    workflow_names = list(protocol.workflows.keys())
    workflow_depends_on = {
        name: workflow.depends_on for name, workflow in protocol.workflows.items()
    }
    workflow_order = topological_sort(workflow_names, workflow_depends_on)

    for workflow_name in workflow_order:
        workflow = protocol.workflows[workflow_name]
        steps_by_id = {step.id: step for step in workflow.steps}
        step_depends_on = {step.id: step.depends_on for step in workflow.steps}
        step_order = topological_sort(list(steps_by_id.keys()), step_depends_on)

        workflow_logger = run_ctx.logger.getChild(f"workflow.{workflow_name}")
        workflow_logger.info("starting workflow %r", workflow_name)

        for step_id in step_order:
            step = steps_by_id[step_id]
            _execute_step(step, workflow_name, registry, plugin_registry, run_ctx)


def _execute_step(
    step: Step,
    workflow_name: str,
    registry: ArtifactRegistry,
    plugin_registry: dict,
    run_ctx: RunContext,
) -> None:
    """Run a single step: resolve inputs, invoke its module, register outputs.

    On exception: re-raises unless ``step.on_error.action == "continue"``,
    in which case the error is handled by :func:`_handle_step_error` and
    execution moves on to the next step.
    """
    step_logger = run_ctx.logger.getChild(f"step.{step.id}")
    try:
        inputs = {
            input_name: registry.get(ref.artifact)
            for input_name, ref in step.inputs.items()
        }

        module = plugin_registry.get(step.module)
        if module is None:
            raise ExecutorError(
                f"step {step.id!r} references module {step.module!r}, which "
                "is not present in the plugin registry"
            )

        context = BATContext(
            run_dir=run_ctx.run_dir,
            artifacts_dir=run_ctx.artifacts_dir,
            logger=step_logger,
        )

        step_logger.info("running step %r (module=%s)", step.id, step.module)
        outputs: dict[str, Any] = module.run(inputs, step.params, context)

        missing = [name for name in step.outputs if name not in outputs]
        if missing:
            raise ExecutorError(
                f"step {step.id!r} declared outputs {missing!r} but its "
                f"module.run() did not produce them (got {list(outputs)!r})"
            )

        for output_name in step.outputs:
            registry.register(outputs[output_name])

        step_logger.info("step %r completed", step.id)
    except Exception as exc:
        on_error = step.on_error
        if on_error is None or on_error.action == "stop":
            raise
        step_logger.error("step %r failed, continuing (on_error=continue): %s", step.id, exc)
        _handle_step_error(step, workflow_name, exc, registry, run_ctx)


def _handle_step_error(
    step: Step,
    workflow_name: str,
    exc: Exception,
    registry: ArtifactRegistry,
    run_ctx: RunContext,
) -> None:
    """Minimal, CARD-010-scoped error handling for ``on_error: continue``.

    Writes one small YAML error artifact per name declared in
    ``step.on_error.output`` and registers it in ``registry``. This is
    deliberately minimal -- CARD-011 will replace/refactor this with a
    dedicated ``bat.engine.errors.handle_step_error()``.
    """
    assert step.on_error is not None  # guarded by the caller

    payload = {
        "step_id": step.id,
        "message": str(exc),
        "traceback": traceback.format_exc(),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    for output_name, declaration in step.on_error.output.items():
        storage.write_data_yaml(run_ctx.run_dir, output_name, payload)
        artifact = Artifact(
            name=output_name,
            artifact_type=declaration.type,
            format=declaration.format,
            path=storage.artifact_dir(run_ctx.run_dir, output_name),
            creator_module=step.module,
            creator_step=step.id,
            creator_workflow=workflow_name,
            params=step.params,
        )
        storage.write_meta(run_ctx.run_dir, artifact)
        registry.register(artifact)
