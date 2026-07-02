# CARD-013: Output Restriction Check

## Goal

Implement a post-step best-effort validation that all artifacts declared in a step's `outputs` exist inside the run directory after the step completes.

## Context

By convention, modules should not write outside the run folder. This is not enforced at the OS/filesystem level in v1 — modules run in-process and there is no sandboxing. This is a documented convention, not a technical barrier.

As a best-effort check, after each step executes, the engine validates that all artifacts declared in the step's `outputs` actually exist on disk inside `runs/<run-id>/artifacts/`. This catches the common case where a module writes to the wrong path by mistake.

True enforcement via subprocess isolation is deferred to a future version.

## What to check

After a step runs successfully (no exception raised):

For each artifact declared in `step.outputs`:
1. Check that the artifact was registered in `ArtifactRegistry` by the module
2. Check that the artifact's `path` is inside `run_ctx.artifacts_dir`
3. Check that at least one file exists at `artifact.path`

If any check fails, raise a `ArtifactViolationError` describing which artifact is missing or in the wrong location.

## What NOT to check

- Do not scan the filesystem for undeclared files written outside the run dir — this is too expensive and unreliable in-process
- Do not block module execution before it runs — the check is post-hoc only

## Interface

```python
class ArtifactViolationError(Exception):
    """Raised when a declared artifact is missing or outside the run directory."""
    ...

def check_step_outputs(
    step: Step,
    registry: ArtifactRegistry,
    run_ctx: RunContext,
) -> None:
    """
    Post-step check: verify all declared outputs are registered and
    exist inside the run artifacts directory. Raises ArtifactViolationError on failure.
    """
    ...
```

### Types used (defined in other cards)

- `Step` — from CARD-003 (`src/bat/engine/schema.py`)
- `ArtifactRegistry` — from CARD-008 (`src/bat/artifacts/registry.py`)
- `RunContext` — from CARD-009 (`src/bat/engine/run.py`)

## Integration

`check_step_outputs` is called by the execution engine (CARD-010) after every successful step invocation, before moving to the next step.

## File location

```text
src/bat/engine/checks.py    # ArtifactViolationError, check_step_outputs
```

## Tests

- A step whose declared outputs are all registered and inside `artifacts_dir` passes
- A step whose declared output is registered but path is outside `artifacts_dir` raises `ArtifactViolationError`
- A step whose declared output is not registered at all raises `ArtifactViolationError`
- A step whose declared output is registered but the file does not exist on disk raises `ArtifactViolationError`

## Acceptance criteria

- `check_step_outputs` runs after every successful step
- Missing or misplaced declared artifacts raise `ArtifactViolationError` with a clear message
- The check does not scan for undeclared files
- The check does not run if the step raised an exception (error handling takes over)
