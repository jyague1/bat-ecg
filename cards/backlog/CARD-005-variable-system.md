# CARD-005: Variable System

## Goal

Implement `{{ var }}` substitution throughout protocol YAML values, variable file loading, and CLI variable injection with correct precedence.

## Context

BAT protocols support simple variable substitution using `{{ var_name }}` syntax in string values. Only simple substitution is supported — no expressions, filters, or logic in v1.

Variables can come from four sources with this precedence (highest to lowest):

```
CLI vars > CLI vars-file > protocol vars > imported vars
```

Environment variable substitution is not supported in v1.

## Variable sources

### 1. Protocol vars (declared in the YAML)
```yaml
vars:
  record: "100"
  fs: 360
```

### 2. Imported vars (from imported files, lowest precedence)
Merged in by the import resolver (CARD-004) before substitution.

### 3. CLI vars-file
```bash
bat run protocol.yaml --vars-file vars/mitdb.yaml
```
`vars/mitdb.yaml`:
```yaml
record: "200"
fs: 500
```

### 4. CLI vars (highest precedence)
```bash
bat run protocol.yaml --var record=100 --var fs=360
```

## Substitution rules

- Substitution applies to string values anywhere in the protocol (params, paths, names, etc.)
- Only `{{ var_name }}` syntax is supported — no filters, conditionals, or expressions
- Whitespace inside the braces is ignored: `{{ record }}` and `{{record}}` are equivalent
- If a variable is referenced but not defined, raise a descriptive error naming the missing variable and the field where it appears
- Substitution is applied after imports are resolved and variables from all sources are merged

## Interface

```python
def build_var_context(
    protocol_vars: dict,
    imported_vars: dict,
    vars_file: Path | None,
    cli_vars: dict[str, str],
) -> dict:
    ...

def substitute_vars(raw: dict, context: dict) -> dict:
    ...
```

- `build_var_context` merges all variable sources in precedence order and returns the final context dict
- `substitute_vars` walks the raw protocol dict recursively and replaces `{{ var }}` in all string values

Both are called after import resolution and before Pydantic schema validation:

```python
def load_protocol(path, vars_file=None, cli_vars=None) -> Protocol:
    raw = yaml.safe_load(path.read_text())
    raw, imported_vars = resolve_imports(raw, base_path=path.parent)
    context = build_var_context(raw.get("vars", {}), imported_vars, vars_file, cli_vars or {})
    raw = substitute_vars(raw, context)
    return Protocol.model_validate(raw)
```

## File location

```text
src/bat/engine/variables.py    # build_var_context, substitute_vars
```

## Tests

- Substitution replaces `{{ var }}` in string values
- Whitespace variants `{{var}}` and `{{ var }}` both work
- CLI vars override protocol vars
- CLI vars-file overrides protocol vars
- Protocol vars override imported vars
- Missing variable raises a descriptive error with field location
- Non-string values (int, float, list, dict) are not modified
- Partial substitution within a string works: `"data/{{ record }}/ecg"`

## Acceptance criteria

- All `{{ var }}` references in the resolved protocol are substituted before validation
- Precedence order is correct
- Missing variables produce clear error messages
- Expressions and filters are not supported and need not be handled
