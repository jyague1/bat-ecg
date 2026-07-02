# CARD-015: `bat validate` Command

## Goal

Implement the `bat validate <protocol>` command that checks the top-level structure of a protocol YAML file.

## Context

`bat validate` is a quick structural check for protocol authors. It parses the YAML, resolves static imports, and validates the top-level schema — but it does not execute any modules, check whether input files exist, or fully resolve all dependencies.

## Command

```bash
bat validate protocol.yaml
```

## What it validates

1. The file exists and is valid YAML
2. The top-level structure matches the protocol schema (CARD-003):
   - `version` is present and a string
   - `workflows` is present and non-empty
   - Each workflow has at least one step
   - Each step has `id`, `name`, and `module`
   - Step IDs are unique across the protocol
   - Artifact names declared in `outputs` are unique across the protocol
   - `depends_on` references in workflows refer to existing workflow keys
   - `depends_on` references in steps refer to existing step IDs within the same workflow
3. Static imports are resolved (CARD-004) — imported files are read and inlined before validation
4. Variables are not substituted — `{{ var }}` tokens are left as-is for validation purposes

## What it does NOT validate

- Whether input files (e.g. `data/100`) exist on disk
- Whether referenced modules are installed
- Whether artifact type/format combinations are valid
- Full dependency resolution across workflows

## Output

On success:
```
protocol.yaml: OK
```

On failure:
```
protocol.yaml: INVALID

Errors:
  - workflows.preprocess.steps[0]: missing required field 'module'
  - workflows.features.depends_on[0]: 'nonexistent' does not refer to a known workflow
```

Exit code 0 on success, 1 on failure.

## Interface

The command calls `load_protocol` (CARD-003) in validation-only mode (skip variable substitution):

```python
def validate_protocol(path: Path) -> list[str]:
    """
    Returns a list of error messages. Empty list means valid.
    """
    ...
```

## File location

```text
src/bat/cli/validate.py         # bat validate command
src/bat/engine/validation.py    # validate_protocol function
```

## Tests

- A valid protocol prints "OK" and exits 0
- A protocol with a missing `module` field prints the error and exits 1
- A protocol with duplicate step IDs prints the error and exits 1
- A protocol with an invalid `depends_on` reference prints the error and exits 1
- A non-existent file prints a clear error and exits 1
- A file that is not valid YAML prints a clear error and exits 1

## Acceptance criteria

- Exit code 0 for valid protocols, 1 for invalid
- All structural errors are reported (not just the first one)
- Variable tokens `{{ var }}` are not required to be defined — validation skips substitution
- Imports are resolved before validation
