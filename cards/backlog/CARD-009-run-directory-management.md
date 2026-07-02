# CARD-009: Run Directory Management

## Goal

Implement run directory creation, naming, layout initialization, resolved protocol writing, and log setup.

## Context

Every `bat run` invocation creates a new run directory. Runs are self-contained — all artifacts, logs, and metadata for a run live under the run directory. This is fundamental to BAT's reproducibility guarantee.

## Run directory location

Run directories are created under `runs/` relative to the protocol file:

```text
runs/<run-id>/
```

## Run ID format

Default (timestamp-based):
```text
runs/2026-06-23_153012/
```

Format: `YYYY-MM-DD_HHmmss` in local time.

Named run (via `--run-name`):
```bash
bat run protocol.yaml --run-name mitdb-baseline
```
Creates:
```text
runs/mitdb-baseline/
```

If a named run directory already exists, raise an error — do not overwrite.

## Run directory layout

```text
runs/<run-id>/
  resolved_protocol.yaml    # protocol after imports inlined and vars substituted
  provenance.yaml           # written at end of run (CARD-012)
  logs/
    run.log                 # plain text log for the entire run
  artifacts/                # all step artifacts (CARD-008)
```

## `resolved_protocol.yaml`

Written immediately after the protocol is loaded and resolved (imports inlined, vars substituted), before any step executes. This is the exact protocol the engine will execute.

## Logger setup

A run-scoped logger writes to `logs/run.log` in plain text. Format:

```
2026-06-23 15:30:12 INFO  [engine] Starting run mitdb-baseline
2026-06-23 15:30:12 INFO  [workflow.preprocess] Starting workflow
2026-06-23 15:30:13 INFO  [step.load_record] Running module core.wfdb.read
```

The logger is also passed to modules via `BATContext.logger` (defined in CARD-007).

## Interface

```python
@dataclass
class RunContext:
    run_id: str
    run_dir: Path
    artifacts_dir: Path
    logs_dir: Path
    logger: logging.Logger

def create_run(
    protocol_path: Path,
    run_name: str | None = None,
) -> RunContext:
    ...
```

- Creates the run directory and subdirectories
- Sets up the logger
- Returns a `RunContext` used throughout the run

```python
def write_resolved_protocol(run_ctx: RunContext, resolved: dict) -> None:
    ...
```

- Writes the resolved protocol dict to `resolved_protocol.yaml` in the run directory

## File location

```text
src/bat/engine/run.py    # RunContext, create_run, write_resolved_protocol
```

## Tests

- `create_run` creates the expected directory structure
- Timestamp-based run ID uses the correct format
- `--run-name` creates a named run directory
- Attempting to create a run dir that already exists raises an error
- `write_resolved_protocol` writes valid YAML to the correct path
- Logger writes to `logs/run.log`

## Acceptance criteria

- `create_run` returns a `RunContext` with all paths populated
- Run directory layout matches the spec exactly
- Named runs error on collision
- `resolved_protocol.yaml` is written before any step executes
- Log file is plain text with timestamps
