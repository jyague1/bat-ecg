# CARD-003: Protocol Schema and Parser

## Goal

Implement the Pydantic schema for the full BAT protocol model and a YAML loader that parses a protocol file into validated Python objects.

## Context

BAT is driven by declarative YAML protocols. A protocol is the top-level file passed to `bat run protocol.yaml`. It contains variables, workflows, and steps. The hierarchy is:

```
protocol → workflows → steps
```

This is analogous to Ansible's playbook → play → task.

## Protocol YAML structure

```yaml
version: "0.1"

vars:
  record: "100"
  fs: 360

workflows:
  - id: preprocess
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}"
        outputs:
          raw_signal:
            type: signal
            format: wfdb

  - id: features
    depends_on:
      - preprocess
    steps:
      - id: export_signal
        name: Export cleaned signal
        module: core.wfdb.write
        inputs:
          signal:
            artifact: raw_signal
        params:
          path: "{{ record }}"
        outputs:
          exported_signal:
            type: signal
            format: wfdb
```

## Pydantic schema to implement

### Top-level: `Protocol`
- `version`: str (required)
- `vars`: dict[str, Any] (optional, default empty)
- `workflows`: list[Workflow] (required, at least one)

### `Workflow`
- `id`: str (required) — unique within the protocol
- `depends_on`: list[str] (optional, default empty) — references other workflow IDs
- `steps`: list[Step] (required, at least one)

### `Step`
- `id`: str (required) — unique within the protocol
- `name`: str (required)
- `module`: str (required) — dotted module name e.g. `core.wfdb.read`
- `depends_on`: list[str] (optional, default empty) — references other step IDs within the same workflow
- `inputs`: dict[str, ArtifactRef] (optional)
- `params`: dict[str, Any] (optional)
- `outputs`: dict[str, ArtifactDeclaration] (optional)
- `on_error`: OnError (optional)

### `ArtifactRef`
- `artifact`: str — globally unique artifact name declared by a previous step

### `ArtifactDeclaration`
- `type`: str — one of: `signal`, `annotations`, `features`, `metadata`, `model`, `report`, `error`
- `format`: str — e.g. `wfdb`, `parquet`, `yaml`, `html`, `onnx`

### `OnError`
- `action`: Literal["stop", "continue"] (default: "stop")
- `output`: dict[str, ArtifactDeclaration] (optional) — error artifacts to produce

## Validation rules

- Workflow IDs must be unique across the entire protocol
- Step IDs must be unique across the entire protocol (not just within a workflow)
- Artifact names declared in `outputs` must be unique across the entire protocol
- `depends_on` references in workflows must refer to existing workflow IDs
- `depends_on` references in steps must refer to existing step IDs within the same workflow
- `inputs.*.artifact` must reference an artifact name declared in a previous step's outputs (order based on topological sort — full resolution can be deferred; at parse time, check that the name exists somewhere in the protocol)

## Parser

```python
def load_protocol(path: Path) -> Protocol:
    ...
```

- Reads YAML from `path`
- Returns a validated `Protocol` object
- Raises a descriptive error if validation fails

## File location

```text
src/bat/engine/schema.py     # Pydantic models
src/bat/engine/loader.py     # load_protocol function
```

## Tests

- Valid protocol parses correctly
- Missing required fields raise validation errors
- Duplicate step IDs raise validation errors
- Duplicate artifact names raise validation errors
- Invalid `depends_on` references raise validation errors

## Acceptance criteria

- `load_protocol(path)` returns a `Protocol` object for a valid YAML file
- All validation rules are enforced at parse time
- Errors include the field path and a human-readable message
