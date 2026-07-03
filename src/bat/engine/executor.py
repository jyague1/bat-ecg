"""DAG execution engine for BAT protocols.

Both workflows (within a protocol) and steps (within a workflow) form
directed acyclic graphs via ``depends_on`` declarations. Execution is
always single-threaded: :func:`topological_sort` produces one linear
execution order per DAG (workflows, then steps within each workflow),
using YAML declaration order as the tiebreaker whenever more than one node
is ready to run. :func:`execute_protocol` walks those orders, resolving
each step's inputs from the :class:`~bat.artifacts.registry.ArtifactRegistry`,
invoking the step's plugin module, and registering its declared outputs.

Step-level error-handling semantics (deciding whether a failure stops the
run or continues, and producing error artifacts) live in
:mod:`bat.engine.errors`. Post-step validation that declared outputs were
actually registered and written inside the run's artifacts directory lives
in :mod:`bat.engine.checks`. This module is responsible for:

- Calling :func:`~bat.engine.checks.check_step_outputs` after a step's
  outputs are registered, and treating a raised
  :class:`~bat.engine.checks.ArtifactViolationError` as just another kind
  of step failure.
- Calling :func:`~bat.engine.errors.handle_step_error` when a step's
  ``module.run()`` (or the output-restriction check above) raises, and
  raising :class:`~bat.engine.errors.StepExecutionError` when that call
  reports the run should stop.
- Workflow-level ``on_error``: if a :class:`~bat.engine.errors.StepExecutionError`
  propagates out of a workflow whose own ``on_error.action == "continue"``,
  that workflow stops but the run moves on to the next workflow in
  topological order. Otherwise the error propagates out of
  :func:`execute_protocol` and stops the whole run.

See ``cards/backlog/CARD-010-dag-execution-engine.md`` and
``cards/backlog/CARD-011-error-handling.md`` for the full specs.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bat.artifacts import storage
from bat.artifacts.registry import ArtifactRegistry
from bat.engine.checks import check_step_outputs
from bat.engine.errors import StepExecutionError, handle_step_error
from bat.engine.run import RunContext
from bat.engine.schema import Protocol, Step
from bat.plugins.interface import BATContext

__all__ = [
    "topological_sort",
    "check_acyclic",
    "execute_protocol",
    "CycleError",
    "ExecutorError",
    "StepExecutionError",
    "StepOutcome",
    "WorkflowOutcome",
    "RunRecords",
]


class CycleError(Exception):
    """Raised by :func:`topological_sort` when the graph contains a cycle."""


# --------------------------------------------------------------------------
# Execution records (real per-step/per-workflow status + timing)
# --------------------------------------------------------------------------


@dataclass
class StepOutcome:
    """What actually happened to one step during a run.

    ``status`` is ``"success"`` (ran, all declared outputs registered),
    ``"failed"`` (its ``module.run()`` or output check raised -- whether or
    not ``on_error: continue`` then handled it), or ``"skipped"`` (never
    reached because its workflow stopped or the run stopped earlier).
    """

    workflow_id: str
    step_id: str
    status: str = "skipped"
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class WorkflowOutcome:
    """What actually happened to one workflow during a run.

    ``status`` is ``"success"`` (all steps succeeded), ``"partial"`` (all
    steps ran but at least one failed and was handled via
    ``on_error: continue``), ``"failed"`` (a step failed unhandled, stopping
    the workflow), or ``"skipped"`` (the run stopped before this workflow
    ran).
    """

    workflow_id: str
    status: str = "skipped"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    steps: list[StepOutcome] = field(default_factory=list)


@dataclass
class RunRecords:
    """Collector populated by :func:`execute_protocol` as it runs.

    Pass an instance in to capture real per-step/per-workflow status and
    wall-clock timings; the runner turns these into provenance records
    directly, with no after-the-fact reconstruction. Optional -- callers
    that don't need provenance (e.g. most executor tests) omit it.
    """

    workflows: list[WorkflowOutcome] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def check_acyclic(protocol: Protocol) -> None:
    """Raise :class:`CycleError` if any ``depends_on`` graph has a cycle.

    Checks both the workflow-level graph and each workflow's step-level
    graph, reusing :func:`topological_sort` purely for its cycle-detection
    side effect. Callers with a validated :class:`Protocol` (e.g. the
    runner's pre-flight, before the run directory is created) use this so a
    cyclic protocol fails cleanly rather than blowing up mid-execution.
    Assumes every ``depends_on`` target is a known node -- true for a
    schema-validated ``Protocol``; the raw-dict validator in
    :mod:`bat.engine.validation` does its own tolerant cycle check.
    """
    workflow_ids = [workflow.id for workflow in protocol.workflows]
    workflow_depends_on = {
        workflow.id: workflow.depends_on for workflow in protocol.workflows
    }
    topological_sort(workflow_ids, workflow_depends_on)

    for workflow in protocol.workflows:
        step_ids = [step.id for step in workflow.steps]
        step_depends_on = {step.id: step.depends_on for step in workflow.steps}
        topological_sort(step_ids, step_depends_on)


# --------------------------------------------------------------------------
# Protocol execution
# --------------------------------------------------------------------------


def execute_protocol(
    protocol: Protocol,
    registry: ArtifactRegistry,
    plugin_registry: dict,
    run_ctx: RunContext,
    records: RunRecords | None = None,
) -> None:
    """Execute every workflow and step in ``protocol``, in topological order.

    Workflows are sorted by their ``depends_on``, then the steps within
    each workflow are sorted by their own ``depends_on``. Each step's
    inputs are resolved from ``registry`` before its module is invoked; its
    declared outputs are validated and registered afterward.

    If a step fails and :func:`~bat.engine.errors.handle_step_error`
    reports the run should stop, :class:`StepExecutionError` propagates up
    to this loop. If the containing workflow has
    ``on_error.action == "continue"``, that workflow is abandoned (its
    remaining steps do not run) but execution moves on to the next
    workflow; otherwise the error propagates out of this function entirely,
    stopping the whole run. See the module docstring for details.

    If a :class:`RunRecords` is passed as ``records``, it is populated with
    real per-step/per-workflow status and wall-clock timings as execution
    proceeds (the runner uses this to build provenance directly). Passing
    ``None`` is fully supported and changes nothing about execution.
    """
    if records is None:
        records = RunRecords()

    # Resolve the full topological plan up front. This both drives execution
    # and lets us seed one outcome record per workflow/step (all "skipped"
    # until proven otherwise), so a run that stops early still leaves an
    # accurate record for the steps that never ran -- no reconstruction.
    workflows_by_id = {workflow.id: workflow for workflow in protocol.workflows}
    workflow_ids = list(workflows_by_id.keys())
    workflow_depends_on = {
        workflow.id: workflow.depends_on for workflow in protocol.workflows
    }
    workflow_order = topological_sort(workflow_ids, workflow_depends_on)

    plan: list[tuple[str, Any, list[Step]]] = []
    for workflow_name in workflow_order:
        workflow = workflows_by_id[workflow_name]
        steps_by_id = {step.id: step for step in workflow.steps}
        step_depends_on = {step.id: step.depends_on for step in workflow.steps}
        step_order = topological_sort(list(steps_by_id.keys()), step_depends_on)
        ordered_steps = [steps_by_id[step_id] for step_id in step_order]
        plan.append((workflow_name, workflow, ordered_steps))

    for workflow_name, _workflow, ordered_steps in plan:
        wf_outcome = WorkflowOutcome(
            workflow_id=workflow_name,
            steps=[
                StepOutcome(workflow_id=workflow_name, step_id=step.id)
                for step in ordered_steps
            ],
        )
        records.workflows.append(wf_outcome)
    outcome_by_workflow = {wo.workflow_id: wo for wo in records.workflows}

    for workflow_name, workflow, ordered_steps in plan:
        wf_outcome = outcome_by_workflow[workflow_name]
        step_outcome_by_id = {so.step_id: so for so in wf_outcome.steps}
        wf_outcome.started_at = _now()

        workflow_logger = run_ctx.logger.getChild(f"workflow.{workflow_name}")
        workflow_logger.info("starting workflow %r", workflow_name)

        had_handled_failure = False
        try:
            for step in ordered_steps:
                step_outcome = step_outcome_by_id[step.id]
                step_outcome.started_at = _now()
                handled_failure = _execute_step(
                    step, workflow_name, registry, plugin_registry, run_ctx
                )
                step_outcome.finished_at = _now()
                if handled_failure:
                    step_outcome.status = "failed"
                    had_handled_failure = True
                else:
                    step_outcome.status = "success"
            wf_outcome.status = "partial" if had_handled_failure else "success"
            wf_outcome.finished_at = _now()
        except StepExecutionError as exc:
            failed_id = exc.step_id
            failed_outcome = (
                step_outcome_by_id.get(failed_id) if failed_id is not None else None
            )
            if failed_outcome is not None:
                failed_outcome.finished_at = _now()
                failed_outcome.status = "failed"
            wf_outcome.status = "failed"
            wf_outcome.finished_at = _now()

            on_error = workflow.on_error
            if on_error is not None and on_error.action == "continue":
                workflow_logger.error(
                    "workflow %r stopping due to unhandled step failure "
                    "(workflow on_error=continue); run continues with the "
                    "next workflow",
                    workflow_name,
                )
                continue
            raise


def _relocate_artifact(
    artifact: Any,
    output_name: str,
    step: Step,
    workflow_name: str,
    run_ctx: RunContext,
) -> Any:
    """Normalize a module-returned artifact before registration.

    A module only knows its own schema's output field name and creator
    module -- it cannot know the step's declared artifact name, workflow
    id, or step id. This stamps those authoritatively from what the
    executor already knows, and reconciles the artifact's on-disk
    location with BAT's ``artifacts/<artifact-name>/`` storage convention
    (``bat.artifacts.storage`` always derives an artifact's directory from
    its *name*, so if a module wrote its data under a different directory
    -- e.g. its own schema output key, as ``core.wfdb.read``/``write`` do
    -- that directory is moved to ``artifacts/<output_name>/`` here).

    Deliberately does NOT write ``meta.yaml`` -- that only happens once
    :func:`~bat.engine.checks.check_step_outputs` has confirmed the
    artifact's data genuinely exists on disk (see
    :func:`_write_output_meta`), so a module bug that never actually
    writes any data still gets caught rather than being masked by
    ``meta.yaml`` itself counting as "a file exists".
    """
    target_dir = run_ctx.artifacts_dir / output_name
    current_path = Path(artifact.path)

    if current_path != target_dir and current_path.is_dir():
        try:
            current_path.relative_to(run_ctx.artifacts_dir)
        except ValueError:
            new_path = current_path  # outside the run dir -- leave as-is
        else:
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            current_path.rename(target_dir)
            new_path = target_dir
    else:
        new_path = current_path

    return dataclasses.replace(
        artifact,
        name=output_name,
        path=new_path,
        creator_step=step.id,
        creator_workflow=workflow_name,
        creator_module=step.module,
    )


def _write_output_meta(step: Step, registry: ArtifactRegistry, run_ctx: RunContext) -> None:
    """Write ``meta.yaml`` for each of ``step``'s declared, now-validated outputs.

    Called only after :func:`~bat.engine.checks.check_step_outputs` has
    passed, so this never runs for an artifact whose data doesn't
    actually exist on disk (see :func:`_relocate_artifact`). Matches
    CARD-008's "meta.yaml is written alongside every artifact"
    requirement for successful step outputs -- error artifacts already
    get this from :mod:`bat.engine.errors`.
    """
    for artifact_name in step.outputs.values():
        storage.write_meta(run_ctx.run_dir, registry.get(artifact_name))


def _execute_step(
    step: Step,
    workflow_name: str,
    registry: ArtifactRegistry,
    plugin_registry: dict,
    run_ctx: RunContext,
) -> bool:
    """Run a single step: resolve inputs, invoke its module, register outputs.

    After a step's declared outputs are registered,
    :func:`~bat.engine.checks.check_step_outputs` runs as a final,
    best-effort validation that every declared artifact was actually
    registered and written to disk inside the run's ``artifacts/``
    directory (see ``cards/backlog/CARD-013-output-restriction-check.md``).
    A violation it raises is just another kind of step failure and is
    handled by the same ``except`` block below -- it never runs from
    within that block, only on the success path, per the card.

    On exception (including from ``module.run()`` itself or from the
    output-restriction check above): delegates to
    :func:`~bat.engine.errors.handle_step_error`. If it returns ``True``
    (``on_error: continue`` handled the failure and produced any declared
    error artifacts), execution returns normally so the caller moves on to
    the next step. If it returns ``False``, raises
    :class:`StepExecutionError` chained from the original exception,
    stopping the run (subject to workflow-level ``on_error`` handling in
    :func:`execute_protocol`).

    Returns:
        ``True`` if the step failed but was handled by ``on_error:
        continue`` (so it is recorded as a failed-but-continued step),
        ``False`` if it succeeded. Raises rather than returning when a
        failure is unhandled.
    """
    step_logger = run_ctx.logger.getChild(f"step.{step.id}")
    try:
        inputs = {
            input_name: registry.get(artifact_name)
            for input_name, artifact_name in step.inputs.items()
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
        raw_outputs: dict[str, Any] = module.run(inputs, step.params, context)

        missing = [
            module_field
            for module_field in step.outputs
            if module_field not in raw_outputs
        ]
        if missing:
            raise ExecutorError(
                f"step {step.id!r} declared outputs {missing!r} but its "
                f"module.run() did not produce them (got {list(raw_outputs)!r})"
            )

        for module_field, artifact_name in step.outputs.items():
            artifact = _relocate_artifact(
                raw_outputs[module_field], artifact_name, step, workflow_name, run_ctx
            )
            registry.register(artifact)

        check_step_outputs(step, registry, run_ctx)
        _write_output_meta(step, registry, run_ctx)

        step_logger.info("step %r completed", step.id)
        return False
    except Exception as exc:
        should_continue = handle_step_error(
            exc,
            step,
            workflow_id=workflow_name,
            on_error=step.on_error,
            registry=registry,
            artifacts_dir=run_ctx.artifacts_dir,
            logger=step_logger,
        )
        if not should_continue:
            # step_id/workflow_id let callers orchestrating a full run
            # (CARD-016's bat.engine.runner) report exactly which
            # step/workflow failed without parsing the message string.
            raise StepExecutionError(
                f"step {step.id!r} failed: {exc}",
                step_id=step.id,
                workflow_id=workflow_name,
            ) from exc
        # Failure was handled by on_error: continue -- recorded as a
        # failed-but-continued step by execute_protocol.
        return True
