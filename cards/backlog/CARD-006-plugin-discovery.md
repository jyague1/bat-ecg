# CARD-006: Plugin Discovery

## Goal

Implement plugin discovery from two sources: installed Python packages (via entry points) and the local project `plugins/` directory. Unify both into a single module registry at runtime.

## Context

BAT plugins are collections of modules. A module is the unit of execution — it maps to a `module:` key in a protocol step. Module names use dotted namespaces:

```
core.wfdb.read
lab.ecg.detect_rpeaks
neurokit.ecg.clean
custom_lab.rpeak_detector
```

The first segment of the dotted name is the collection namespace.

## Discovery sources

### 1. Installed Python packages (entry points)

A package declares itself as a BAT plugin collection in its `pyproject.toml`:

```toml
[project.entry-points."bat.plugins"]
lab = "lab_ecg_plugin"
```

- The entry point group is `bat.plugins`
- The key (e.g. `lab`) becomes the top-level namespace for all modules in that package
- The value is the importable Python package/module that contains the plugin collection

BAT discovers all installed packages that register under `bat.plugins` using `importlib.metadata.entry_points(group="bat.plugins")`.

### 2. Local `plugins/` directory

The local `plugins/` directory (relative to the protocol file) is scanned directly. Each subdirectory or Python file in `plugins/` is treated as a collection.

Example structure:
```text
plugins/
  custom_lab/
    __init__.py
    rpeak_detector.py   # defines module custom_lab.rpeak_detector
```

The directory name becomes the top-level namespace.

## Module registry

All discovered modules from both sources are merged into a single flat registry:

```python
# key: dotted module name, value: callable module object
registry: dict[str, ModuleInterface] = {}
```

A `ModuleInterface` is any Python object that exposes:
- `run(inputs, params, context=None)` — callable
- `schema` — a Pydantic model or object describing inputs, params, outputs, citations

(Full interface definition is in CARD-007.)

## Duplicate detection

If the same dotted module name is registered from more than one source, discovery raises an error naming both sources.

## Interface

```python
def discover_plugins(plugins_dir: Path | None) -> dict[str, Any]:
    ...
```

- `plugins_dir`: path to the local `plugins/` directory (may not exist — handle gracefully)
- Returns the unified module registry dict

## File location

```text
src/bat/plugins/discovery.py    # discover_plugins
```

## Tests

- Entry points with registered `bat.plugins` groups are discovered
- Local `plugins/` directory modules are discovered
- Both sources are merged into one registry
- Duplicate module names from different sources raise an error
- Missing `plugins/` directory is handled gracefully (returns empty registry from that source)
- A module with dotted name `lab.ecg.detect_rpeaks` is accessible as `registry["lab.ecg.detect_rpeaks"]`

## Acceptance criteria

- `discover_plugins(plugins_dir)` returns a unified registry of all available modules
- Entry point discovery uses `importlib.metadata`
- Duplicate names are detected and reported with source information
- Local plugins dir absence does not cause an error
