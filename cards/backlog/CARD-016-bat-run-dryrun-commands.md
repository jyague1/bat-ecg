# CARD-016: `bat run` and `bat dry-run` Commands

## Goal

Implement the full `bat run` execution path and `bat dry-run` mode, which resolves the protocol and prints the planned execution order without running any modules.

## Context

`bat run` is the primary command in BAT. It ties together all engine components: protocol loading, import resolution, variable substitution, plugin discovery, run directory creation, DAG execution, provenance tracking, and error handling.

`bat dry-run` is identical to `bat run --dry-run` — it resolves the protocol, builds the execution plan, and prints the planned workflow/step order without executing any modules.

## Commands

```bash
bat run protocol.yaml
bat run protocol.yaml --dry-run
bat run protocol.yaml --run-name mitdb-baseline
bat run protocol.yaml --var record=100 --var fs=360
bat run protocol.yaml --vars-file vars/mitdb.yaml

bat dry-run protocol.yaml     # equivalent to bat run --dry-run
```

## `bat run` execution sequence

1. Load and parse the protocol file (CARD-003)
2. Resolve static imports (CARD-004)
3. Build variable context and substitute `{{ var }}` (CARD-005)
4. Discover plugins from installed packages and `plugins/` dir (CARD-006)
5. Validate all `module:` references in steps exist in the plugin registry
6. Create the run directory (CARD-009)
7. Write `resolved_protocol.yaml` to the run directory (CARD-009)
8. Execute workflows and steps in topological order (CARD-010)
   - For each step: resolve inputs, validate, invoke module, check outputs, register artifacts
   - Handle errors per step/workflow `on_error` config (CARD-011)
   - Run post-step output restriction check (CARD-013)
9. Write `provenance.yaml` (CARD-012)
10. Print run summary

## `bat dry-run` execution sequence

Same as above through step 6 (run directory is created), then:
- Build and print the execution plan (workflow and step order)
- Do not invoke any modules
- Do not write `provenance.yaml`

Dry-run output format:

```
Dry run: protocol.yaml
Run directory: runs/2026-06-23_153012/

Execution plan:
  [workflow] preprocess
    [step] load_record        core.wfdb.read
    [step] filter_signal      lab.ecg.filter

  [workflow] features
    [step] detect_rpeaks      lab.ecg.detect_rpeaks
    [step] extract_hrv        lab.hrv.extract
```

## Module reference validation (step 5)

Before creating the run directory, check that every `module:` value in every step exists in the plugin registry. If any are missing, print all missing modules and exit with an error (no run directory is created).

## Run summary (success)

```
Run complete: runs/2026-06-23_153012/
  Workflows: 2
  Steps:     4
  Artifacts: 5
  Duration:  12.4s
```

## Run summary (failure)

```
Run failed: runs/2026-06-23_153012/
  Failed at step: filter_signal (workflow: preprocess)
  See logs: runs/2026-06-23_153012/logs/run.log
```

## File location

```text
src/bat/cli/run.py          # bat run and bat dry-run commands
src/bat/engine/runner.py    # run_protocol(), dry_run_protocol() orchestration functions
```

## Tests

- `bat run` on a valid protocol with stub modules completes successfully
- `bat run --dry-run` prints the execution plan without invoking modules
- `bat dry-run` is equivalent to `bat run --dry-run`
- `--var` overrides protocol vars
- `--vars-file` loads additional vars
- `--run-name` creates a named run directory
- A missing module reference exits with an error before creating the run directory
- A failed step (with no `on_error`) prints the failure summary and exits 1

## Acceptance criteria

- Full execution sequence is correctly orchestrated
- Dry-run prints the plan without executing modules
- `--var`, `--vars-file`, and `--run-name` all work
- Missing module references are caught before the run starts
- Exit code 0 on success, 1 on failure
