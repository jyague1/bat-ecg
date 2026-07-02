# BAT — Biomedical Analysis Toolbox

A declarative YAML-driven toolbox for reproducible biomedical signal processing.

## Overview

BAT is a command-line biomedical signal processing toolbox driven by declarative YAML **protocols**. Its design is inspired by Ansible: instead of writing imperative scripts, you describe *what* should happen in YAML, and an execution engine resolves the protocol, discovers modules, validates the definitions, and runs the requested workflows. BAT v1 targets **offline** processing only — real-time and streaming use cases are out of scope. It's aimed at biomedical researchers, students, and researchers contributing new algorithms as plugins.

A protocol is structured as a three-level hierarchy: a **protocol** contains one or more **workflows**, and each workflow contains an ordered sequence of **steps**. Workflows and steps each form a directed acyclic graph (DAG) via `depends_on` declarations, and execution is always single-threaded and deterministic: when more than one node is ready to run, YAML declaration order breaks the tie.

BAT is built around a few core design values:

- **Reproducibility.** Every run writes out the exact protocol it executed, a full YAML provenance manifest (module/plugin versions, timings, artifact hashes), and plain-text logs, so a run can be inspected or re-derived later.
- **Explicit artifacts.** Steps never pass data to each other directly. They declare named, typed **artifacts** as outputs, and downstream steps consume them by name. Artifacts are globally unique per run and immutable once produced.
- **Stateless modules.** A plugin module is a pure function: `inputs + params -> outputs`. Modules must not rely on hidden state between executions, which keeps runs predictable and easy to reason about.
- **Plugin-extensible.** The core engine only ships YAML parsing, validation, DAG execution, plugin discovery, artifact management, provenance tracking, and two core I/O modules (`core.wfdb.read` / `core.wfdb.write`). All actual biomedical algorithms live in plugins, discovered either from installed Python packages or a project-local `plugins/` directory.

## Installation

BAT is not yet published on PyPI. Install it directly from GitHub:

```bash
pip install git+https://github.com/jyague1/bat-ecg.git
```

For development:

```bash
git clone https://github.com/jyague1/bat-ecg.git
cd bat-ecg
pip install -e ".[dev]"
```

Installing the package provides the `bat` command-line tool.

## Quickstart

**1. Initialize a project:**

```bash
bat init my-project
cd my-project
```

This scaffolds:

```text
my-project/
  protocol.yaml
  plugins/
  vars/
  runs/
```

**2. The generated `protocol.yaml`:**

```yaml
version: "0.1"

# Variables can be overridden at runtime:
#   bat run protocol.yaml --var record=101
vars:
  record: "100"

workflows:
  load:
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
```

This one-step protocol reads a WFDB record (e.g. from the MIT-BIH Arrhythmia Database) at `data/<record>` using the core `core.wfdb.read` module, and declares its output as a `signal` artifact named `raw_signal`.

**3. Dry run** (validates the protocol, resolves modules, and prints the execution plan without running anything — no data files needed):

```bash
bat run protocol.yaml --dry-run
```

```text
Dry run: protocol.yaml
Run directory: runs/2026-07-02_151131/

Execution plan:
  [workflow] load
    [step] load_record          core.wfdb.read
```

**4. Run the protocol** (once `data/100.hea` / `data/100.dat` exist — e.g. a record from PhysioNet's MIT-BIH Arrhythmia Database):

```bash
bat run protocol.yaml --var record=100
```

```text
Run complete: runs/2026-07-02_151139/
  Workflows: 1
  Steps:     1
  Artifacts: 1
  Duration:  0.0s
```

**5. What the run directory contains:**

```text
runs/2026-07-02_151139/
  resolved_protocol.yaml       # exact protocol that was executed, vars substituted
  provenance.yaml              # full provenance record
  logs/
    run.log                    # plain text execution log
  artifacts/
    raw_signal/                # named after the artifact, e.g. "raw_signal"
      meta.yaml                # artifact metadata (type, format, creator, ...)
      signal.hea
      signal.dat
```

## CLI Reference

| Command | Description |
|---|---|
| `bat run PROTOCOL` | Run a protocol. |
| `bat dry-run PROTOCOL` | Resolve a protocol and print its execution plan without running any modules (alias for `bat run PROTOCOL --dry-run`). |
| `bat validate PROTOCOL` | Structurally validate a protocol file (schema shape, imports, cross-references) without executing it. |
| `bat plugins list` | List discovered plugin modules (installed packages + local `plugins/`). |
| `bat plugins docs` | Generate Markdown reference documentation for all discovered plugin modules. |
| `bat init PROJECT_NAME` | Scaffold a new BAT project directory. |

There is no default protocol filename — you must always pass a path explicitly.

### `bat run PROTOCOL [OPTIONS]`

| Option | Description |
|---|---|
| `--dry-run` | Validate and print the run plan without executing it (equivalent to `bat dry-run`). |
| `--run-name TEXT` | Name for the run directory (e.g. `mitdb-baseline`), instead of the default timestamp-based id. |
| `--var KEY=VALUE` | Set a protocol variable as `key=value`. May be repeated. |
| `--vars-file PATH` | Path to a YAML file of protocol variables. |

### `bat dry-run PROTOCOL [OPTIONS]`

Same options as `bat run` except `--dry-run` itself (it's always implied): `--run-name`, `--var`, `--vars-file`.

### `bat validate PROTOCOL`

No options beyond `--help`. Parses the YAML, resolves static imports, and validates the top-level schema shape — without executing modules, checking that input files exist, or requiring `{{ var }}` references to already be defined. Prints `PROTOCOL: OK` on success, or `PROTOCOL: INVALID` plus a list of every structural error found, and exits non-zero.

### `bat plugins list [OPTIONS]`

| Option | Description |
|---|---|
| `--verbose` | Show full detail (description, source, citations, inputs, params, outputs) for each discovered module instead of the compact grouped-by-namespace listing. |
| `--module TEXT` | Filter the listing to a single module by dotted name (implies verbose-style detail for just that module). |

Discovers plugins the same way a run would: installed `bat.plugins` entry points plus a `plugins/` directory relative to the current working directory. Does not require a protocol file.

### `bat plugins docs`

No options beyond `--help`. Generates Markdown reference documentation (one section per module: description, source, citations, a parameters table, and inputs/outputs tables) for every discovered module and prints it to stdout. Redirect to a file to save it, e.g. `bat plugins docs > docs/plugins.md`.

### `bat init PROJECT_NAME`

No options beyond `--help`. Refuses to run if `PROJECT_NAME/` already exists.

## Protocol structure

```yaml
version: "0.1"                      # protocol schema version

vars:                                # protocol-level variables, substitutable via {{ var }}
  record: "100"
  export_dir: "exported"

workflows:                           # one or more named workflows
  load:                              # workflow id
    steps:                           # ordered list of steps (min. 1)
      - id: load_record              # unique step id (unique across the whole protocol)
        name: Load WFDB record       # human-readable step name
        module: core.wfdb.read       # dotted plugin module name to invoke
        params:                      # module parameters ({{ var }} substitution applies)
          path: "data/{{ record }}"
        outputs:                     # artifacts this step produces
          raw_signal:                # artifact name (globally unique)
            type: signal             # artifact type (see Artifact types below)
            format: wfdb             # on-disk format
        on_error:                    # optional: what to do if this step fails
          action: continue           # "stop" (default) or "continue"
          output:                    # error artifact(s) to write if action: continue
            load_error:
              type: error
              format: yaml

  export:
    depends_on: [load]               # this workflow only runs after "load" completes
    steps:
      - id: export_record
        name: Re-export WFDB record
        module: core.wfdb.write
        depends_on: []                # step-level depends_on (within the same workflow)
        inputs:                       # artifacts consumed by this step
          signal:
            artifact: raw_signal      # references an artifact declared elsewhere in the protocol
        params:
          path: "{{ export_dir }}/{{ record }}"
        outputs:
          exported_signal:
            type: signal
            format: wfdb
```

Field reference:

- **`version`** — required protocol schema version string.
- **`vars`** — a flat mapping of protocol-level variables (see [Variables](#variables)).
- **`workflows`** — a mapping of workflow id to workflow definition. Workflows form a DAG via `depends_on`; a workflow that consumes another workflow's artifacts must still declare that dependency explicitly — artifact references alone don't imply ordering.
  - **`depends_on`** (workflow-level) — list of workflow ids that must complete first.
  - **`steps`** — an ordered, non-empty list of steps.
  - **`on_error`** (workflow-level) — if the workflow's own steps propagate an unhandled failure and `action: continue` is set here, that workflow stops but the run moves on to the next workflow rather than aborting entirely.
- **`steps[].id`** — required, unique step id across the entire protocol.
- **`steps[].name`** — required human-readable name.
- **`steps[].module`** — required dotted name of the plugin module to run (see `bat plugins list`).
- **`steps[].depends_on`** — list of step ids (within the same workflow) that must complete first.
- **`steps[].inputs`** — mapping of module input name to `{artifact: <name>}`, referencing an artifact declared as some step's output elsewhere in the protocol.
- **`steps[].params`** — arbitrary module parameters, validated against the module's own schema; string values may contain `{{ var }}` references.
- **`steps[].outputs`** — mapping of artifact name to its declared `type` and `format` (see [Artifact types](#artifact-types)). Output declarations must use this verbose form; there is no shorthand.
- **`steps[].on_error`** — step-level error handling; same shape as workflow-level `on_error` (`action: stop` (default) or `action: continue`, plus `output` artifacts to write on failure).

## Variables

Protocol strings may reference variables anywhere using `{{ var_name }}` syntax (params, paths, names, etc.). Only simple, flat substitution is supported in v1 — no expressions, filters, conditionals, or variable-referencing-variable chains. Partial substitution within a string works too, e.g. `"data/{{ record }}/ecg"`.

Variables can come from four sources, merged with this precedence (highest wins on conflict):

```text
CLI vars (--var) > CLI vars-file (--vars-file) > protocol vars (top-level `vars:`) > imported vars
```

- **`--var KEY=VALUE`** — set a single variable on the command line; may be repeated.
- **`--vars-file PATH`** — a YAML file containing a flat mapping of variable name to value.
- **protocol `vars:`** — the protocol's own top-level `vars` mapping.
- **imported vars** — variables merged in from files the protocol statically `imports` (imports are resolved entirely at parse time; import paths must be static strings, not `{{ var }}` expressions).

Example:

```bash
bat run protocol.yaml --var record=101 --vars-file vars/mitdb.yaml
```

If a `{{ var }}` reference has no value in the merged context, the run fails with an error naming the missing variable and the field where it was found.

## Run directory

Every `bat run` (or `bat dry-run`, for the plan only) creates a new, self-contained directory under `runs/` (relative to the protocol file), named either `runs/<run-name>/` (if `--run-name` was given) or a timestamp-based id `runs/YYYY-MM-DD_HHmmss/`. A run never overwrites an existing named run directory.

```text
runs/2026-07-02_151139/
  resolved_protocol.yaml    # exact protocol that was executed (imports inlined, vars substituted)
  provenance.yaml           # full provenance record: environment, per-workflow/per-step status, artifacts
  logs/
    run.log                 # plain text execution log
  artifacts/                # all step outputs, one subdirectory per artifact
    raw_signal/              # runs/<run-id>/artifacts/<artifact-name>/
      meta.yaml               # written alongside every artifact
      signal.hea
      signal.dat
```

There is no separate `outputs/` directory — every step output is an artifact and lives under `artifacts/`. By convention, modules should not write outside the run's `artifacts/` directory (this is a v1 convention, not sandboxed/enforced at the filesystem level — but the engine does a best-effort check after each step that its declared outputs actually exist inside the run folder).

## Plugins

A **plugin** is a collection of modules under a shared dotted namespace (e.g. `core.wfdb.read`, `lab.ecg.detect_rpeaks`). Modules are the unit referenced by a step's `module:` key; each one implements `run(inputs, params, context=None) -> outputs` plus a `schema` describing its parameters, inputs, outputs, and citations.

Plugins are discovered from two sources, merged into one registry:

- **Installed Python packages**, declared via the `bat.plugins` entry point group in their `pyproject.toml`:

  ```toml
  [project.entry-points."bat.plugins"]
  lab = "lab_ecg_plugin"
  ```

  Install one with e.g. `pip install bat-lab-ecg`; its modules become available under whatever namespace it registers (e.g. `lab.*`).

- **A local `plugins/` directory**, relative to the protocol file. Each subdirectory (a Python package with `__init__.py`) or top-level `.py` file directly under `plugins/` becomes a namespace, without needing to be installed as a package at all — handy for project-specific or in-development modules.

A module name colliding between two sources (or twice within a source) is a discovery error.

Inspect what's available with:

```bash
bat plugins list                 # compact, grouped by namespace/source
bat plugins list --verbose       # full detail per module
bat plugins list --module core.wfdb.read
bat plugins docs                 # full Markdown reference for every discovered module
```

BAT ships one built-in plugin namespace, `core`, providing the two core I/O modules `core.wfdb.read` and `core.wfdb.write`. Everything else — filtering, feature extraction, R-peak detection, models, etc. — is expected to live in plugins.

## Artifact types

Steps communicate exclusively through named, typed **artifacts** — never directly. Each artifact type has a recommended default on-disk format:

| Type        | Default Format |
|-------------|----------------|
| signal      | WFDB           |
| annotations | WFDB           |
| features    | Parquet        |
| metadata    | YAML           |
| model       | ONNX           |
| report      | HTML           |
| error       | YAML           |

Artifacts are globally unique by name within a run, immutable once produced, and provenance-tracked (creator step/module/workflow, params, input hashes, timestamp). `error` is a first-class artifact type: a step whose `on_error.action` is `continue` writes an `error`-typed artifact for each name declared under `on_error.output` instead of aborting the run.

## Reproducibility

To make a run inspectable and reconstructable after the fact, BAT records, per run:

- **`resolved_protocol.yaml`** — the exact protocol that was executed, with static imports already inlined and every `{{ var }}` reference already substituted, so there's no ambiguity about what actually ran.
- **`provenance.yaml`** — a full manifest covering the run's identity and overall status, the *environment* it ran in (Python version, the installed `batecg` version, and the version/source — installed vs. local — of every plugin namespace used), a per-workflow/per-step breakdown of status and timing, and every artifact produced (type, format, path, creator step/module, timestamp, and best-effort SHA-256 hashes of its input artifacts' files).
- **`logs/run.log`** — a plain-text execution log of every workflow/step as it starts and finishes (or fails).
- **`artifacts/`** — the actual on-disk data every step produced, so downstream inspection or reruns don't need to guess where output ended up.

Because modules are stateless and artifacts are immutable, this combination is intended to make "what exactly happened, and can I get the same result again" answerable purely by reading a run directory — without needing to re-run anything or dig through code.
