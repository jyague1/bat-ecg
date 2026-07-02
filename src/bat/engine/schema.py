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


class ArtifactRef(BaseModel):
    """A reference to a previously-declared artifact, used as a step input."""

    model_config = ConfigDict(extra="forbid")

    artifact: str


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
    inputs: dict[str, ArtifactRef] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, ArtifactDeclaration] = Field(default_factory=dict)
    on_error: OnError | None = None


class Workflow(BaseModel):
    """A named, ordered sequence of steps."""

    model_config = ConfigDict(extra="forbid")

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
    workflows: dict[str, Workflow] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_workflow_depends_on(self) -> "Protocol":
        """Every workflow ``depends_on`` entry must name an existing workflow."""
        workflow_names = set(self.workflows.keys())
        for wf_name, workflow in self.workflows.items():
            for dep in workflow.depends_on:
                if dep not in workflow_names:
                    raise ValueError(
                        f"workflows.{wf_name}.depends_on: unknown workflow "
                        f"reference {dep!r}"
                    )
        return self

    @model_validator(mode="after")
    def _validate_unique_step_ids(self) -> "Protocol":
        """Step IDs must be unique across the whole protocol."""
        seen: dict[str, str] = {}
        for wf_name, workflow in self.workflows.items():
            for step in workflow.steps:
                if step.id in seen:
                    raise ValueError(
                        f"workflows.{wf_name}.steps: duplicate step id "
                        f"{step.id!r} (already declared in workflow "
                        f"{seen[step.id]!r})"
                    )
                seen[step.id] = wf_name
        return self

    @model_validator(mode="after")
    def _validate_step_depends_on(self) -> "Protocol":
        """Step ``depends_on`` entries must reference a step id within the same workflow."""
        for wf_name, workflow in self.workflows.items():
            step_ids = {step.id for step in workflow.steps}
            for step in workflow.steps:
                for dep in step.depends_on:
                    if dep not in step_ids:
                        raise ValueError(
                            f"workflows.{wf_name}.steps.{step.id}.depends_on: "
                            f"unknown step reference {dep!r} in workflow "
                            f"{wf_name!r}"
                        )
        return self

    @model_validator(mode="after")
    def _validate_unique_artifact_names(self) -> "Protocol":
        """Artifact names declared in ``outputs`` must be unique across the protocol."""
        seen: dict[str, tuple[str, str]] = {}
        for wf_name, workflow in self.workflows.items():
            for step in workflow.steps:
                for artifact_name in step.outputs:
                    if artifact_name in seen:
                        prev_wf, prev_step = seen[artifact_name]
                        raise ValueError(
                            f"workflows.{wf_name}.steps.{step.id}.outputs: "
                            f"duplicate artifact name {artifact_name!r} "
                            f"(already declared by step {prev_step!r} in "
                            f"workflow {prev_wf!r})"
                        )
                    seen[artifact_name] = (wf_name, step.id)
                if step.on_error is not None:
                    for artifact_name in step.on_error.output:
                        if artifact_name in seen:
                            prev_wf, prev_step = seen[artifact_name]
                            raise ValueError(
                                f"workflows.{wf_name}.steps.{step.id}."
                                f"on_error.output: duplicate artifact name "
                                f"{artifact_name!r} (already declared by "
                                f"step {prev_step!r} in workflow "
                                f"{prev_wf!r})"
                            )
                        seen[artifact_name] = (wf_name, step.id)
        return self

    @model_validator(mode="after")
    def _validate_artifact_inputs(self) -> "Protocol":
        """Every ``inputs.*.artifact`` reference must name a declared artifact.

        Full dependency-ordering resolution (i.e. checking that the artifact
        is produced by a step that actually runs *before* the consuming step
        in topological order) is deferred to later cards. At parse time we
        only check that the referenced artifact name exists somewhere in the
        protocol's declared outputs.
        """
        declared_artifacts: set[str] = set()
        for workflow in self.workflows.values():
            for step in workflow.steps:
                declared_artifacts.update(step.outputs.keys())
                if step.on_error is not None:
                    declared_artifacts.update(step.on_error.output.keys())

        for wf_name, workflow in self.workflows.items():
            for step in workflow.steps:
                for input_name, artifact_ref in step.inputs.items():
                    if artifact_ref.artifact not in declared_artifacts:
                        raise ValueError(
                            f"workflows.{wf_name}.steps.{step.id}.inputs."
                            f"{input_name}: unknown artifact reference "
                            f"{artifact_ref.artifact!r} (no step declares "
                            "this artifact as an output)"
                        )
        return self
