"""Orchestration for ``bat run`` / ``bat dry-run`` (CARD-016).

This module wires together every engine component built across CARD-001
through CARD-015 into two entry points meant to be called directly from
:mod:`bat.cli.run`:

- :func:`run_protocol` — the full ``bat run`` execution sequence.
- :func:`dry_run_protocol` — the ``bat run --dry-run`` / ``bat dry-run``
  sequence: everything through creating the run directory and writing
  ``resolved_protocol.yaml``, but stopping before any module is invoked and
  returning a topologically-sorted execution plan instead.

Both share the same first few steps (load the protocol, discover plugins,
validate every ``step.module`` reference against the plugin registry,
create the run directory, write ``resolved_protocol.yaml``) via the private
:func:`_load_and_validate` / :func:`_workflow_step_order` helpers.

Provenance-building design note
--------------------------------
:func:`~bat.engine.executor.execute_protocol` (CARD-010/011/013) does not
return or accept anywhere to record per-step/per-workflow timing -- it just
runs the protocol and either returns normally or raises
:class:`~bat.engine.errors.StepExecutionError`. Rather than thread a new
callback/records parameter through the executor (which would touch code
several other cards' tests exercise directly, e.g.
``tests/test_engine_executor.py`` and ``tests/test_engine_checks.py``),
this module builds a **best-effort, coarse-grained**
:class:`~bat.engine.provenance.RunProvenance` after the fact (option (b)
from the card):

- All steps within a workflow share that workflow's ``started_at`` /
  ``finished_at`` timestamps (no true per-step wall-clock precision).
- A step's status is inferred from the final :class:`ArtifactRegistry`
  state plus (when the run stopped on an unhandled failure) the identity
  of the failing step: a step is ``"success"`` if all of its declared
  outputs are registered, ``"failed"`` if it is the step that raised (or
  if one of its ``on_error.output`` error artifacts was registered), and
  ``"skipped"`` otherwise (i.e. topologically after the point execution
  stopped).
- A workflow is ``"failed"`` if it contains the failing step, ``"partial"``
  if some of its steps failed-but-were-handled via ``on_error: continue``,
  ``"skipped"`` if it was never reached, and ``"success"`` otherwise.

The one surgical change made to :mod:`bat.engine.executor` to support this
is that the :class:`~bat.engine.errors.StepExecutionError` raised on an
unhandled step failure now carries ``.step_id`` / ``.workflow_id``
attributes -- a purely additive change that does not affect
``execute_protocol``'s signature or any existing caller/test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bat.artifacts.registry import ArtifactRegistry
from bat.engine.errors import StepExecutionError
from bat.engine.executor import execute_protocol, topological_sort
from bat.engine.loader import load_protocol
from bat.engine.provenance import (
    ArtifactRecord,
    RunProvenance,
    StepRecord,
    WorkflowRecord,
    build_artifact_record,
    build_environment_record,
    write_provenance,
)
from bat.engine.run import RunContext, create_run, write_resolved_protocol
from bat.engine.schema import Protocol, Step
from bat.plugins.discovery import discover_plugins

__all__ = [
    "MissingModulesError",
    "ExecutionPlan",
    "RunResult",
    "run_protocol",
    "dry_run_protocol",
]


class MissingModulesError(Exception):
    """Raised when one or more steps reference modules the plugin registry
    does not know about.

    Raised *before* the run directory is created, per the card's
    pre-flight-check requirement. Carries every missing reference (not
    just the first), so the CLI can report all of them at once.
    """

    def __init__(self, missing: list[tuple[str, str, str]]) -> None:
        """``missing`` is a list of ``(module, workflow_id, step_id)`` tuples."""
        self.missing = missing
        self.missing_modules = sorted({module for module, _wf, _step in missing})
        details = ", ".join(
            f"{module!r} (workflow {wf!r}, step {step!r})"
            for module, wf, step in missing
        )
        super().__init__(
            "Protocol references modules not found in the plugin registry: "
            f"{details}"
        )


@dataclass
class ExecutionPlan:
    """The result of :func:`dry_run_protocol`: run directory info plus plan.

    ``workflows`` is the topologically-sorted workflow order, each paired
    with its own topologically-sorted ``(step_id, module)`` list.
    """

    protocol_path: Path
    run_ctx: RunContext
    workflows: list[tuple[str, list[tuple[str, str]]]]


@dataclass
class RunResult:
    """The result of :func:`run_protocol`, enough for the CLI to print a
    run summary and set its exit code."""

    run_ctx: RunContext
    status: str  # "success", "failed", "partial"
    workflow_count: int
    step_count: int
    artifact_count: int
    duration_seconds: float
    failed_step: str | None = None
    failed_workflow: str | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status != "failed"


# --------------------------------------------------------------------------
# Shared setup (steps 1-5 of the card's execution sequence)
# --------------------------------------------------------------------------


def _load_and_validate(
    protocol_path: Path,
    vars_file: str | Path | None,
    cli_vars: dict[str, str],
) -> tuple[Protocol, dict]:
    """Load+resolve the protocol and discover plugins, then validate that
    every ``step.module`` reference exists in the plugin registry.

    Raises:
        ProtocolError: If the protocol fails to load/parse/validate.
        CycleError: If any workflow-level or step-level ``depends_on`` graph
            contains a cycle. Raised by ``load_protocol`` below, before the
            caller creates the run directory, so a cyclic protocol never
            leaves an orphaned run dir. Handled as a pre-flight error by the
            CLI (bat.cli.run._PREFLIGHT_ERRORS).
        PluginDiscoveryError: If plugin discovery itself fails (bad
            entry-point package, malformed local plugin module, etc).
        MissingModulesError: If one or more steps reference a module not
            present in the discovered plugin registry.
    """
    protocol = load_protocol(protocol_path, vars_file=vars_file, cli_vars=cli_vars)

    plugins_dir = protocol_path.parent / "plugins"
    plugin_registry = discover_plugins(plugins_dir)

    missing: list[tuple[str, str, str]] = []
    for workflow_id, workflow in protocol.workflows.items():
        for step in workflow.steps:
            if step.module not in plugin_registry:
                missing.append((step.module, workflow_id, step.id))
    if missing:
        raise MissingModulesError(missing)

    return protocol, plugin_registry


def _workflow_step_order(protocol: Protocol) -> list[tuple[str, list[Step]]]:
    """Topologically sort workflows, then steps within each workflow.

    Mirrors the two-level sort :func:`~bat.engine.executor.execute_protocol`
    performs internally, using the same :func:`topological_sort` helper, so
    dry-run's plan and the real execution order are guaranteed to match.
    """
    workflow_names = list(protocol.workflows.keys())
    workflow_depends_on = {
        name: workflow.depends_on for name, workflow in protocol.workflows.items()
    }
    workflow_order = topological_sort(workflow_names, workflow_depends_on)

    result: list[tuple[str, list[Step]]] = []
    for workflow_id in workflow_order:
        workflow = protocol.workflows[workflow_id]
        steps_by_id = {step.id: step for step in workflow.steps}
        step_depends_on = {step.id: step.depends_on for step in workflow.steps}
        step_order = topological_sort(list(steps_by_id.keys()), step_depends_on)
        result.append((workflow_id, [steps_by_id[step_id] for step_id in step_order]))
    return result


# --------------------------------------------------------------------------
# bat dry-run
# --------------------------------------------------------------------------


def dry_run_protocol(
    protocol_path: Path,
    run_name: str | None = None,
    vars_file: str | Path | None = None,
    cli_vars: dict[str, str] | None = None,
) -> ExecutionPlan:
    """Resolve ``protocol_path`` and build its execution plan, without
    running any module.

    Performs steps 1-5 of the card's execution sequence (load, discover
    plugins, validate module references, create the run directory, write
    ``resolved_protocol.yaml``), then builds the topologically-sorted
    workflow/step plan. Never calls :func:`~bat.engine.executor.execute_protocol`
    and never writes ``provenance.yaml``.
    """
    protocol_path = Path(protocol_path)
    protocol, _plugin_registry = _load_and_validate(
        protocol_path, vars_file, cli_vars or {}
    )

    run_ctx = create_run(protocol_path, run_name=run_name)
    write_resolved_protocol(run_ctx, protocol.model_dump(mode="json"))

    workflows = [
        (workflow_id, [(step.id, step.module) for step in steps])
        for workflow_id, steps in _workflow_step_order(protocol)
    ]

    return ExecutionPlan(protocol_path=protocol_path, run_ctx=run_ctx, workflows=workflows)


# --------------------------------------------------------------------------
# bat run
# --------------------------------------------------------------------------


def run_protocol(
    protocol_path: Path,
    run_name: str | None = None,
    vars_file: str | Path | None = None,
    cli_vars: dict[str, str] | None = None,
) -> RunResult:
    """Run ``protocol_path`` end to end: load, validate, execute, record
    provenance.

    Raises the same exceptions as :func:`dry_run_protocol` for failures
    that happen *before* the run directory is created (protocol load
    errors, plugin discovery errors, missing module references). Once the
    run directory exists, a step failure is *not* re-raised -- it is
    captured in the returned :class:`RunResult` (``status="failed"``,
    ``failed_step``/``failed_workflow`` set) so the CLI can print a
    failure summary and exit non-zero, while ``provenance.yaml`` is still
    written.
    """
    protocol_path = Path(protocol_path)
    protocol, plugin_registry = _load_and_validate(
        protocol_path, vars_file, cli_vars or {}
    )

    run_ctx = create_run(protocol_path, run_name=run_name)
    write_resolved_protocol(run_ctx, protocol.model_dump(mode="json"))

    registry = ArtifactRegistry()
    started_at = datetime.now(timezone.utc)

    failed_step_id: str | None = None
    failed_workflow_id: str | None = None
    run_error: str | None = None

    try:
        execute_protocol(protocol, registry, plugin_registry, run_ctx)
    except StepExecutionError as exc:
        failed_step_id = getattr(exc, "step_id", None)
        failed_workflow_id = getattr(exc, "workflow_id", None)
        run_error = str(exc)

    finished_at = datetime.now(timezone.utc)

    provenance = _build_provenance(
        protocol=protocol,
        registry=registry,
        plugin_registry=plugin_registry,
        run_ctx=run_ctx,
        protocol_path=protocol_path,
        started_at=started_at,
        finished_at=finished_at,
        failed_step_id=failed_step_id,
        failed_workflow_id=failed_workflow_id,
    )
    # provenance.status is derived from the per-workflow reconstruction in
    # _build_provenance, which already accounts for both an outright
    # unhandled failure (failed_step_id is not None -> "failed") and a
    # handled-but-imperfect run (some workflow's steps failed via
    # on_error: continue -> "partial").
    status = provenance.status

    write_provenance(run_ctx, provenance)

    workflow_count = len(protocol.workflows)
    step_count = sum(len(workflow.steps) for workflow in protocol.workflows.values())
    artifact_count = len(registry.all())
    duration_seconds = (finished_at - started_at).total_seconds()

    return RunResult(
        run_ctx=run_ctx,
        status=status,
        workflow_count=workflow_count,
        step_count=step_count,
        artifact_count=artifact_count,
        duration_seconds=duration_seconds,
        failed_step=failed_step_id,
        failed_workflow=failed_workflow_id,
        error=run_error,
    )


# --------------------------------------------------------------------------
# Provenance construction (best-effort, see module docstring)
# --------------------------------------------------------------------------


def _module_version_lookup(environment: dict) -> dict[str, str | None]:
    """Map top-level plugin namespace -> version, from an environment record."""
    return {entry["name"]: entry["version"] for entry in environment.get("plugins", [])}


def _build_provenance(
    protocol: Protocol,
    registry: ArtifactRegistry,
    plugin_registry: dict,
    run_ctx: RunContext,
    protocol_path: Path,
    started_at: datetime,
    finished_at: datetime,
    failed_step_id: str | None,
    failed_workflow_id: str | None,
) -> RunProvenance:
    """Build a best-effort :class:`RunProvenance` from the final registry
    state and (if the run failed) the identity of the failing step.

    See the module docstring for the precision trade-offs this makes.
    """
    environment = build_environment_record(plugin_registry)
    module_versions = _module_version_lookup(environment)

    workflow_records: list[WorkflowRecord] = []
    run_stopped = False  # once True, all later workflows never ran.

    for workflow_id, steps in _workflow_step_order(protocol):
        workflow = protocol.workflows[workflow_id]

        if run_stopped:
            workflow_records.append(
                _skipped_workflow_record(workflow_id, steps, started_at)
            )
            continue

        step_records: list[StepRecord] = []
        workflow_stopped = False
        workflow_had_handled_failure = False
        workflow_had_unhandled_failure = False

        for step in steps:
            if workflow_stopped:
                step_records.append(_step_record(step, "skipped", started_at, module_versions))
                continue

            if failed_step_id is not None and step.id == failed_step_id:
                step_records.append(_step_record(step, "failed", finished_at, module_versions))
                workflow_stopped = True
                workflow_had_unhandled_failure = True
                continue

            declared = list(step.outputs.keys())
            all_present = all(registry.exists(name) for name in declared)

            if declared and all_present:
                step_records.append(_step_record(step, "success", finished_at, module_versions))
                continue

            error_names = list(step.on_error.output.keys()) if step.on_error else []
            if error_names and any(registry.exists(name) for name in error_names):
                step_records.append(_step_record(step, "failed", finished_at, module_versions))
                workflow_had_handled_failure = True
                continue

            if not declared:
                # No declared outputs and it isn't the step that failed --
                # best-effort assumption is that it ran successfully.
                step_records.append(_step_record(step, "success", finished_at, module_versions))
                continue

            # Declared outputs, none/some present, no failure signal found:
            # never reached.
            step_records.append(_step_record(step, "skipped", started_at, module_versions))
            workflow_stopped = True

        if workflow_id == failed_workflow_id or workflow_had_unhandled_failure:
            workflow_status = "failed"
            wf_on_error = workflow.on_error
            if wf_on_error is None or wf_on_error.action != "continue":
                run_stopped = True
        elif workflow_had_handled_failure:
            workflow_status = "partial"
        else:
            workflow_status = "success"

        workflow_records.append(
            WorkflowRecord(
                workflow_id=workflow_id,
                status=workflow_status,
                started_at=started_at,
                finished_at=finished_at,
                steps=step_records,
            )
        )

    overall_status = _overall_status(workflow_records, failed_step_id)

    artifact_records: list[ArtifactRecord] = [
        build_artifact_record(artifact, registry) for artifact in registry.all()
    ]

    return RunProvenance(
        run_id=run_ctx.run_id,
        protocol_path=protocol_path,
        started_at=started_at,
        finished_at=finished_at,
        status=overall_status,
        environment=environment,
        workflow_records=workflow_records,
        artifact_records=artifact_records,
    )


def _overall_status(workflow_records: list[WorkflowRecord], failed_step_id: str | None) -> str:
    if failed_step_id is not None:
        return "failed"
    statuses = {wf.status for wf in workflow_records}
    if statuses <= {"success"}:
        return "success"
    if "failed" in statuses:
        # A workflow failed but the run continued past it (workflow-level
        # on_error: continue) -- the run as a whole didn't stop, so this
        # reads as a partial completion rather than an outright failure.
        return "partial"
    if "partial" in statuses:
        return "partial"
    return "success"


def _step_record(
    step: Step, status: str, when: datetime, module_versions: dict[str, str | None]
) -> StepRecord:
    namespace = step.module.split(".", 1)[0]
    outputs = [name for name in step.outputs if status != "skipped"]
    return StepRecord(
        step_id=step.id,
        status=status,
        module=step.module,
        module_version=module_versions.get(namespace),
        started_at=when,
        finished_at=when,
        inputs=[ref.artifact for ref in step.inputs.values()],
        outputs=outputs,
        params=dict(step.params),
    )


def _skipped_workflow_record(
    workflow_id: str, steps: list[Step], when: datetime
) -> WorkflowRecord:
    return WorkflowRecord(
        workflow_id=workflow_id,
        status="skipped",
        started_at=when,
        finished_at=when,
        steps=[
            StepRecord(
                step_id=step.id,
                status="skipped",
                module=step.module,
                module_version=None,
                started_at=when,
                finished_at=when,
                inputs=[ref.artifact for ref in step.inputs.values()],
                outputs=[],
                params=dict(step.params),
            )
            for step in steps
        ],
    )
