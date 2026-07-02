# CARD-002: CLI Scaffold

## Goal

Wire up all `bat` CLI subcommands as stubs with correct signatures, help text, and argument/option definitions. No business logic yet — each command should print a "not implemented" message and exit 0.

## Context

BAT is a command-line biomedical signal processing toolbox. The CLI is the primary interface for users. It uses Click as the CLI framework (installed via CARD-001).

The hierarchy is: `bat` (root) → subcommand groups (`plugins`) → subcommands.

## Commands to implement

```bash
bat run <protocol>                        # Run a protocol
bat run <protocol> --dry-run              # Dry run (equivalent to bat dry-run)
bat dry-run <protocol>                    # Alias for bat run --dry-run
bat validate <protocol>                   # Validate protocol structure
bat plugins list                          # List discovered plugins
bat plugins list --verbose                # Verbose plugin listing
bat plugins list --module <name>          # Show a specific module
bat plugins docs                          # Generate plugin documentation
bat init <project-name>                   # Scaffold a new project
```

## Argument and option definitions

### `bat run <protocol>`
- `protocol`: positional argument, path to protocol YAML file (required, no default)
- `--dry-run`: boolean flag, equivalent to `bat dry-run`
- `--run-name`: optional string, names the run directory (e.g. `mitdb-baseline`)
- `--var`: multiple key=value pairs (e.g. `--var record=100 --var fs=360`)
- `--vars-file`: path to a YAML variable file

### `bat dry-run <protocol>`
- Same arguments as `bat run` minus `--dry-run` flag (it is implicit)

### `bat validate <protocol>`
- `protocol`: positional argument, path to protocol YAML file (required)

### `bat plugins list`
- `--verbose`: boolean flag
- `--module`: optional string, filter to a specific module by dotted name

### `bat plugins docs`
- No arguments in v1

### `bat init <project-name>`
- `project-name`: positional argument, name of the directory to create

## Structure

```text
src/bat/cli/
  __init__.py      # exports main Click group
  run.py           # bat run and bat dry-run
  validate.py      # bat validate
  plugins.py       # bat plugins (group with list and docs subcommands)
  init.py          # bat init
```

## Acceptance criteria

- All commands are reachable via `bat <command> --help`
- All commands print stub output and exit 0
- `bat run --dry-run protocol.yaml` and `bat dry-run protocol.yaml` both work
- No business logic implemented
