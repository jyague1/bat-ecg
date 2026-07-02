# CARD-001: Project Initialization

## Goal

Set up the Python project structure for BAT (Biomedical Analysis Toolbox) in the current directory. This is the foundation all other cards build on.

## Context

BAT is a command-line biomedical signal processing toolbox driven by declarative YAML protocols. It is inspired by Ansible: users describe what should happen in YAML, and an execution engine resolves the protocol, discovers modules, and runs the requested workflows.

The project uses a protocol → workflow → step hierarchy (analogous to Ansible's playbook → play → task).

BAT targets Python 3.10+.

## What to deliver

### Directory layout

```text
src/
  bat/
    __init__.py
    cli/
      __init__.py
    engine/
      __init__.py
    plugins/
      __init__.py
    artifacts/
      __init__.py
    core/
      __init__.py
      wfdb/
        __init__.py
tests/
  __init__.py
  conftest.py
pyproject.toml
```

### `pyproject.toml`

- Build backend: `hatchling` or `setuptools` with PEP 517
- Package name: `batecg`
- Entry point: `bat = "bat.cli:main"`
- Dependencies:
  - `click` — CLI framework
  - `pydantic>=2.0` — schema validation and JSON Schema generation
  - `pyyaml` — YAML parsing
  - `wfdb` — WFDB signal I/O
  - `onnx` — model artifact format
- Dev dependencies:
  - `pytest`
  - `pytest-cov`

### Entry point

`src/bat/cli/__init__.py` should expose a `main` function that is the Click group root. It can be a stub at this stage — just the root command group with a version option.

### Tests

`tests/conftest.py` should be empty or contain only placeholder fixtures. A smoke test that imports `bat` and checks `bat --help` exits 0 is sufficient.

## Acceptance criteria

- `pip install -e .` installs the package without errors
- `bat --help` runs without errors
- `pytest` discovers and passes the smoke test
- No business logic yet — stubs only
