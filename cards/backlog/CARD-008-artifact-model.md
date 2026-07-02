# CARD-008: Artifact Model

## Goal

Implement the artifact object, global artifact registry, immutability enforcement, typed storage on disk, and artifact metadata model.

## Context

Artifacts are the communication mechanism between steps in BAT. Steps do not pass data directly — they declare outputs as artifacts, and downstream steps reference those artifacts by name. Artifacts are first-class objects in BAT.

Key properties:
- Globally unique name across the entire protocol run
- Immutable — once written, cannot be overwritten
- Typed (signal, annotations, features, metadata, model, report, error)
- Stored on disk under `runs/<run-id>/artifacts/`
- Provenance-tracked (creator step, creator module, parameters, input hashes, timestamp)

## Artifact types and default formats

```
signal      → wfdb
annotations → wfdb
features    → parquet
metadata    → yaml
error       → yaml
model       → onnx
report      → html
```

## Artifact object

```python
@dataclass
class Artifact:
    name: str                          # globally unique name declared in the protocol
    artifact_type: str                 # signal, annotations, features, etc.
    format: str                        # wfdb, parquet, yaml, onnx, html
    path: Path                         # absolute path on disk
    metadata: dict                     # arbitrary key-value metadata
    creator_module: str                # dotted module name
    creator_step: str                  # step ID
    creator_workflow: str              # workflow ID
    params: dict                       # params passed to the creator step
    input_artifact_names: list[str]    # names of input artifacts consumed
    input_hashes: dict[str, str]       # sha256 hashes of input artifacts where possible
    timestamp: datetime                # UTC creation time
```

## Artifact registry

The registry tracks all artifacts produced during a run. It is scoped to a single run.

```python
class ArtifactRegistry:
    def register(self, artifact: Artifact) -> None:
        """Register a new artifact. Raises if name already exists (immutability)."""
        ...

    def get(self, name: str) -> Artifact:
        """Retrieve an artifact by name. Raises if not found."""
        ...

    def exists(self, name: str) -> bool:
        ...

    def all(self) -> list[Artifact]:
        ...
```

## Disk storage

Artifacts are stored under:

```text
runs/<run-id>/artifacts/<artifact-name>/
```

Each artifact directory contains:
- The artifact data file(s) (format-dependent)
- `meta.yaml` — artifact metadata in YAML

Example `meta.yaml`:

```yaml
name: raw_signal
type: signal
format: wfdb
path: runs/2026-06-23_153012/artifacts/raw_signal/
creator_module: core.wfdb.read
creator_step: load_record
creator_workflow: preprocess
timestamp: "2026-06-23T15:30:15Z"
params:
  path: data/100
input_artifact_names: []
input_hashes: {}
metadata: {}
```

## Immutability enforcement

- `ArtifactRegistry.register()` raises `ArtifactConflictError` if an artifact with the same name already exists
- The engine also checks at the start of each step that its declared output names are not already in the registry

## Modules must not write undeclared artifacts

- The engine validates after each step that all artifacts declared in `outputs` are present in the registry
- Artifacts not declared in `outputs` but found in `artifacts/` (e.g. from a previous run mixed in) are ignored — the registry is the source of truth, not the directory

## Error artifacts

`error` is a valid artifact type. A failed step with `on_error: continue` may produce an error artifact. Error artifacts are stored as YAML and contain:
- Error message
- Traceback
- Step ID
- Timestamp

## File location

```text
src/bat/artifacts/model.py       # Artifact dataclass, ArtifactConflictError
src/bat/artifacts/registry.py    # ArtifactRegistry
src/bat/artifacts/storage.py     # disk read/write helpers, meta.yaml serialization
```

## Tests

- Registering an artifact with a duplicate name raises `ArtifactConflictError`
- `registry.get()` returns the correct artifact
- `meta.yaml` is written correctly to disk
- Error artifacts can be registered and stored

## Acceptance criteria

- `Artifact` dataclass captures all required provenance fields
- `ArtifactRegistry` enforces immutability
- `meta.yaml` is written alongside every artifact
- Error is a valid artifact type with YAML format
