# CARD-014: `bat init` Command

## Goal

Implement the `bat init <project-name>` command that scaffolds a new BAT project directory.

## Context

BAT is a command-line biomedical signal processing toolbox. New users start a project with `bat init`, which creates a directory with the standard project structure and a starter protocol file.

## Command

```bash
bat init my-project
```

Creates:

```text
my-project/
  protocol.yaml
  plugins/
  vars/
  runs/
```

## Starter `protocol.yaml`

The generated `protocol.yaml` should be a working example with comments explaining each section:

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

## Behavior

- Creates `<project-name>/` in the current working directory
- If `<project-name>/` already exists, raise an error and exit — do not overwrite
- Creates all subdirectories (`plugins/`, `vars/`, `runs/`)
- Writes the starter `protocol.yaml`
- Prints a success message with next steps:

```
Created project: my-project/

Get started:
  cd my-project
  bat run protocol.yaml --dry-run
```

## File location

```text
src/bat/cli/init.py    # bat init command implementation
```

## Tests

- Running `bat init my-project` creates the expected directory structure
- Running `bat init my-project` when `my-project/` already exists raises an error
- The generated `protocol.yaml` is valid YAML
- The generated `protocol.yaml` passes `bat validate`

## Acceptance criteria

- All directories are created
- `protocol.yaml` is written with starter content
- Existing directory collision raises an error with a clear message
- Success message includes next steps
