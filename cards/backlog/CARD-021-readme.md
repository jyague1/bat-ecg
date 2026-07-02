# CARD-021: Project README

## Goal

Write the user-facing README for the BAT project covering installation, quickstart, CLI reference, and project structure.

## Context

BAT (Biomedical Analysis Toolbox) is a command-line biomedical signal processing toolbox driven by declarative YAML protocols. It is inspired by Ansible: users describe what should happen in YAML, and an execution engine resolves the protocol, discovers modules, validates protocol definitions, and runs the requested workflows.

The hierarchy is: **protocol → workflow → step** (analogous to Ansible's playbook → play → task).

BAT v1 targets offline processing only. Real-time and streaming use cases are out of scope.

Target audience: biomedical researchers, students, and researchers contributing new algorithms as plugins.

## README sections to include

### 1. Title and one-line description

```
# BAT — Biomedical Analysis Toolbox
A declarative YAML-driven toolbox for reproducible biomedical signal processing.
```

### 2. Overview (2-3 paragraphs)

- What BAT is and why it exists
- The protocol → workflow → step model
- Key design values: reproducibility, explicit artifacts, stateless modules, plugin-extensible

### 3. Installation

```bash
pip install batecg
```

For development:

```bash
git clone <repo>
cd batecg
pip install -e ".[dev]"
```

### 4. Quickstart

Show a complete minimal example:

1. Initialize a project:
   ```bash
   bat init my-project
   cd my-project
   ```

2. Show the generated `protocol.yaml`

3. Run a dry run:
   ```bash
   bat run protocol.yaml --dry-run
   ```

4. Run the protocol:
   ```bash
   bat run protocol.yaml --var record=100
   ```

5. Show what the run directory contains

### 5. CLI Reference

Document all commands with their options:

- `bat run <protocol>` — options: `--dry-run`, `--run-name`, `--var`, `--vars-file`
- `bat dry-run <protocol>`
- `bat validate <protocol>`
- `bat plugins list` — options: `--verbose`, `--module`
- `bat plugins docs`
- `bat init <project-name>`

### 6. Protocol structure

Show an annotated example of a full protocol YAML with explanations of each section:
- `version`
- `vars`
- `workflows` with `depends_on`
- `steps` with `id`, `name`, `module`, `inputs`, `params`, `outputs`, `depends_on`, `on_error`

### 7. Variables

Explain the variable system:
- `{{ var }}` substitution
- Variable sources and precedence: CLI > vars-file > protocol vars > imported vars
- `--var` and `--vars-file`

### 8. Run directory

Show what a run directory looks like and what each file is:

```text
runs/2026-06-23_153012/
  resolved_protocol.yaml    # exact protocol that was executed
  provenance.yaml           # full provenance record
  logs/run.log              # plain text execution log
  artifacts/                # all step outputs
```

### 9. Plugins

Briefly explain:
- What a plugin is (a collection of modules)
- How to install a plugin (`pip install bat-lab-ecg`)
- How to use a local plugin (`plugins/` directory)
- `bat plugins list` and `bat plugins docs`

### 10. Artifact types

List the standard artifact types and their default formats:

| Type        | Default Format |
|-------------|----------------|
| signal      | WFDB           |
| annotations | WFDB           |
| features    | Parquet        |
| metadata    | YAML           |
| model       | ONNX           |
| report      | HTML           |
| error       | YAML           |

### 11. Reproducibility

Briefly explain what BAT records per run and why.

## File location

```text
README.md    # in the project root
```

## Acceptance criteria

- README covers all sections listed above
- All CLI commands are documented with options
- The quickstart is a working example using `core.wfdb.read`
- The protocol YAML example is syntactically correct
- No incorrect terminology: use "protocol", "workflow", "step" (not "playbook", "play", "task")
