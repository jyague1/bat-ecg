# CARD-004: Static Import Resolution

## Goal

Implement parse-time inlining of imported files into the protocol before schema validation and execution.

## Context

BAT protocols support static imports only in v1. Imports are resolved at parse time — the imported file is read and its contents are merged into the protocol before any validation or execution occurs. Import paths must be static strings; variables are not allowed in import paths.

Dynamic includes (runtime resolution, variable paths) are deferred to a future version.

Imports may define:
- Variables
- Steps
- Workflows

## Import syntax

Imports are declared at the top level of a protocol or imported file:

```yaml
version: "0.1"

imports:
  - vars/common.yaml
  - workflows/preprocess.yaml

vars:
  record: "100"

workflows:
  - id: features
    steps:
      - id: export_signal
        name: Export signal
        module: core.wfdb.write
        inputs:
          signal: raw_signal
        outputs:
          exported_signal: exported_signal
```

An imported file (`workflows/preprocess.yaml`) might look like:

```yaml
vars:
  default_fs: 360

workflows:
  - id: preprocess
    steps:
      - id: load_record
        name: Load WFDB record
        module: core.wfdb.read
        params:
          path: "data/{{ record }}"
        outputs:
          signal: raw_signal
```

## Merge rules

- Imported `vars` are merged into the protocol `vars` (lower precedence than protocol-level vars — see variable precedence: CLI > vars-file > protocol vars > imported vars)
- Imported `workflows` are merged into the protocol `workflows` list by `id`
- Imported `steps` (if a file only defines steps with no workflow wrapper) are not supported in v1 — steps must be inside a workflow
- Import paths are resolved relative to the file that contains the `imports` key
- Circular imports are an error
- Imports are processed depth-first (an imported file may itself import other files)

## Interface

```python
def resolve_imports(raw: dict, base_path: Path) -> dict:
    ...
```

- `raw`: the raw parsed YAML dict (before Pydantic validation)
- `base_path`: directory of the file containing the `imports` key, used to resolve relative paths
- Returns a merged dict with all imports inlined, ready to be passed to the Pydantic schema

This function is called by `load_protocol` (CARD-003) before schema validation:

```python
def load_protocol(path: Path) -> Protocol:
    raw = yaml.safe_load(path.read_text())
    raw = resolve_imports(raw, base_path=path.parent)
    return Protocol.model_validate(raw)
```

## File location

```text
src/bat/engine/imports.py    # resolve_imports function
```

## Tests

- A protocol with no imports passes through unchanged
- Imported vars are merged with correct precedence
- Imported workflows are merged into the protocol
- Relative import paths resolve correctly
- Circular imports raise an error
- Import paths containing `{{ var }}` raise an error (not allowed in v1)
- Missing import file raises a descriptive error

## Acceptance criteria

- `resolve_imports` inlines all imports recursively before validation
- Merge precedence is correct (protocol vars override imported vars)
- Circular imports are detected and reported clearly
- Variable expressions in import paths are rejected
