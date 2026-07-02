"""``core.wfdb.read``: read a WFDB record from disk into a signal artifact.

WFDB (WaveForm DataBase) is the standard format for physiological signals
used across PhysioNet datasets (e.g. MIT-BIH Arrhythmia Database). This is
one of BAT's two core I/O modules (see CARD-019); it is plain I/O with no
published algorithm behind it, hence ``citations: none``.

See ``cards/backlog/CARD-019-core-wfdb-read.md`` for the full spec.
"""

from __future__ import annotations

from typing import Any

import wfdb
from pydantic import BaseModel, Field

from bat.artifacts.model import Artifact
from bat.plugins.interface import BATContext
from bat.plugins.schema import ModuleSchema, OutputField

#: The module's own schema output field name. The on-disk artifact
#: directory is named after this (not the step's declared output name --
#: the module has no way to know that from inside ``run()``); the engine's
#: executor remaps the returned ``Artifact.name`` to the step-declared name
#: after ``run()`` returns (see ``bat.engine.executor._remap_outputs_to_step_names``).
_OUTPUT_KEY = "signal"


class WFDBReadSchema(ModuleSchema):
    class Meta:
        name = "core.wfdb.read"
        description = "Read a WFDB record from disk and produce a signal artifact."
        citations = "none"
        examples: list[dict] = []

    class Params(BaseModel):
        path: str = Field(
            description=(
                "Path to the WFDB record (without extension). Relative "
                "paths are resolved from the working directory where "
                "`bat` is invoked."
            )
        )
        channel_names: list[str] | None = Field(
            default=None,
            description="Subset of channels to load. If null, all channels are loaded.",
        )

    class Inputs(BaseModel):
        pass

    class Outputs(BaseModel):
        signal: OutputField(artifact_type="signal", artifact_format="wfdb")


schema = WFDBReadSchema


def run(
    inputs: dict[str, Any], params: dict[str, Any], context: BATContext | None = None
) -> dict[str, Any]:
    """Read a WFDB record and return it as a ``signal`` artifact.

    ``params["path"]`` is passed straight through to ``wfdb.rdrecord`` --
    relative paths are resolved from the current working directory (WFDB's
    own convention). ``params.get("channel_names")`` optionally restricts
    which channels are loaded.

    The record is written back out to
    ``context.artifacts_dir / "signal" / `` (using this module's own
    schema output key as the on-disk directory name -- the executor
    remaps the returned artifact's ``.name`` to the step's actual declared
    output name afterward).
    """
    path = params["path"]
    channel_names = params.get("channel_names")

    try:
        record = wfdb.rdrecord(path, channel_names=channel_names)
    except Exception as exc:
        raise FileNotFoundError(
            f"core.wfdb.read: could not read WFDB record at path {path!r} "
            f"(resolved from the current working directory if relative): {exc}"
        ) from exc

    if context is None:
        raise ValueError("core.wfdb.read requires a BATContext (context=None was given)")

    out_dir = context.artifacts_dir / _OUTPUT_KEY
    out_dir.mkdir(parents=True, exist_ok=True)

    wfdb.wrsamp(
        record_name=_OUTPUT_KEY,
        fs=record.fs,
        units=record.units,
        sig_name=record.sig_name,
        p_signal=record.p_signal,
        fmt=["16"] * record.n_sig,
        write_dir=str(out_dir),
    )

    artifact = Artifact(
        name=_OUTPUT_KEY,
        artifact_type="signal",
        format="wfdb",
        path=out_dir,
        metadata={
            "n_channels": record.n_sig,
            "fs": record.fs,
            "channel_names": record.sig_name,
            "units": record.units,
            "n_samples": record.sig_len,
        },
        creator_module="core.wfdb.read",
        params=dict(params),
    )

    return {_OUTPUT_KEY: artifact}
