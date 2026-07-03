# BAT — Biomedical Analysis Toolbox

## Initial Specification Draft

## 1. Overview

BAT, short for **Biomedical Analysis Toolbox**, is a command-line biomedical signal processing toolbox driven by declarative YAML protocols.

The design is inspired by Ansible: users describe what should happen in YAML, and an execution engine resolves the protocol, discovers modules, validates task definitions, and runs the requested workflows.

BAT is intended primarily for:

* Biomedical researchers
* Students
* Researchers contributing new algorithms as plugins

BAT v1 targets **offline processing** only. Real-time and streaming use cases are out of scope for the initial version.

---

## 2. Core Principles

### 2.1 Declarative execution

Users define workflows using YAML. The YAML describes workflows, steps, inputs, outputs, parameters, dependencies, variables, and imports.

### 2.2 Stateless modules

All modules are stateless.

A module execution is defined as:

```text
Inputs + Parameters → Outputs
```

Modules must not rely on hidden persistent state between executions.

### 2.3 Engine-agnostic signal processing

The BAT engine should not implement biomedical algorithms directly. Biomedical algorithms should live in plugins.

The core engine is responsible for:

* YAML parsing
* Top-level validation
* Workflow execution
* Plugin discovery
* Artifact management
* Provenance tracking
* Logging
* Core I/O modules
* CLI commands

### 2.4 Explicit artifacts

Steps communicate through explicitly declared artifacts.

Artifacts are:

* Named globally
* Immutable
* Typed
* Stored on disk
* Provenance-tracked

A step cannot overwrite an existing artifact.

---

## 3. Command Line Interface

The CLI command is:

```bash
bat
```

Initial commands:

```bash
bat run protocol.yaml
bat run protocol.yaml --dry-run
bat dry-run protocol.yaml
bat validate protocol.yaml
bat plugins list
bat plugins docs
bat init my-project
```

`bat run protocol.yaml --dry-run` and `bat dry-run protocol.yaml` are equivalent.

There is no default protocol filename. Users must explicitly pass a protocol path.

---

## 4. Project Structure

`bat init my-project` should scaffold:

```text
my-project/
  protocol.yaml
  plugins/
  vars/
  runs/
```

Local plugins are discovered from:

```text
plugins/
```

Run folders are created under:

```text
runs/
```

relative to the protocol file.

Example:

```text
runs/2026-06-23_153012/
```

Users may also name runs:

```bash
bat run protocol.yaml --run-name mitdb-baseline
```

---

## 5. Run Directory

Each run creates a directory containing:

```text
runs/<run-id>/
  resolved_protocol.yaml
  provenance.yaml
  logs/
    run.log
  artifacts/
```

All artifacts produced during the run are stored under:

```text
artifacts/
```

There is no separate `outputs/` directory. All step outputs are artifacts and live in the same location. Users navigate `artifacts/` directly.

Logs are plain text.

The provenance manifest is YAML.

---

## 6. YAML Protocol Model

A BAT protocol may contain multiple workflows.

Workflows are executed **sequentially in topological order** derived from `depends_on` declarations. Workflows form a directed acyclic graph (DAG); execution is always single-threaded.

When multiple workflows are ready (no unresolved dependencies), YAML order is used as the tiebreaker.

A workflow may depend on artifacts from another workflow, but the dependency must be explicitly declared via `depends_on`.

Example:

```yaml
version: "0.1"

vars:
  record: "100"

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

  - id: features
    depends_on:
      - preprocess
    steps:
      - id: export_signal
        name: Export cleaned signal
        module: core.wfdb.write
        inputs:
          signal: raw_signal
        params:
          path: "{{ record }}"
        outputs:
          exported_signal: exported_signal
```

---

## 7. Includes, Imports, and Variables

BAT supports static imports only in v1.

Imports are resolved at parse time before execution starts. The imported file is inlined into the protocol. Import paths must be static strings — variables are not allowed in import paths.

Dynamic includes (runtime resolution, variable paths) are deferred to a future version.

Imports may define:

* Variables
* Steps
* Workflows

BAT supports variable files:

```bash
bat run protocol.yaml --vars-file vars/mitdb.yaml
```

BAT supports CLI variables:

```bash
bat run protocol.yaml --var record=100
```

Variable precedence:

```text
CLI vars > CLI vars-file > protocol vars > imported vars
```

Environment variable substitution is not supported in v1.

Only simple substitution is supported. Expressions are not supported in v1.

---

## 8. Validation

`bat validate protocol.yaml` validates only the top-level YAML structure for now.

It does not resolve all imports in v1.

It does not check whether input files exist.

`bat dry-run protocol.yaml` resolves the protocol, builds the execution plan, and shows the planned workflow/step order without executing modules.

---

## 9. Workflows and Steps

The execution unit inside a workflow is called a **step**.

Every step must have:

* `id`
* `name`
* `module`

Every step may have:

* `depends_on`
* `inputs`
* `params`
* `outputs`
* `on_error`

Step IDs are required.

Artifact names are globally unique across the protocol.

### Step execution order

Steps within a workflow are executed **sequentially in topological order** derived from `depends_on` declarations. Steps form a directed acyclic graph (DAG); execution is always single-threaded — no two steps run concurrently.

When multiple steps are ready (no unresolved dependencies), YAML order is used as the tiebreaker.

Parallel step execution is deferred.

A step's `outputs` maps the module's own output field name to the artifact
name it should be registered as. `type`/`format` are not declared here --
they come from the module's own schema (see `bat.plugins.schema.OutputField`).

Example:

```yaml
outputs:
  signal: filtered_signal
```

`inputs` follows the same shape (module's own input field name -> the
artifact name it should be bound to):

```yaml
inputs:
  signal: raw_signal
```

---

## 10. Dependencies

Workflow dependencies are explicit:

```yaml
depends_on:
  - preprocess
```

Step dependencies are explicit:

```yaml
depends_on:
  - load_record
```

Artifact references do not automatically imply workflow dependencies. If one workflow uses artifacts from another workflow, the dependency must still be declared.

---

## 11. Error Handling

Default behavior:

```yaml
error_handling:
  default: stop
```

BAT stops on the first failed workflow.

BAT stops on failed steps unless the error is explicitly handled.

Handled errors may produce error artifacts.

Example:

```yaml
on_error:
  action: continue
  output:
    load_failure:
      type: error
      format: yaml
```

`error` is a first-class artifact type.

When a step has `on_error: continue`, all downstream steps that depend on it will still run. It is the responsibility of the protocol author to correctly wire downstream steps to handle the case where an upstream artifact may be an error artifact.

Error handling may be defined at both workflow level and step level.

---

## 12. Artifact Model

Artifacts are first-class objects.

Artifact properties:

* Globally unique ID
* Type
* Format
* Path
* Metadata
* Provenance
* Creator module
* Creator step
* Parameters
* Input artifact references
* Timestamp
* Input hashes where possible

Artifacts are immutable.

A step cannot produce an artifact with a name that already exists.

Modules must not write undeclared artifacts.

---

## 13. Artifact Types

Initial artifact types:

```text
signal
annotations
features
metadata
model
report
error
```

The engine defines standard artifact storage formats.

Recommended defaults:

```text
signal: WFDB
annotations: WFDB annotation
features: Parquet
metadata: YAML
error: YAML
model: ONNX
report: HTML
```

Model artifacts use ONNX as the standard format. Pickle/joblib are not supported in v1.

---

## 14. Signal Artifacts

Signal artifacts should support:

* Multi-channel signals
* Multi-rate signals
* Metadata
* Channel names
* Units
* Sampling rates
* Signal type
* Time represented as:

  * sample index
  * seconds
  * absolute timestamps

Mandatory metadata fields are deferred to implementation time.

Missing metadata should produce warnings rather than hard failures.

Metadata should be editable from YAML.

---

## 15. Core I/O

Core should include WFDB read and write modules.

Example module names:

```text
core.wfdb.read
core.wfdb.write
```

The rest of the biomedical processing functionality should be implemented as plugins.

---

## 16. Plugin System

BAT supports discoverable external plugins.

Plugins may come from:

* Installed Python packages
* Local project `plugins/` directory

### Discovery mechanism

**Installed packages** are discovered via Python entry points. A package declares itself as a BAT plugin collection in its `pyproject.toml`:

```toml
[project.entry-points."bat.plugins"]
lab = "lab_ecg_plugin"
```

The entry point key (e.g., `lab`) becomes the top-level namespace for all modules in that package.

**Local plugins** are discovered by scanning the project `plugins/` directory directly.

Both sources are unified at runtime. All discovered modules are accessible by dotted name.

### Namespacing

Plugin names use dotted namespaces. The first segment is the collection namespace, matching the entry point key for installed packages:

```text
core.wfdb.read
lab.ecg.detect_rpeaks
neurokit.ecg.clean
custom_lab.rpeak_detector
```

Duplicate plugin/module names are errors.

### Listing plugins

`bat plugins list` shows all discovered plugins from both sources.

```bash
bat plugins list
bat plugins list --verbose
bat plugins list --module core.wfdb.read
```

---

## 17. Plugin Interface

v1 interface:

```python
def run(inputs, params, context=None):
    return outputs
```

`context` is optional and not required for all modules.

Modules are stateless.

Every plugin module must provide a schema describing:

* Parameters
* Inputs
* Outputs
* Artifact types
* Artifact formats
* Examples
* Documentation
* Citations/references — required for all modules. Modules with no applicable citation must explicitly declare `citations: none`.

Schemas are defined using Pydantic models. JSON Schema is generated automatically from Pydantic for validation and documentation.

---

## 18. Documentation

Plugin documentation should be generated automatically from:

* Module schemas
* Docstrings
* Examples
* Citations

Command:

```bash
bat plugins docs
```

Documentation output should initially be Markdown.

---

## 19. Execution Mode

BAT v1 executes modules in-process.

This is simpler for researchers and students, easier to debug, and sufficient for offline workflows.

Subprocess isolation may be added later for:

* Fault isolation
* Memory control
* Security
* Unstable third-party plugins

---

## 20. Output Restrictions

By convention, modules should not write outside the run folder.

Step outputs go inside the current run directory. This keeps runs self-contained and reproducible.

This is a **convention in v1, not enforced at runtime**. Modules run in-process and there is no filesystem sandboxing. Plugin authors are expected to follow this rule; it is not technically prevented.

As a best-effort check, the engine validates that all artifacts declared in a step's `outputs` exist inside the run folder after the step completes.

True enforcement via subprocess isolation is deferred to a future version.

---

## 21. Reproducibility

Each run stores:

* Resolved protocol
* Plain text logs
* YAML provenance manifest
* Artifact metadata
* Module versions
* Plugin versions
* Output paths

A future `bat.lock` file should pin plugin/module versions.

Caching is deferred and not part of v1.

---

## 22. Deferred Features

The following are intentionally deferred:

* Real-time processing
* Streaming execution
* Caching
* Parallel workflow execution
* Subprocess isolation
* Distributed/cloud/HPC execution
* Deep validation modes
* Environment variable substitution
* Expression language in YAML
* Lock file enforcement
* Stateful modules
* `bat inspect` command
* Dynamic includes (runtime-resolved, variable paths)

---

## 23. Open Questions

The following decisions are deferred to implementation time:

1. Exact mandatory metadata fields for signal artifacts.
2. Exact WFDB storage layout for multi-rate signal artifacts.
