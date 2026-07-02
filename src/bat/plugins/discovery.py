"""Plugin discovery for BAT.

BAT plugins are collections of *modules* (the unit of execution referenced
by a step's ``module:`` key) organized under a dotted namespace, e.g.
``lab.ecg.detect_rpeaks``. Two independent sources of plugin collections
are discovered and merged into a single flat registry:

1. **Installed Python packages**, declared via the ``bat.plugins`` entry
   point group in a package's ``pyproject.toml``::

       [project.entry-points."bat.plugins"]
       lab = "lab_ecg_plugin"

   The entry point *name* (``lab``) becomes the top-level namespace; the
   entry point *value* (``lab_ecg_plugin``) is an importable package that is
   walked recursively for submodules.

2. **The local ``plugins/`` directory** (relative to the protocol file).
   Each subdirectory (a regular Python package, i.e. containing
   ``__init__.py``) or top-level ``.py`` file directly under ``plugins/``
   is treated as a collection; the directory/file name (minus ``.py``)
   becomes the top-level namespace.

A submodule is registered as a plugin *module* if it looks like one: it
must expose a callable ``run`` attribute. (Strict interface/schema
validation, including citation enforcement, is out of scope for this card
-- see ``cards/backlog/CARD-007-plugin-interface-schema.md``.)

If the same dotted module name is produced by more than one source, or
more than once within a source, discovery fails with
:class:`PluginDiscoveryError` naming both sources.

See ``cards/backlog/CARD-006-plugin-discovery.md`` for the full spec.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import pkgutil
import sys
from pathlib import Path
from typing import Any


class PluginDiscoveryError(Exception):
    """Raised when plugin discovery fails.

    This covers both duplicate module-name registration (the same dotted
    name produced by two different sources) and failures while importing a
    discovered module or package.
    """


def discover_plugins(plugins_dir: Path | None) -> dict[str, Any]:
    """Discover and merge all available BAT plugin modules.

    Args:
        plugins_dir: Path to the local ``plugins/`` directory. May be
            ``None`` or point to a nonexistent path -- in either case the
            local source simply contributes nothing to the registry.

    Returns:
        A flat ``dict`` mapping dotted module name (e.g.
        ``"lab.ecg.detect_rpeaks"``) to the imported module object (or any
        object exposing ``run``/``schema``).

    Raises:
        PluginDiscoveryError: If a plugin package/module fails to import,
            or if the same dotted module name is registered more than
            once (whether from the same source or across sources).
    """
    collected: dict[str, tuple[Any, str]] = {}

    for name, module, source in _discover_entry_point_plugins():
        _register(collected, name, module, source)

    for name, module, source in _discover_local_plugins(plugins_dir):
        _register(collected, name, module, source)

    return {name: module for name, (module, _source) in collected.items()}


def _register(
    collected: dict[str, tuple[Any, str]], name: str, module: Any, source: str
) -> None:
    """Add ``name -> module`` to ``collected``, raising on a collision."""
    if name in collected:
        _existing_module, existing_source = collected[name]
        raise PluginDiscoveryError(
            f"Duplicate plugin module {name!r}: registered from both "
            f"{existing_source} and {source}"
        )
    collected[name] = (module, source)


def _looks_like_plugin_module(obj: Any) -> bool:
    """Heuristic: a plugin module exposes a callable ``run``."""
    return callable(getattr(obj, "run", None))


def _discover_entry_point_plugins() -> list[tuple[str, Any, str]]:
    """Discover plugin collections registered via the ``bat.plugins`` entry
    point group on installed packages.
    """
    found: list[tuple[str, Any, str]] = []
    entry_points = importlib.metadata.entry_points(group="bat.plugins")

    for entry_point in entry_points:
        namespace = entry_point.name
        source = f"entry point {entry_point.name!r} ({entry_point.value})"
        try:
            package = entry_point.load()
        except ImportError as exc:
            raise PluginDiscoveryError(
                f"Failed to load plugin entry point {entry_point.name!r} "
                f"(-> {entry_point.value!r}): {exc}"
            ) from exc
        found.extend(_walk_collection(package, namespace, source))

    return found


def _discover_local_plugins(plugins_dir: Path | None) -> list[tuple[str, Any, str]]:
    """Discover plugin collections in the local ``plugins/`` directory."""
    found: list[tuple[str, Any, str]] = []

    if plugins_dir is None:
        return found

    plugins_dir = Path(plugins_dir)
    if not plugins_dir.is_dir():
        return found

    source = f"local plugins directory ({plugins_dir})"
    plugins_dir_str = str(plugins_dir.resolve())
    path_already_present = plugins_dir_str in sys.path
    if not path_already_present:
        sys.path.insert(0, plugins_dir_str)

    try:
        for entry in sorted(plugins_dir.iterdir(), key=lambda p: p.name):
            if entry.name.startswith((".", "_")):
                continue

            if entry.is_dir():
                if not (entry / "__init__.py").is_file():
                    # Not a regular package -- nothing we can import as a
                    # collection under a stable dotted name.
                    continue
                namespace = entry.name
                try:
                    package = importlib.import_module(namespace)
                except ImportError as exc:
                    raise PluginDiscoveryError(
                        f"Failed to import local plugin collection "
                        f"{namespace!r} from {plugins_dir}: {exc}"
                    ) from exc
                found.extend(_walk_collection(package, namespace, source))
            elif entry.is_file() and entry.suffix == ".py":
                namespace = entry.stem
                module = _import_file_module(namespace, entry)
                if _looks_like_plugin_module(module):
                    found.append((namespace, module, source))
    finally:
        if not path_already_present:
            try:
                sys.path.remove(plugins_dir_str)
            except ValueError:
                pass

    return found


def _walk_collection(package: Any, namespace: str, source: str) -> list[tuple[str, Any, str]]:
    """Walk a plugin collection (package or plain module), returning every
    submodule that looks like a plugin module, registered under
    ``<namespace>.<relative dotted path>``.

    If ``package`` is a plain module (no ``__path__``, i.e. not a package),
    it is itself checked and registered directly under ``namespace``.
    """
    found: list[tuple[str, Any, str]] = []

    if not hasattr(package, "__path__"):
        # A single importable module was registered directly as a
        # collection -- treat the module itself as the (only) plugin
        # module in that namespace.
        if _looks_like_plugin_module(package):
            found.append((namespace, package, source))
        return found

    prefix = package.__name__ + "."
    for module_info in pkgutil.walk_packages(package.__path__, prefix=prefix):
        submodule_name = module_info.name
        try:
            submodule = importlib.import_module(submodule_name)
        except ImportError as exc:
            raise PluginDiscoveryError(
                f"Failed to import plugin submodule {submodule_name!r} "
                f"from {source}: {exc}"
            ) from exc

        if not _looks_like_plugin_module(submodule):
            continue

        relative = submodule_name[len(package.__name__) + 1 :]
        found.append((f"{namespace}.{relative}", submodule, source))

    return found


def _import_file_module(name: str, path: Path) -> Any:
    """Import a standalone ``.py`` file as a module named ``name``."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PluginDiscoveryError(f"Could not load plugin module {name!r} from {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(name, None)
        raise PluginDiscoveryError(
            f"Failed to import plugin module {name!r} from {path}: {exc}"
        ) from exc

    return module
