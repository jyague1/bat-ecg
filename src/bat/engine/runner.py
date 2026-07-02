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

Provenance
----------
``run_protocol`` passes a :class:`~bat.engine.executor.RunRecords` into
:func:`~bat.engine.executor.execute_protocol`, which populates it with the
real per-step/per-workflow status and wall-clock timings as it runs.
:func:`_build_provenance` then joins those recorded outcomes with each
step's static protocol data (module, inputs, outputs, params) and plugin
version -- a direct adapter, with no reconstruction of status from the
final registry state and no faked per-step timing. ``records`` is an
optional parameter on ``execute_protocol``; callers that don't need
provenance (most executor tests) simply omit it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bat.artifacts.registry import ArtifactRegistry
from bat.engine.errors import StepExecutionError
from bat.engine.executor import RunRecords, execute_protocol, topological_sort
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
    records = RunRecords()
    started_at = datetime.now(timezone.utc)

    failed_step_id: str | None = None
    failed_workflow_id: str | None = None
    run_error: str | None = None
    run_stopped = False

    try:
        execute_protocol(protocol, registry, plugin_registry, run_ctx, records=records)
    except StepExecutionError as exc:
        failed_step_id = exc.step_id
        failed_workflow_id = exc.workflow_id
        run_error = str(exc)
        run_stopped = True

    finished_at = datetime.now(timezone.utc)

    provenance = _build_provenance(
        protocol=protocol,
        records=records,
        registry=registry,
        plugin_registry=plugin_registry,
        run_ctx=run_ctx,
        protocol_path=protocol_path,
        started_at=started_at,
        finished_at=finished_at,
        run_stopped=run_stopped,
    )
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
# Provenance construction (from the executor's real execution records)
# --------------------------------------------------------------------------


def _module_version_lookup(environment: dict) -> dict[str, str | None]:
    """Map top-level plugin namespace -> version, from an environment record."""
    return {entry["name"]: entry["version"] for entry in environment.get("plugins", [])}


def _build_provenance(
    protocol: Protocol,
    records: RunRecords,
    registry: ArtifactRegistry,
    plugin_registry: dict,
    run_ctx: RunContext,
    protocol_path: Path,
    started_at: datetime,
    finished_at: datetime,
    run_stopped: bool,
) -> RunProvenance:
    """Build a :class:`RunProvenance` directly from ``records``.

    ``records`` was populated by :func:`~bat.engine.executor.execute_protocol`
    as it ran, so per-step/per-workflow status and timings are the real
    thing -- no reconstruction from the final registry state, and no faked
    per-step wall-clock. This adapter just joins each recorded outcome with
    the step's static protocol data (module, inputs, outputs, params) and
    the plugin version.
    """
    environment = build_environment_record(plugin_registry)
    module_versions = _module_version_lookup(environment)

    workflow_records: list[WorkflowRecord] = []
    for wf_outcome in records.workflows:
        workflow = protocol.workflows[wf_outcome.workflow_id]
        steps_by_id = {step.id: step for step in workflow.steps}

        step_records: list[StepRecord] = []
        for step_outcome in wf_outcome.steps:
            step = steps_by_id[step_outcome.step_id]
            namespace = step.module.split(".", 1)[0]
            # A step only "has" its declared outputs in provenance if it
            # actually completed successfully and produced them.
            outputs = (
                list(step.outputs.keys())
                if step_outcome.status == "success"
                else []
            )
            step_records.append(
                StepRecord(
                    step_id=step_outcome.step_id,
                    status=step_outcome.status,
                    module=step.module,
                    module_version=module_versions.get(namespace),
                    started_at=step_outcome.started_at or started_at,
                    finished_at=step_outcome.finished_at,
                    inputs=[ref.artifact for ref in step.inputs.values()],
                    outputs=outputs,
                    params=dict(step.params),
                )
            )

        workflow_records.append(
            WorkflowRecord(
                workflow_id=wf_outcome.workflow_id,
                status=wf_outcome.status,
                started_at=wf_outcome.started_at or started_at,
                finished_at=wf_outcome.finished_at,
                steps=step_records,
            )
        )

    artifact_records: list[ArtifactRecord] = [
        build_artifact_record(artifact, registry) for artifact in registry.all()
    ]

    return RunProvenance(
        run_id=run_ctx.run_id,
        protocol_path=protocol_path,
        started_at=started_at,
        finished_at=finished_at,
        status=_overall_status(records, run_stopped),
        environment=environment,
        workflow_records=workflow_records,
        artifact_records=artifact_records,
    )


def _overall_status(records: RunRecords, run_stopped: bool) -> str:
    """Derive the run's overall status from the recorded workflow outcomes.

    ``"failed"`` if the run stopped on an unhandled failure; otherwise
    ``"partial"`` if any workflow failed-but-was-continued (workflow-level
    ``on_error: continue``) or completed with a handled step failure; else
    ``"success"``.
    """
    if run_stopped:
        return "failed"
    statuses = {wf.status for wf in records.workflows}
    if statuses & {"failed", "partial"}:
        return "partial"
    return "success"
