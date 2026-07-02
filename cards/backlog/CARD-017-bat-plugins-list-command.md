# CARD-017: `bat plugins list` Command

## Goal

Implement the `bat plugins list` command that shows all discovered plugin modules with optional verbosity and filtering.

## Context

BAT discovers plugins from two sources: installed Python packages (via entry points) and the local `plugins/` directory. `bat plugins list` shows what is currently discoverable in the environment.

Plugin module names use dotted namespaces:
```
core.wfdb.read
lab.ecg.detect_rpeaks
neurokit.ecg.clean
```

## Commands

```bash
bat plugins list                          # compact list of all modules
bat plugins list --verbose                # full details per module
bat plugins list --module core.wfdb.read  # details for a specific module
```

## Default output (`bat plugins list`)

```
Installed plugins:
  core      (0.1.0)   core.wfdb.read, core.wfdb.write
  lab       (0.3.1)   lab.ecg.filter, lab.ecg.detect_rpeaks, lab.hrv.extract

Local plugins (plugins/):
  custom_lab           custom_lab.rpeak_detector
```

## Verbose output (`bat plugins list --verbose`)

For each module, show:

```
core.wfdb.read
  Description : Read a WFDB record from disk
  Source      : installed (core 0.1.0)
  Citations   : none
  Inputs      : (none)
  Params      : path (str, required)
  Outputs     : signal (type: signal, format: wfdb)

lab.ecg.detect_rpeaks
  Description : Detect R-peaks using the Pan-Tompkins algorithm
  Source      : installed (lab 0.3.1)
  Citations   : Pan J, Tompkins W. A real-time QRS detection algorithm. IEEE TBME. 1985.
  Inputs      : signal (type: signal, format: wfdb)
  Params      : min_distance_ms (float, default: 200.0)
  Outputs     : rpeaks (type: annotations, format: wfdb)
```

## Single module output (`bat plugins list --module core.wfdb.read`)

Same as verbose output but for a single module. Exits with error if the module name is not found.

## Plugin discovery context

`bat plugins list` runs plugin discovery (CARD-006) using the `plugins/` directory in the current working directory (if it exists). It does not require a protocol file.

## Interface

The command uses `discover_plugins` from CARD-006 and reads module schemas from CARD-007:

```python
def format_plugin_list(registry: dict, verbose: bool) -> str:
    ...

def format_module_detail(module_name: str, module: Any) -> str:
    ...
```

## File location

```text
src/bat/cli/plugins.py    # bat plugins group, list and docs subcommands
```

## Tests

- `bat plugins list` shows all discovered modules grouped by collection
- `bat plugins list --verbose` shows schema details for each module
- `bat plugins list --module core.wfdb.read` shows details for that module
- `bat plugins list --module nonexistent` exits with an error
- Local plugins are listed separately from installed plugins
- Citations are shown correctly (including `none`)

## Acceptance criteria

- Default output groups modules by collection with versions
- Verbose output shows all schema fields
- `--module` filter works for both installed and local plugins
- Unknown module name exits 1 with a clear message
- Runs without a protocol file
