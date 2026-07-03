# CARD-011: Error Handling

## Goal

Implement BAT's error handling model: default stop-on-failure behavior, `on_error: continue` with error artifact production, and downstream step continuation.

## Context

BAT stops by default on the first failed step. Steps can optionally declare `on_error` behavior to produce an error artifact and allow execution to continue. When a step continues on error, all downstream steps that depend on it still run — it is the responsibility of the protocol author to correctly wire downstream steps to handle the case where an upstream artifact may be an error artifact.

`error` is a first-class artifact type in BAT.

## Default behavior

```yaml
error_handling:
  default: stop
```

- BAT stops on the first failed step unless the step declares `on_error`
- BAT stops on the first failed workflow (i.e. if a workflow contains a failed step that was not handled, the entire run stops)
- A failed step logs the full traceback to `logs/run.log`

## `on_error: continue`

```yaml
steps:
  - id: load_record
    module: core.wfdb.read
    params:
      path: "data/{{ record }}"
    outputs:
      signal: raw_signal
    on_error:
      action: continue
      output:
        load_failure:
          type: error
          format: yaml
```

When a step with `on_error: action: continue` fails:
1. The exception is caught
2. An error artifact is produced and written to `artifacts/load_failure/`
3. The error artifact is registered in the `ArtifactRegistry`
4. Execution continues with the next step in topological order
5. Downstream steps that declared `depends_on: [load_record]` still run

## Error artifact structure

Error artifacts are stored as YAML under `artifacts/<artifact-name>/error.yaml`:

```yaml
artifact_name: load_failure
step_id: load_record
workflow_id: preprocess
module: core.wfdb.read
error_type: FileNotFoundError
message: "No such file or directory: 'data/100'"
traceback: |
  Traceback (most recent call last):
    ...
timestamp: "2026-06-23T15:30:15Z"
```

## Error handling at workflow level

Error handling may also be declared at the workflow level:

```yaml
workflows:
  - id: preprocess
    on_error:
      action: continue
    steps:
      ...
```

If a workflow has `on_error: continue`, a failed step within it (without its own `on_error`) causes the workflow to stop but the overall run to continue with the next workflow.

## Interface

```python
class StepExecutionError(Exception):
    """Raised when a step fails and error is not handled."""
    ...

def handle_step_error(
    exc: Exception,
    step: Step,
    workflow_id: str,
    on_error: OnError | None,
    registry: ArtifactRegistry,
    artifacts_dir: Path,
    logger: logging.Logger,
) -> bool:
    """
    Handle a step failure.
    Returns True if execution should continue, False if it should stop.
    Writes error artifact if on_error.output is declared.
    """
    ...
```

### Types used (defined in other cards)

- `Step`, `OnError` — from CARD-003 (`src/bat/engine/schema.py`)
- `ArtifactRegistry` — from CARD-008 (`src/bat/artifacts/registry.py`)

## File location

```text
src/bat/engine/errors.py    # StepExecutionError, handle_step_error
```

## Tests

- A step failure with no `on_error` raises `StepExecutionError` and stops the run
- A step failure with `on_error: continue` produces an error artifact and continues
- The error artifact is registered in the `ArtifactRegistry`
- The error YAML contains the correct fields
- A workflow-level `on_error: continue` stops the workflow but continues the run
- Downstream steps of a failed-but-continued step still execute

## Acceptance criteria

- Default behavior stops the run on the first unhandled step failure
- `on_error: continue` produces an error artifact and continues execution
- Downstream steps always run when the upstream step has `on_error: continue`
- Error artifacts are valid `Artifact` objects with type `error` and format `yaml`
- Full traceback is logged to `run.log`
