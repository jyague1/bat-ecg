# CARD-010: DAG Execution Engine

## Goal

Implement topological sorting and sequential execution of workflows and steps, respecting `depends_on` declarations at both levels.

## Context

BAT protocols define a protocol → workflow → step hierarchy. Both workflows and steps form directed acyclic graphs (DAGs) via `depends_on` declarations. Execution is always single-threaded — no concurrency. Parallel execution is deferred to a future version.

The same DAG model applies at both levels:
- Workflows within a protocol form a DAG; executed in topological order
- Steps within a workflow form a DAG; executed in topological order
- When multiple nodes are ready (no unresolved dependencies), YAML declaration order is used as the tiebreaker

## Execution model

### Workflow-level

Given:
```yaml
workflows:
  - id: preprocess
    steps: [...]

  - id: features
    depends_on:
      - preprocess
    steps: [...]

  - id: report
    depends_on:
      - features
    steps: [...]
```

Execution order: `preprocess` → `features` → `report`

### Step-level

Given:
```yaml
steps:
  - id: load_record
    module: core.wfdb.read
    outputs:
      raw_signal: ...

  - id: filter_signal
    depends_on: [load_record]
    module: lab.ecg.filter
    inputs:
      signal:
        artifact: raw_signal
    outputs:
      filtered_signal: ...

  - id: detect_rpeaks
    depends_on: [filter_signal]
    module: lab.ecg.detect_rpeaks
    inputs:
      signal:
        artifact: filtered_signal
    outputs:
      rpeaks: ...
```

Execution order: `load_record` → `filter_signal` → `detect_rpeaks`

Branching is supported:
```yaml
steps:
  - id: load_record
    ...
  - id: filter_ecg         # depends on load_record
    depends_on: [load_record]
    ...
  - id: filter_eeg         # also depends on load_record (parallel branch, but sequential execution)
    depends_on: [load_record]
    ...
  - id: merge
    depends_on: [filter_ecg, filter_eeg]
    ...
```

Both `filter_ecg` and `filter_eeg` depend on `load_record` but not on each other. They form parallel branches but are still executed sequentially — YAML order determines which runs first.

## Topological sort

Implement Kahn's algorithm or DFS-based topological sort. Detect cycles and raise a descriptive error naming the cycle.

## Step execution

For each step (in topological order):

1. Resolve inputs — look up each `artifact` reference in the `ArtifactRegistry` (CARD-008)
2. Validate inputs against the module schema (CARD-007)
3. Validate params against the module schema
4. Look up the module in the plugin registry (CARD-006)
5. Call `module.run(inputs, params, context)` where `context` is a `BATContext` (CARD-007/009)
6. Validate that all declared outputs were produced and exist in `artifacts_dir`
7. Register outputs in the `ArtifactRegistry`

## Error handling integration

- If a step raises an exception and has no `on_error`, re-raise (stops the run)
- If a step raises and has `on_error: continue`, produce an error artifact and continue
- Full error handling logic is in CARD-011; this card only needs to call the error handler

## Interface

```python
def topological_sort(nodes: list[str], depends_on: dict[str, list[str]]) -> list[str]:
    """Returns node names in execution order. Raises on cycle."""
    ...

def execute_protocol(
    protocol: Protocol,
    registry: ArtifactRegistry,
    plugin_registry: dict,
    run_ctx: RunContext,
) -> None:
    """Execute all workflows and steps in topological order."""
    ...
```

### Types used (defined in other cards)

- `Protocol` — from CARD-003 (`src/bat/engine/schema.py`)
- `ArtifactRegistry` — from CARD-008 (`src/bat/artifacts/registry.py`)
- `RunContext` — from CARD-009 (`src/bat/engine/run.py`)
- Plugin registry dict — from CARD-006

## File location

```text
src/bat/engine/executor.py    # topological_sort, execute_protocol
```

## Tests

- Linear chain executes in correct order
- Branching DAG executes in topological order with YAML order as tiebreaker
- Cycle in workflow `depends_on` raises an error
- Cycle in step `depends_on` raises an error
- A step's inputs are resolved from the registry before execution
- A step that produces no declared outputs causes a post-step validation error

## Acceptance criteria

- `topological_sort` correctly orders nodes from a DAG
- `execute_protocol` runs all workflows and steps in correct order
- Cycles are detected and reported clearly
- Step inputs are resolved from `ArtifactRegistry` before calling `module.run()`
- Both workflow-level and step-level DAGs are handled
