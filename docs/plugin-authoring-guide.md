# Plugin Authoring Guide

This guide explains how to write, register, and document a BAT plugin
module. It's aimed at anyone adding a new biomedical algorithm (or a
utility/I/O step) to BAT — the engine itself is algorithm-agnostic, so
**every algorithm lives in a plugin module**.

All code examples in this guide were written against the real
implementation and verified to run (see the "Testing your plugin" and
"Writing a module" sections for the exact commands used).

## 1. What is a BAT plugin?

- A **plugin** is a collection of BAT **modules**, grouped under a dotted
  namespace (e.g. `lab.ecg.detect_rpeaks` — the first segment, `lab`, is
  the collection namespace).
- A **module** is a stateless function: `Inputs + Parameters -> Outputs`.
  Modules do not hold state between invocations; anything a module needs
  must come from its `inputs`, `params`, or the `context` object passed to
  it.
- Modules never pass data to each other directly. They communicate through
  **artifacts** — named, typed, immutable units of data (signals,
  annotations, feature tables, models, reports, ...) registered in a run's
  artifact registry. A step declares which artifacts it consumes as
  inputs and which it produces as outputs; the engine resolves artifact
  names to actual data on disk.
- Every module must declare its interface via a **Pydantic schema**: a
  `bat.plugins.schema.ModuleSchema` subclass describing its name,
  description, citations, parameters, inputs, and outputs. This schema is
  what the engine validates a protocol step against, what `bat plugins
  list` and `bat plugins docs` introspect, and what plugin discovery
  enforces (see "Verifying discovery" below).

A module file therefore exposes exactly two things at module scope:

- `schema` — a `ModuleSchema` subclass (the class itself, not an instance).
- `run(inputs, params, context=None) -> dict` — the module's entry point.

## 2. Plugin structure

A minimal plugin package looks like this:

```text
lab_ecg_plugin/
  __init__.py
  ecg/
    __init__.py
    detect_rpeaks.py
  pyproject.toml
```

`pyproject.toml` registers the package under the `bat.plugins` entry point
group:

```toml
[project]
name = "bat-lab-ecg"
version = "0.1.0"
dependencies = ["batecg", "wfdb", "scipy"]

[project.entry-points."bat.plugins"]
lab = "lab_ecg_plugin"
```

The entry point *name* (`lab`) becomes the top-level namespace under which
every module in the package is discoverable; the entry point *value*
(`lab_ecg_plugin`) is the importable package that BAT's plugin discovery
walks recursively for submodules exposing a `run` callable.

This is exactly the mechanism BAT itself uses for its own built-in
modules — `batecg`'s own `pyproject.toml` registers:

```toml
[project.entry-points."bat.plugins"]
core = "bat.core"
```

so `bat.core.wfdb.read` and `bat.core.wfdb.write` are discoverable as
`core.wfdb.read` and `core.wfdb.write`.

## 3. Writing a module

Below is a complete, working module — `lab.ecg.detect_rpeaks` — that reads
a `signal` artifact, detects peaks, and produces an `annotations`
artifact. It was written to closely follow BAT's own built-in
`core.wfdb.read` / `core.wfdb.write` modules (`src/bat/core/wfdb/read.py`,
`src/bat/core/wfdb/write.py`), which are the best reference for a
correct, complete module implementation. It was run end-to-end (feeding it
a real artifact produced by `core.wfdb.read.run()`) to confirm it's
syntactically and semantically correct before being written into this
guide.

```python
# lab_ecg_plugin/ecg/detect_rpeaks.py

from __future__ import annotations

from typing import Any

import wfdb
from pydantic import BaseModel, Field
from scipy.signal import find_peaks

from bat.artifacts.model import Artifact
from bat.plugins.interface import BATContext
from bat.plugins.schema import InputField, ModuleSchema, OutputField

#: This module's own schema output field name. The on-disk artifact
#: directory is named after this; if a protocol step declares a different
#: output name than "rpeaks", the engine remaps the returned artifact
#: automatically -- see "Output naming" below.
_OUTPUT_KEY = "rpeaks"


class DetectRPeaksSchema(ModuleSchema):
    class Meta:
        name = "lab.ecg.detect_rpeaks"
        description = "Detect R-peaks in an ECG signal by local-maximum thresholding."
        citations = [
            "Pan J, Tompkins W. A real-time QRS detection algorithm. "
            "IEEE Trans Biomed Eng. 1985;32(3):230-236."
        ]
        examples: list[dict] = []

    class Params(BaseModel):
        min_distance_ms: float = Field(
            default=200.0,
            gt=0,
            description="Minimum distance between consecutive peaks, in milliseconds.",
        )

    class Inputs(BaseModel):
        signal: InputField(artifact_type="signal", artifact_format="wfdb")

    class Outputs(BaseModel):
        rpeaks: OutputField(artifact_type="annotations", artifact_format="wfdb")


schema = DetectRPeaksSchema


def run(
    inputs: dict[str, Any], params: dict[str, Any], context: BATContext | None = None
) -> dict[str, Any]:
    """Detect R-peaks in the input ``signal`` artifact's first channel."""
    if context is None:
        raise ValueError("lab.ecg.detect_rpeaks requires a BATContext (context=None was given)")

    signal_artifact = inputs["signal"]

    # Don't assume the on-disk WFDB record name matches the artifact name --
    # discover the record file inside the artifact's directory instead.
    hea_files = list(signal_artifact.path.glob("*.hea"))
    if not hea_files:
        raise FileNotFoundError(
            f"lab.ecg.detect_rpeaks: no WFDB .hea file found under {signal_artifact.path}"
        )
    record = wfdb.rdrecord(str(hea_files[0].with_suffix("")))

    channel = record.p_signal[:, 0]
    min_distance_samples = max(int(params["min_distance_ms"] / 1000.0 * record.fs), 1)
    peak_samples, _ = find_peaks(channel, distance=min_distance_samples)

    out_dir = context.artifacts_dir / _OUTPUT_KEY
    out_dir.mkdir(parents=True, exist_ok=True)

    wfdb.wrann(
        _OUTPUT_KEY,
        "atr",
        sample=peak_samples.astype("int64"),
        symbol=["N"] * len(peak_samples),
        write_dir=str(out_dir),
    )

    artifact = Artifact(
        name=_OUTPUT_KEY,
        artifact_type="annotations",
        format="wfdb",
        path=out_dir,
        metadata={"n_peaks": int(len(peak_samples))},
        creator_module="lab.ecg.detect_rpeaks",
        params=dict(params),
    )

    return {_OUTPUT_KEY: artifact}
```

A few things worth calling out:

- **`Inputs`/`Outputs` fields are declared with `InputField`/`OutputField`
  used as the field's type *annotation***, not as a `Field(...)` default:
  `signal: InputField(artifact_type="signal", artifact_format="wfdb")`,
  with no `=`. This is intentional — `InputField`/`OutputField` return an
  `Annotated[str, Field(...)]` value that Pydantic treats as the field's
  declared type while still reading the constraints/metadata off the
  `FieldInfo`. See the docstring in `src/bat/plugins/schema.py` for the
  full rationale.
- **Output naming.** A module's `schema.Outputs` declares a *fixed* field
  name (here, `rpeaks`), but a protocol step is free to declare a
  different artifact name for that same output (e.g. `outputs: {my_peaks:
  {...}}`). A module has no way to know, from inside `run()`, what name a
  step gave its output — so it should simply return a dict keyed by its
  *own* schema's output field names, exactly as the example above does.
  BAT's executor (`bat.engine.executor._remap_outputs_to_step_names`)
  automatically remaps the returned dict's keys (and the artifact's
  `.name`) from the schema's output field name to the step's declared
  name whenever they differ. `core.wfdb.read` and `core.wfdb.write` rely
  on exactly this mechanism, which is why they always return `{"signal":
  ...}` / `{"exported_signal": ...}` regardless of what a step calls the
  output.
- Always raise a descriptive exception (with the module name in the
  message) on failure, and require `context` when the module needs to
  write files.

## 4. Schema fields reference

| Field | Required | Description |
|-------|----------|--------------|
| `Meta.name` | yes | Dotted module name matching the registry key (e.g. `lab.ecg.detect_rpeaks`) |
| `Meta.description` | yes | Human-readable description |
| `Meta.citations` | yes | List of citation strings, or the literal string `"none"` |
| `Meta.examples` | no | List of example step dicts (as would appear in a protocol YAML), e.g. `{"id": ..., "module": ..., "inputs": {...}, "params": {...}, "outputs": {...}}` |
| `Params` | yes (can be an empty `BaseModel`) | Pydantic model describing the module's parameters |
| `Inputs` | yes (can be an empty `BaseModel`) | Pydantic model describing the module's input artifacts, whose fields are declared with `InputField` |
| `Outputs` | yes (can be an empty `BaseModel`) | Pydantic model describing the module's output artifacts, whose fields are declared with `OutputField` |

If any of `Meta`, `Params`, `Inputs`, or `Outputs` is omitted entirely,
`ModuleSchema`'s own empty defaults are used — **except `Meta.citations`,
which is required** and is enforced at plugin discovery time (see
`bat.plugins.discovery._validate_module_interface`): a module that exposes
a `run` callable but has a missing or invalid `Meta.citations` fails
discovery with a `PluginDiscoveryError`.

**Citations are required for every module.** If your module wraps a
published algorithm, list the citation(s):

```python
citations = [
    "Pan J, Tompkins W. A real-time QRS detection algorithm. "
    "IEEE Trans Biomed Eng. 1985;32(3):230-6."
]
```

If your module has no applicable citation (a plain I/O or utility module,
for example), explicitly declare:

```python
citations = "none"
```

This is exactly what BAT's own `core.wfdb.read` and `core.wfdb.write`
do — they're plain I/O with no published algorithm behind them.

## 5. Artifact types and formats

Every artifact has a `artifact_type` and a `format`. The valid types and
their default on-disk formats (from `bat.artifacts.model.DEFAULT_FORMATS`,
shared by `bat.plugins.schema.DEFAULT_ARTIFACT_FORMATS`) are:

| Type          | Default Format | Use case |
|---------------|-----------------|----------|
| `signal`      | `wfdb`          | Raw or processed physiological signals |
| `annotations` | `wfdb`          | Event markers, labels, beat annotations |
| `features`    | `parquet`       | Extracted feature tables |
| `metadata`    | `yaml`          | Configuration or descriptive data |
| `model`       | `onnx`          | Trained ML models |
| `report`      | `html`          | Analysis reports |
| `error`       | `yaml`          | Error information from failed steps |

`InputField`/`OutputField` accept an explicit `artifact_format=` argument
if a module needs a non-default format for a given type; if omitted, the
type's default format above is used.

## 6. Using `BATContext`

The engine passes a `BATContext` instance as the third positional argument
to `run()` (it may be `None` if a module is invoked without one, so guard
against that if your module needs it, as in the example above). Its real
fields (`src/bat/plugins/interface.py`) are:

```python
@dataclass
class BATContext:
    run_dir: Path             # root of the current run directory
    artifacts_dir: Path       # where to write artifact files
    logger: logging.Logger    # write log messages here, already tagged with the current run
```

**Always write output files inside `context.artifacts_dir`.** Modules
should not write outside the run directory — `core.wfdb.write` even logs
a warning via `context.logger` if a caller-supplied output path resolves
outside of it. Read `context.run_dir` if you need the run root for some
other reason (e.g. resolving a relative path parameter), and use
`context.logger` instead of `print()` for diagnostic output so log
messages carry the run's context.

## 7. Local plugins (no package required)

For quick local development — or project-specific modules you don't want
to publish as an installable package — drop a module (or a package of
modules) into a `plugins/` directory next to your protocol file:

```text
my-project/
  plugins/
    custom_lab/
      __init__.py
      rpeak_detector.py    # exposes run() and schema
  my_protocol.yaml
```

Discovery (`bat.plugins.discovery.discover_plugins`) scans
`plugins/` relative to the current working directory. Each subdirectory
containing an `__init__.py` is treated as a namespaced collection (walked
recursively, exactly like an installed package) — the directory name
(`custom_lab`) becomes the top-level namespace, so `rpeak_detector.py`
inside it is discoverable as `custom_lab.rpeak_detector`. A bare `.py`
file placed directly under `plugins/` (no wrapping package) is also
supported: its filename (minus `.py`) becomes both the namespace and the
whole module in one.

Local and installed (entry-point) plugins are merged into one flat
registry; if the same dotted module name is produced by both, discovery
fails with a descriptive `PluginDiscoveryError` naming both sources.

## 8. Testing your plugin

The most direct way to test a module is to call its `run()` function with
mock `Artifact` inputs — no protocol file, engine, or CLI needed. This is
how BAT's own built-in modules are tested (see `tests/test_core_wfdb_read.py`).

```python
import logging

import pytest

from bat.plugins.interface import BATContext
from lab_ecg_plugin.ecg.detect_rpeaks import run, schema


@pytest.fixture
def context(tmp_path):
    run_dir = tmp_path / "run"
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True)
    return BATContext(run_dir=run_dir, artifacts_dir=artifacts_dir, logger=logging.getLogger("test"))


def test_detect_rpeaks(context, wfdb_signal_artifact):
    # `wfdb_signal_artifact` is any Artifact whose `.path` points at a
    # directory containing a WFDB record -- e.g. the output of running
    # `core.wfdb.read.run()` against a synthetic record written with
    # `wfdb.wrsamp()` in the test itself.
    result = run(
        {"signal": wfdb_signal_artifact},
        {"min_distance_ms": 200.0},
        context,
    )

    assert "rpeaks" in result
    assert result["rpeaks"].artifact_type == "annotations"
    assert result["rpeaks"].metadata["n_peaks"] > 0
```

This was verified against the real `detect_rpeaks` module above: a
synthetic WFDB record was written with `wfdb.wrsamp()`, fed through
`core.wfdb.read.run()` to produce a real `signal` Artifact, and that
Artifact was passed straight into `detect_rpeaks.run()` — it correctly
returned an `annotations` artifact with `n_peaks > 0`, and the schema's
`Meta.name`, `Meta.citations`, `Inputs`, and `Outputs` all matched
expectations.

Also assert on `schema.Meta.citations`, `schema.Inputs`, and
`schema.Outputs` directly where useful — they're plain classes, no
mocking required.

## 9. Verifying discovery

Once your plugin package is installed (editable installs work fine during
development):

```bash
pip install -e .
bat plugins list --module lab.ecg.detect_rpeaks
```

This prints the module's full detail block — description, source
(`installed (<namespace> <version>)` or `local (<namespace>)`), citations,
inputs, params, and outputs — confirming the module was discovered and its
schema is valid. For example, against the verified module above:

```text
lab.ecg.detect_rpeaks
  Description : Detect R-peaks in an ECG signal by local-maximum thresholding.
  Source      : local (lab)
  Citations   : Pan J, Tompkins W. A real-time QRS detection algorithm. IEEE Trans Biomed Eng. 1985;32(3):230-236.
  Inputs      : signal (type: signal, format: wfdb)
  Params      : min_distance_ms (float, default: 200.0)
  Outputs     : rpeaks (type: annotations, format: wfdb)
```

Run `bat plugins list` with no `--module` filter to see every discovered
module grouped by namespace (installed vs. local, with versions for
installed packages). If discovery fails — e.g. a missing `schema`
attribute, invalid `Meta.citations`, or a duplicate dotted name across two
sources — the command exits with a descriptive error instead of a
partial listing.

## 10. Generating docs

To generate full Markdown reference documentation for every discovered
plugin module (grouped by namespace, with a Parameters/Inputs/Outputs
table per module):

```bash
bat plugins docs
```

The Markdown is printed to stdout; redirect it to a file to save it, e.g.:

```bash
bat plugins docs > docs/plugins.md
```

This uses the same discovery as `bat plugins list` (installed
`bat.plugins` entry points plus the local `plugins/` directory, if any) —
no protocol file is required.
