# CARD-012: Provenance Tracking

## Goal

Implement the YAML provenance manifest written at the end of each run, capturing all artifacts, module versions, plugin versions, and output paths.

## Context

Reproducibility is a core principle of BAT. Every run stores a complete provenance record so that any result can be traced back to its inputs, the module that produced it, and the exact version of that module.

## What is stored per run

Each run directory contains:

```text
runs/<run-id>/
  resolved_protocol.yaml    # written before execution (CARD-009)
  provenance.yaml           # written after execution (this card)
  logs/run.log
  artifacts/
```

## `provenance.yaml` structure

```yaml
run_id: "2026-06-23_153012"
protocol: "protocol.yaml"
started_at: "2026-06-23T15:30:12Z"
finished_at: "2026-06-23T15:31:04Z"
status: "success"           # or "failed" or "partial"

environment:
  python_version: "3.11.4"
  bat_version: "0.1.0"
  plugins:
    - name: "core"
      version: "0.1.0"
      source: "installed"
    - name: "lab"
      version: "0.3.1"
      source: "installed"
    - name: "custom_lab"
      version: null
      source: "local"

workflows:
  preprocess:
    status: "success"
    started_at: "2026-06-23T15:30:12Z"
    finished_at: "2026-06-23T15:30:45Z"
    steps:
      load_record:
        status: "success"
        module: "core.wfdb.read"
        module_version: "0.1.0"
        started_at: "2026-06-23T15:30:12Z"
        finished_at: "2026-06-23T15:30:14Z"
        inputs: []
        outputs:
          - raw_signal
        params:
          path: "data/100"

artifacts:
  raw_signal:
    type: signal
    format: wfdb
    path: "artifacts/raw_signal/"
    creator_step: load_record
    creator_module: core.wfdb.read
    timestamp: "2026-06-23T15:30:14Z"
    input_hashes: {}
```

## Version capture

- `bat_version`: read from the installed `batecg` package metadata
- Plugin versions: read from `importlib.metadata` for installed packages; `null` for local plugins
- Module version: resolved from the plugin collection version

## Hashing

For provenance, compute SHA-256 hashes of input artifact files where feasible:
- Single-file artifacts: hash the file directly
- Multi-file artifacts (e.g. WFDB with `.dat` + `.hea`): hash each file, store as a dict

Hash computation is best-effort — if a file is too large or hashing fails, store `null` with a note.

## Interface

```python
@dataclass
class RunProvenance:
    run_id: str
    protocol_path: Path
    started_at: datetime
    finished_at: datetime | None
    status: str                         # "success", "failed", "partial"
    environment: dict
    workflow_records: list[WorkflowRecord]
    artifact_records: list[ArtifactRecord]

def write_provenance(run_ctx: RunContext, provenance: RunProvenance) -> None:
    """Write provenance.yaml to the run directory."""
    ...

def build_environment_record(plugin_registry: dict) -> dict:
    """Capture Python version, bat version, and all plugin versions."""
    ...
```

### Types used (defined in other cards)

- `RunContext` — from CARD-009 (`src/bat/engine/run.py`)
- `ArtifactRegistry` — from CARD-008 (`src/bat/artifacts/registry.py`)

## File location

```text
src/bat/engine/provenance.py    # RunProvenance, write_provenance, build_environment_record
```

## Tests

- `write_provenance` writes a valid YAML file to `run_dir/provenance.yaml`
- Plugin versions are captured from `importlib.metadata`
- Local plugins have `version: null`
- `status` is `"failed"` if any step failed without handling
- `status` is `"partial"` if some steps continued after error
- SHA-256 hashes are computed for artifact files

## Acceptance criteria

- `provenance.yaml` is written at run completion (success or failure)
- All artifacts are listed with type, format, path, creator, and hashes
- All plugin versions are captured
- Run status correctly reflects success, failure, or partial completion
