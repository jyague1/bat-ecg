# CARD-022: Plugin Authoring Guide

## Goal

Write the developer documentation that explains how to write, register, and document a BAT plugin module.

## Context

BAT plugins are the only place where biomedical algorithms live. The engine is algorithm-agnostic. A plugin is a Python package (or local directory) that exposes one or more modules. Each module implements a `run()` function and a Pydantic schema that describes its inputs, params, outputs, and citations.

Plugin names use dotted namespaces: `lab.ecg.detect_rpeaks`. The first segment is the collection namespace.

## Guide sections to include

### 1. What is a BAT plugin?

Explain the concept:
- A plugin is a collection of BAT modules
- A module is a stateless function: `Inputs + Parameters → Outputs`
- Modules communicate through artifacts — not by passing data directly
- All modules must declare their interface via a Pydantic schema

### 2. Plugin structure

Show a minimal plugin package:

```text
lab_ecg_plugin/
  __init__.py
  ecg/
    __init__.py
    detect_rpeaks.py
  pyproject.toml
```

`pyproject.toml`:

```toml
[project]
name = "bat-lab-ecg"
version = "0.1.0"
dependencies = ["batecg", "wfdb", "scipy"]

[project.entry-points."bat.plugins"]
lab = "lab_ecg_plugin"
```

The entry point key (`lab`) becomes the top-level namespace. All modules in this package are accessible under `lab.*`.

### 3. Writing a module

Show a complete module implementation:

```python
# lab_ecg_plugin/ecg/detect_rpeaks.py

from typing import Literal
from pydantic import BaseModel, Field
from bat.plugins.schema import ModuleSchema, InputField, OutputField
from bat.plugins.interface import BATContext
from bat.artifacts.model import Artifact
import wfdb
import numpy as np

class Schema(ModuleSchema):
    class Meta:
        name = "lab.ecg.detect_rpeaks"
        description = "Detect R-peaks in an ECG signal using the Pan-Tompkins algorithm."
        citations = [
            "Pan J, Tompkins W. A real-time QRS detection algorithm. "
            "IEEE Trans Biomed Eng. 1985;32(3):230-6."
        ]
        examples = [
            {
                "id": "detect_rpeaks",
                "name": "Detect R-peaks",
                "module": "lab.ecg.detect_rpeaks",
                "inputs": {"signal": {"artifact": "filtered_signal"}},
                "params": {"min_distance_ms": 200.0},
                "outputs": {"rpeaks": {"type": "annotations", "format": "wfdb"}},
            }
        ]

    class Params(BaseModel):
        min_distance_ms: float = Field(
            default=200.0, gt=0,
            description="Minimum distance between peaks in milliseconds"
        )

    class Inputs(BaseModel):
        signal: InputField(artifact_type="signal", artifact_format="wfdb")

    class Outputs(BaseModel):
        rpeaks: OutputField(artifact_type="annotations", artifact_format="wfdb")


schema = Schema


def run(inputs: dict, params: dict, context: BATContext | None = None) -> dict:
    signal_artifact = inputs["signal"]
    record = wfdb.rdrecord(str(signal_artifact.path / signal_artifact.name))

    # ... detect R-peaks using Pan-Tompkins ...

    output_path = context.artifacts_dir / "rpeaks"
    output_path.mkdir(parents=True, exist_ok=True)
    # ... write annotation file ...

    return {
        "rpeaks": Artifact(
            name="rpeaks",
            artifact_type="annotations",
            format="wfdb",
            path=output_path,
            metadata={"n_peaks": len(peak_samples)},
            creator_module="lab.ecg.detect_rpeaks",
            ...
        )
    }
```

### 4. Schema fields reference

Document all schema fields:

| Field | Required | Description |
|-------|----------|-------------|
| `Meta.name` | yes | Dotted module name matching the registry key |
| `Meta.description` | yes | Human-readable description |
| `Meta.citations` | yes | List of citation strings, or the string `"none"` |
| `Meta.examples` | no | List of example step YAML dicts |
| `Params` | yes (can be empty) | Pydantic model for parameters |
| `Inputs` | yes (can be empty) | Pydantic model for input artifacts |
| `Outputs` | yes (can be empty) | Pydantic model for output artifacts |

**Citations are required for all modules.** If your module has no applicable citation (e.g. a utility or I/O module), explicitly declare:
```python
citations = "none"
```

### 5. Artifact types and formats

| Type        | Default Format | Use case |
|-------------|----------------|----------|
| signal      | wfdb           | Raw or processed physiological signals |
| annotations | wfdb           | Event markers, labels, beat annotations |
| features    | parquet        | Extracted feature tables |
| metadata    | yaml           | Configuration or descriptive data |
| model       | onnx           | Trained ML models |
| report      | html           | Analysis reports |
| error       | yaml           | Error information from failed steps |

### 6. Using `BATContext`

Explain the `BATContext` object passed as `context`:

```python
@dataclass
class BATContext:
    run_dir: Path        # root of the current run directory
    artifacts_dir: Path  # where to write artifact files
    logger: Logger       # write log messages here
```

Always write output files inside `context.artifacts_dir`. Modules should not write outside the run directory.

### 7. Local plugins (no package required)

For quick local development, drop a module into the project `plugins/` directory:

```text
my-project/
  plugins/
    custom_lab/
      __init__.py
      rpeak_detector.py    # exposes run() and schema
```

The directory name (`custom_lab`) becomes the namespace. The module is accessible as `custom_lab.rpeak_detector`.

### 8. Testing your plugin

Recommend testing the `run()` function directly with mock inputs:

```python
from lab_ecg_plugin.ecg.detect_rpeaks import run, schema
from bat.artifacts.model import Artifact

def test_detect_rpeaks():
    inputs = {"signal": Artifact(...)}
    params = {"min_distance_ms": 200.0}
    result = run(inputs, params)
    assert "rpeaks" in result
```

### 9. Verifying discovery

```bash
pip install -e .
bat plugins list --module lab.ecg.detect_rpeaks
```

### 10. Generating docs

```bash
bat plugins docs
```

## File location

```text
docs/plugin-authoring-guide.md
```

## Acceptance criteria

- Guide covers all sections listed above
- The module example is complete and syntactically correct
- Citations requirement is clearly explained with the `"none"` option shown
- Local plugin setup is covered alongside the package approach
- All artifact types and formats are listed
- `BATContext` usage is explained
- Testing approach is shown
