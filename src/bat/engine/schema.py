"""Pydantic models for the BAT protocol schema.

A protocol is the top-level document passed to ``bat run protocol.yaml``. Its
structure mirrors Ansible's playbook -> play -> task hierarchy:

    protocol -> workflows -> steps

See ``cards/backlog/CARD-003-protocol-schema-parser.md`` for the full spec.
This module only defines the schema and its validation rules; reading YAML
from disk is handled by :mod:`bat.engine.loader`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Artifact-type vocabulary lives in one place (bat.artifacts.types).
from bat.artifacts.types import ArtifactType


class ArtifactDeclaration(BaseModel):
    """Declaration of an artifact produced by a step's ``outputs``."""

    model_config = ConfigDict(extra="forbid")

    type: ArtifactType
    format: str


class OnError(BaseModel):
    """Error-handling behavior for a step or a workflow."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["stop", "continue"] = "stop"
    output: dict[str, ArtifactDeclaration] = Field(default_factory=dict)


class Step(BaseModel):
    """A single execution unit within a workflow."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    module: str
    depends_on: list[str] = Field(default_factory=list)
    # Both keyed by the *module's own* Inputs/Outputs field name (see
    # bat.plugins.schema.ModuleSchema): inputs map it to the artifact name
    # being consumed, outputs map it to the artifact name being produced.
    # Type/format for a real module output come from the module's own
    # OutputField declaration, not repeated here.
    inputs: dict[str, str] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    on_error: OnError | None = None


class Workflow(BaseModel):
    """A named, ordered sequence of steps."""

    model_config = ConfigDict(extra="forbid")

    id: str
    depends_on: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)
    on_error: OnError | None = None


class Protocol(BaseModel):
    """The top-level BAT protocol document."""

    model_config = ConfigDict(extra="forbid")

    version: str
    vars: dict[str, Any] = Field(default_factory=dict)
    # A protocol must contain at least one workflow (matches
    # bat.engine.validation and the spec). Guarded by the parity test in
    # tests/test_validation_parity.py.
    workflows: list[Workflow] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_workflow_ids(self) -> Protocol:
        """Workflow IDs must be unique across the whole protocol."""
        seen: set[str] = set()
        for workflow in self.workflows:
            if workflow.id in seen:
                raise ValueError(
                    f"workflows: duplicate workflow id {workflow.id!r}"
                )
            seen.add(workflow.id)
        return self

    @model_validator(mode="after")
    def _validate_workflow_depends_on(self) -> Protocol:
        """Every workflow ``depends_on`` entry must name an existing workflow."""
        workflow_ids = {workflow.id for workflow in self.workflows}
        for workflow in self.workflows:
            for dep in workflow.depends_on:
                if dep not in workflow_ids:
                    raise ValueError(
                        f"workflows.{workflow.id}.depends_on: unknown "
                        f"workflow reference {dep!r}"
                    )
        return self

    @model_validator(mode="after")
    def _validate_unique_step_ids(self) -> Protocol:
        """Step IDs must be unique across the whole protocol."""
        seen: dict[str, str] = {}
        for workflow in self.workflows:
            for step in workflow.steps:
                if step.id in seen:
                    raise ValueError(
                        f"workflows.{workflow.id}.steps: duplicate step id "
                        f"{step.id!r} (already declared in workflow "
                        f"{seen[step.id]!r})"
                    )
                seen[step.id] = workflow.id
        return self

    @model_validator(mode="after")
    def _validate_step_depends_on(self) -> Protocol:
        """Step ``depends_on`` entries must reference a step id within the same workflow."""
        for workflow in self.workflows:
            step_ids = {step.id for step in workflow.steps}
            for step in workflow.steps:
                for dep in step.depends_on:
                    if dep not in step_ids:
                        raise ValueError(
                            f"workflows.{workflow.id}.steps.{step.id}."
                            f"depends_on: unknown step reference {dep!r} in "
                            f"workflow {workflow.id!r}"
                        )
        return self

    @model_validator(mode="after")
    def _validate_unique_artifact_names(self) -> Protocol:
        """Artifact names declared in ``outputs`` must be unique across the protocol."""
        seen: dict[str, tuple[str, str]] = {}
        for workflow in self.workflows:
            for step in workflow.steps:
                for artifact_name in step.outputs.values():
                    if artifact_name in seen:
                        prev_wf, prev_step = seen[artifact_name]
                        raise ValueError(
                            f"workflows.{workflow.id}.steps.{step.id}."
                            f"outputs: duplicate artifact name "
                            f"{artifact_name!r} (already declared by step "
                            f"{prev_step!r} in workflow {prev_wf!r})"
                        )
                    seen[artifact_name] = (workflow.id, step.id)
                if step.on_error is not None:
                    for artifact_name in step.on_error.output:
                        if artifact_name in seen:
                            prev_wf, prev_step = seen[artifact_name]
                            raise ValueError(
                                f"workflows.{workflow.id}.steps.{step.id}."
                                f"on_error.output: duplicate artifact name "
                                f"{artifact_name!r} (already declared by "
                                f"step {prev_step!r} in workflow "
                                f"{prev_wf!r})"
                            )
                        seen[artifact_name] = (workflow.id, step.id)
        return self

    @model_validator(mode="after")
    def _validate_artifact_inputs(self) -> Protocol:
        """Every ``inputs`` value must name a declared artifact.

        Full dependency-ordering resolution (i.e. checking that the artifact
        is produced by a step that actually runs *before* the consuming step
        in topological order) is deferred to later cards. At parse time we
        only check that the referenced artifact name exists somewhere in the
        protocol's declared outputs.
        """
        declared_artifacts: set[str] = set()
        for workflow in self.workflows:
            for step in workflow.steps:
                declared_artifacts.update(step.outputs.values())
                if step.on_error is not None:
                    declared_artifacts.update(step.on_error.output.keys())

        for workflow in self.workflows:
            for step in workflow.steps:
                for input_name, artifact_name in step.inputs.items():
                    if artifact_name not in declared_artifacts:
                        raise ValueError(
                            f"workflows.{workflow.id}.steps.{step.id}."
                            f"inputs.{input_name}: unknown artifact "
                            f"reference {artifact_name!r} (no step "
                            "declares this artifact as an output)"
                        )
        return self
