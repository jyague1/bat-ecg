# CARD-019: `core.wfdb.read` Module

## Goal

Implement the `core.wfdb.read` BAT module that reads a WFDB record from disk and produces a signal artifact.

## Context

BAT is a biomedical signal processing toolbox. WFDB (WaveForm DataBase) is the standard format for physiological signals in PhysioNet datasets (e.g. MIT-BIH Arrhythmia Database). `core.wfdb.read` is one of the two core I/O modules included with BAT; all other biomedical processing lives in plugins.

This module must conform to the BAT plugin interface (CARD-007).

## Plugin interface (from CARD-007)

Every BAT module exposes:

```python
def run(inputs: dict, params: dict, context: BATContext | None = None) -> dict:
    ...
```

And a `schema` attribute — a class inheriting from `ModuleSchema` (defined in `src/bat/plugins/schema.py`):

```python
class ModuleSchema:
    class Meta:
        name: str
        description: str
        citations: list[str] | Literal["none"]
        examples: list[dict]
    class Params(BaseModel): ...
    class Inputs(BaseModel): ...
    class Outputs(BaseModel): ...
```

`BATContext` provides:
- `run_dir: Path`
- `artifacts_dir: Path`
- `logger: logging.Logger`

## Module specification

### Name
```
core.wfdb.read
```

### Description
Read a WFDB record from disk and produce a signal artifact.

### Citations
```
citations: none
```

### Params

| Name        | Type | Required | Default | Description |
|-------------|------|----------|---------|-------------|
| path        | str  | yes      |         | Path to the WFDB record (without extension). Relative paths are resolved from the working directory where `bat` is invoked. |
| channel_names | list[str] | no | null | Subset of channels to load. If null, all channels are loaded. |

### Inputs
None.

### Outputs

| Name   | Type   | Format | Description |
|--------|--------|--------|-------------|
| signal | signal | wfdb   | The loaded WFDB signal artifact |

## Implementation

Use the `wfdb` Python library:

```python
import wfdb

record = wfdb.rdrecord(params["path"], channel_names=params.get("channel_names"))
```

The output artifact must be stored in `context.artifacts_dir / <artifact_name> /`. For WFDB format, write the record back using `wfdb.wrsamp` or copy the source files — the exact storage approach is deferred to implementation (open question in specs). The artifact must be registered in the engine's `ArtifactRegistry` after the step completes (this is handled by the engine, not the module itself — the module just returns the artifact dict).

The module returns:

```python
return {
    "signal": Artifact(
        name="<declared output name from step>",
        artifact_type="signal",
        format="wfdb",
        path=context.artifacts_dir / "<output_name>",
        metadata={
            "n_channels": record.n_sig,
            "fs": record.fs,
            "channel_names": record.sig_name,
            "units": record.units,
            "n_samples": record.sig_len,
        },
        ...
    )
}
```

Note: The artifact `name` comes from the step's `outputs` declaration, not from the module itself. The engine passes the declared output name when building the artifact.

## File location

```text
src/bat/core/wfdb/read.py    # run() function and schema class
```

## Tests

- Loading a valid WFDB record returns an artifact with correct metadata
- `channel_names` param correctly filters channels
- A missing file raises a descriptive error
- The module schema passes citation enforcement (`citations: none`)
- The module is discoverable as `core.wfdb.read` via the plugin system

## Acceptance criteria

- `run()` loads a WFDB record and returns a signal artifact dict
- Artifact metadata includes `n_channels`, `fs`, `channel_names`, `units`, `n_samples`
- `citations: none` is explicitly declared
- The module is registered under `core.wfdb.read`
