# CARD-020: `core.wfdb.write` Module

## Goal

Implement the `core.wfdb.write` BAT module that takes a signal artifact and writes it to disk in WFDB format.

## Context

BAT is a biomedical signal processing toolbox. `core.wfdb.write` is one of the two core I/O modules included with BAT. It is used to export a processed signal artifact to a specified path, making results accessible outside the run artifacts directory.

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
core.wfdb.write
```

### Description
Write a signal artifact to disk in WFDB format at a specified path.

### Citations
```
citations: none
```

### Params

| Name   | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| path   | str  | yes      |         | Output path for the WFDB record (without extension). Relative paths are resolved from the run directory. |

### Inputs

| Name   | Type   | Format | Description |
|--------|--------|--------|-------------|
| signal | signal | wfdb   | The signal artifact to write |

### Outputs

| Name            | Type   | Format | Description |
|-----------------|--------|--------|-------------|
| exported_signal | signal | wfdb   | The written signal artifact (points to the output path) |

## Implementation

Use the `wfdb` Python library to write the signal. The input `signal` artifact contains the WFDB record data. Read the source WFDB files from `inputs["signal"].path` and write to the path specified in `params["path"]`.

The output artifact path should be inside `context.artifacts_dir` (following output restriction conventions). If the user specifies a path outside the run dir, log a warning but do not block — this is convention-only in v1 (CARD-013 handles the post-step check).

```python
import wfdb
import shutil

src_path = inputs["signal"].path
dst_path = Path(params["path"])

# Copy WFDB files (.hea, .dat, etc.) to destination
...
```

The module returns:

```python
return {
    "exported_signal": Artifact(
        name="<declared output name from step>",
        artifact_type="signal",
        format="wfdb",
        path=dst_path,
        metadata=inputs["signal"].metadata,  # preserve source metadata
        ...
    )
}
```

## File location

```text
src/bat/core/wfdb/write.py    # run() function and schema class
```

## Tests

- Writing a signal artifact produces WFDB files at the specified path
- The output artifact metadata matches the input signal metadata
- A missing input signal raises a descriptive error
- The module schema passes citation enforcement (`citations: none`)
- The module is discoverable as `core.wfdb.write` via the plugin system

## Acceptance criteria

- `run()` writes WFDB files to the specified path and returns an exported signal artifact
- Output artifact metadata is preserved from the input signal
- `citations: none` is explicitly declared
- The module is registered under `core.wfdb.write`
