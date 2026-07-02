"""``core.wfdb.write``: write a signal artifact to disk in WFDB format.

This is the second of BAT's two core I/O modules (see CARD-020, sibling of
``core.wfdb.read`` from CARD-019). It is plain I/O with no published
algorithm behind it, hence ``citations: none``.

See ``cards/backlog/CARD-020-core-wfdb-write.md`` for the full spec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import wfdb
from pydantic import BaseModel, Field

from bat.artifacts.model import Artifact
from bat.plugins.interface import BATContext
from bat.plugins.schema import InputField, ModuleSchema, OutputField

#: The module's own schema output field name. The on-disk WFDB record
#: written inside the destination directory is named after this constant
#: (not the step's declared output name -- the module has no way to know
#: that from inside ``run()``); the engine's executor remaps the returned
#: ``Artifact.name`` to the step-declared name after ``run()`` returns (see
#: ``bat.engine.executor._remap_outputs_to_step_names``).
_OUTPUT_KEY = "exported_signal"

#: The fixed record name ``core.wfdb.read`` writes its output under (see
#: ``bat.core.wfdb.read._OUTPUT_KEY``). An input ``signal`` artifact's
#: ``.path`` is therefore a directory containing ``signal.hea`` /
#: ``signal.dat`` (etc.), regardless of what name the protocol step gave
#: that artifact.
_SOURCE_RECORD_NAME = "signal"


class WFDBWriteSchema(ModuleSchema):
    class Meta:
        name = "core.wfdb.write"
        description = "Write a signal artifact to disk in WFDB format at a specified path."
        citations = "none"
        examples: list[dict] = []

    class Params(BaseModel):
        path: str = Field(
            description=(
                "Output path for the WFDB record (without extension). "
                "Relative paths are resolved from the run directory."
            )
        )

    class Inputs(BaseModel):
        # InputField()/OutputField() return Annotated[...] used as the
        # field type; mypy can't follow this Pydantic DSL (bat.plugins.schema).
        signal: InputField(artifact_type="signal", artifact_format="wfdb")  # type: ignore[valid-type]

    class Outputs(BaseModel):
        exported_signal: OutputField(artifact_type="signal", artifact_format="wfdb")  # type: ignore[valid-type]


schema = WFDBWriteSchema


def run(
    inputs: dict[str, Any], params: dict[str, Any], context: BATContext | None = None
) -> dict[str, Any]:
    """Write the input ``signal`` artifact to disk as a WFDB record.

    Reads the source WFDB files from ``inputs["signal"].path`` (a directory
    containing a ``signal.hea`` / ``signal.dat`` record, per
    ``core.wfdb.read``'s on-disk convention) and re-writes them at
    ``params["path"]`` -- resolved relative to ``context.run_dir`` if
    relative, used as-is if absolute.

    ``params["path"]`` denotes the destination *directory*; the WFDB record
    itself is written inside it under this module's own schema output field
    name (mirroring ``core.wfdb.read``'s convention of naming the on-disk
    record after its schema output field).

    Per CARD-020, staying inside ``context.artifacts_dir`` is a convention,
    not an enforced restriction here -- a path that resolves outside it
    only logs a warning via ``context.logger`` and the write proceeds
    anyway. (The post-step ``check_step_outputs`` from CARD-013 is what
    actually catches and fails such a step, after the fact.)
    """
    if context is None:
        raise ValueError("core.wfdb.write requires a BATContext (context=None was given)")

    signal = inputs.get("signal")
    if signal is None:
        raise ValueError(
            "core.wfdb.write: missing required input 'signal' -- no signal "
            "artifact was provided"
        )

    src_record_path = Path(signal.path) / _SOURCE_RECORD_NAME
    try:
        record = wfdb.rdrecord(str(src_record_path))
    except Exception as exc:
        raise FileNotFoundError(
            f"core.wfdb.write: could not read source WFDB record at "
            f"{src_record_path} (from input artifact {signal.name!r}): {exc}"
        ) from exc

    raw_path = Path(params["path"])
    dst_path = raw_path if raw_path.is_absolute() else context.run_dir / raw_path

    artifacts_dir = context.artifacts_dir.resolve()
    if not dst_path.resolve().is_relative_to(artifacts_dir):
        context.logger.warning(
            "core.wfdb.write: output path %s is outside the run's artifacts "
            "directory (%s); this is convention-only in v1 and will not "
            "block the write, but a later output-restriction check may "
            "still fail the step",
            dst_path,
            context.artifacts_dir,
        )

    dst_path.mkdir(parents=True, exist_ok=True)

    wfdb.wrsamp(
        record_name=_OUTPUT_KEY,
        fs=record.fs,
        units=record.units,
        sig_name=record.sig_name,
        p_signal=record.p_signal,
        fmt=["16"] * record.n_sig,
        write_dir=str(dst_path),
    )

    artifact = Artifact(
        name=_OUTPUT_KEY,
        artifact_type="signal",
        format="wfdb",
        path=dst_path,
        metadata=dict(signal.metadata),
        creator_module="core.wfdb.write",
        params=dict(params),
    )

    return {_OUTPUT_KEY: artifact}
