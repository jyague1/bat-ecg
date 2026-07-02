"""The standard BAT plugin module interface.

Every BAT plugin module (a submodule discovered by
:func:`bat.plugins.discovery.discover_plugins`) must expose:

- a ``run(inputs, params, context=None) -> dict`` function -- the module's
  sole entry point, invoked by the engine to execute one protocol step;
- a ``schema`` attribute -- a :class:`bat.plugins.schema.ModuleSchema`
  subclass describing the module's parameters, inputs, and outputs (see
  ``bat.plugins.schema``).

A module execution is a pure function: ``Inputs + Parameters -> Outputs``.
Modules are stateless -- any state a module needs must be threaded through
``inputs``/``params`` or read from ``context``.

See ``cards/backlog/CARD-007-plugin-interface-schema.md`` for the full
spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# --- Type aliases ------------------------------------------------------

# The artifact dicts passed to / returned from a module's ``run()``. Keys
# are input/output names (matching the module's ``schema.Inputs`` /
# ``schema.Outputs`` field names); values are artifact objects/references
# whose concrete shape is defined by the (later) artifact-storage cards.
Artifacts = dict[str, Any]

# The (already-substituted -- no ``{{ var }}`` templating remaining)
# parameter values passed to a module's ``run()``, matching its
# ``schema.Params`` field names.
Params = dict[str, Any]


@dataclass
class BATContext:
    """Run-scoped context object optionally passed to a module's ``run()``.

    Provided by the engine when invoking a module; gives the module access
    to where it should read/write run-scoped artifacts and a logger that is
    already tagged with the current run.
    """

    run_dir: Path
    artifacts_dir: Path
    logger: logging.Logger


# The standard shape of a plugin module's ``run`` function.
RunFn = Callable[[Artifacts, Params, "BATContext | None"], Artifacts]
