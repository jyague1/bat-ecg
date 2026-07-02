# CARD-007: Plugin Interface and Schema

## Goal

Define and document the standard interface that all BAT plugin modules must implement, including the `run()` function signature and the Pydantic-based schema that describes the module to the engine.

## Context

BAT plugins are the only place where biomedical algorithms live. The engine is algorithm-agnostic — it only handles orchestration. Every module in a plugin must conform to this interface so the engine can validate steps, generate documentation, and invoke modules correctly.

Modules are stateless. A module execution is:

```
Inputs + Parameters → Outputs
```

## `run()` function

Every module must expose a `run` function:

```python
def run(inputs: dict, params: dict, context: BATContext | None = None) -> dict:
    ...
```

- `inputs`: dict mapping input names to artifact objects (see artifact model below)
- `params`: dict mapping parameter names to values (already substituted — no `{{ var }}` remaining)
- `context`: optional `BATContext` object provided by the engine (see below)
- Returns: dict mapping output names to artifact objects

## `BATContext`

```python
@dataclass
class BATContext:
    run_dir: Path           # current run directory
    artifacts_dir: Path     # run_dir / "artifacts"
    logger: logging.Logger  # run-scoped logger
```

## Module schema (Pydantic)

Every module must define a `schema` attribute — a Pydantic model class (not an instance) that describes the module:

```python
from pydantic import BaseModel, Field
from bat.plugins.schema import ModuleSchema, ParamField, InputField, OutputField

class MyModuleSchema(ModuleSchema):
    class Meta:
        name = "lab.ecg.detect_rpeaks"
        description = "Detect R-peaks in an ECG signal using the Pan-Tompkins algorithm."
        citations = ["Pan J, Tompkins W. A real-time QRS detection algorithm. IEEE TBME. 1985."]
        examples = [...]

    class Params(BaseModel):
        min_distance_ms: float = Field(default=200.0, gt=0, description="Minimum distance between peaks in ms")

    class Inputs(BaseModel):
        signal: InputField(artifact_type="signal", artifact_format="wfdb")

    class Outputs(BaseModel):
        rpeaks: OutputField(artifact_type="annotations", artifact_format="wfdb")
```

### `ModuleSchema` base fields

- `Meta.name`: str — dotted module name (must match the registry key)
- `Meta.description`: str — human-readable description
- `Meta.citations`: list[str] | Literal["none"] — **required for all modules**. Use `"none"` explicitly if no citation applies (e.g. for I/O modules).
- `Meta.examples`: list[dict] — example step YAML snippets (optional but recommended)
- `Params`: Pydantic BaseModel — parameter schema with types, defaults, constraints
- `Inputs`: Pydantic BaseModel — declared inputs with artifact type and format
- `Outputs`: Pydantic BaseModel — declared outputs with artifact type and format

### Artifact types

Valid values for `artifact_type`:
```
signal, annotations, features, metadata, model, report, error
```

### Artifact formats

Default formats per type:
```
signal:      wfdb
annotations: wfdb
features:    parquet
metadata:    yaml
error:       yaml
model:       onnx
report:      html
```

## `ModuleSchema` base class and helpers

Implement in `src/bat/plugins/schema.py`:

```python
class ModuleSchema:
    ...

def InputField(artifact_type: str, artifact_format: str) -> FieldInfo:
    ...

def OutputField(artifact_type: str, artifact_format: str) -> FieldInfo:
    ...
```

`ModuleSchema` must expose:
- `to_json_schema() -> dict` — export the full schema as JSON Schema (generated from Pydantic)

## Citations enforcement

At discovery time, the engine checks that every registered module has `Meta.citations` defined and is either a non-empty list of strings or the literal string `"none"`. Missing citations are a discovery error.

## File location

```text
src/bat/plugins/schema.py       # ModuleSchema, InputField, OutputField, BATContext
src/bat/plugins/interface.py    # BATContext dataclass, type aliases
```

## Tests

- A valid module schema passes citation enforcement
- A module with `citations: none` passes enforcement
- A module with missing `citations` raises a discovery error
- `to_json_schema()` returns a valid JSON Schema dict
- `InputField` and `OutputField` carry artifact type and format metadata

## Acceptance criteria

- `ModuleSchema` is the base class all plugin schemas must inherit from
- Citations are required and enforced at discovery time
- `to_json_schema()` works for any conforming schema
- `BATContext` is importable and used by the engine when invoking modules
