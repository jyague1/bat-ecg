"""Tests for plugin discovery (CARD-006).

Covers the acceptance criteria from
``cards/backlog/CARD-006-plugin-discovery.md``: installed packages that
register the ``bat.plugins`` entry point group are discovered, the local
``plugins/`` directory is scanned directly, both sources are merged into
one flat registry, duplicate dotted module names across (or within)
sources raise a descriptive error, and a missing/absent ``plugins/``
directory is handled gracefully.

Local-directory discovery is exercised with real files written under
``tmp_path`` -- no mocking needed. Entry-point discovery has no real
installed plugin package to test against, so each entry-point test builds
a tiny importable package under ``tmp_path``, puts it on ``sys.path`` (via
``monkeypatch.syspath_prepend``, which restores ``sys.path`` afterwards),
and monkeypatches ``importlib.metadata.entry_points`` to return an
``EntryPoint`` pointing at it -- this is the real API BAT's discovery code
calls, just fed a fake but fully functional entry point.

An autouse fixture snapshots ``sys.modules`` before each test and removes
any modules imported during the test afterward, since discovery imports
plugin (sub)modules by dotted name into the shared module cache and tests
reuse namespace names like ``lab`` across cases.
"""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path

import pytest

from bat.plugins.discovery import PluginDiscoveryError, discover_plugins


def write(base: Path, relpath: str, contents: str) -> Path:
    path = base / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    return path


# A module with ``run`` but no ``schema`` at all -- used only for tests that
# exercise modules *without* a callable ``run`` (so schema-conformance never
# comes into play), since CARD-007 requires every module that does expose
# ``run`` to also expose a valid ``schema``.
RUN_ONLY = "def run(inputs, params, context=None):\n    return {}\n"

# A fully conforming plugin module: callable ``run`` plus a ``schema``
# attribute that is a valid ``bat.plugins.schema.ModuleSchema`` subclass
# with citations set to the literal ``"none"`` (these fixtures don't wrap
# any real published algorithm).
RUN_AND_SCHEMA = (
    "from pydantic import BaseModel\n"
    "from bat.plugins.schema import ModuleSchema\n"
    "\n"
    "\n"
    "class Schema(ModuleSchema):\n"
    "    class Meta:\n"
    "        name = 'test.module'\n"
    "        description = 'A test plugin module.'\n"
    "        citations = 'none'\n"
    "\n"
    "    class Params(BaseModel):\n"
    "        pass\n"
    "\n"
    "    class Inputs(BaseModel):\n"
    "        pass\n"
    "\n"
    "    class Outputs(BaseModel):\n"
    "        pass\n"
    "\n"
    "\n"
    "schema = Schema\n"
    "\n"
    "\n"
    "def run(inputs, params, context=None):\n"
    "    return {'rpeaks': []}\n"
)

# A module with ``run`` but a ``schema`` that is missing citations entirely
# (not even the literal ``\"none\"``) -- CARD-007 requires this to be a
# discovery error.
RUN_AND_SCHEMA_MISSING_CITATIONS = (
    "from pydantic import BaseModel\n"
    "from bat.plugins.schema import ModuleSchema\n"
    "\n"
    "\n"
    "class Schema(ModuleSchema):\n"
    "    class Meta:\n"
    "        name = 'test.module'\n"
    "        description = 'A test plugin module with no citations set.'\n"
    "\n"
    "    class Params(BaseModel):\n"
    "        pass\n"
    "\n"
    "    class Inputs(BaseModel):\n"
    "        pass\n"
    "\n"
    "    class Outputs(BaseModel):\n"
    "        pass\n"
    "\n"
    "\n"
    "schema = Schema\n"
    "\n"
    "\n"
    "def run(inputs, params, context=None):\n"
    "    return {}\n"
)


@pytest.fixture(autouse=True)
def _clean_sys_state():
    """Restore ``sys.path``/``sys.modules`` after every test.

    Discovery imports plugin packages/modules by dotted name into the real
    module cache (and, for local directories, temporarily mutates
    ``sys.path``). Several tests reuse the same top-level namespace name
    (e.g. ``lab``) pointing at different ``tmp_path`` locations across
    tests, so leftover cache entries would make later tests import the
    wrong (stale) module.
    """
    path_before = list(sys.path)
    modules_before = set(sys.modules)
    yield
    sys.path[:] = path_before
    for name in list(sys.modules):
        if name not in modules_before:
            del sys.modules[name]


@pytest.fixture()
def no_installed_plugins(monkeypatch):
    """Force entry-point discovery to find nothing installed.

    Isolates local-directory-only tests from whatever happens to be
    installed in the environment's ``bat.plugins`` entry point group
    (there is none today, but tests should not depend on that).
    """
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [])


def make_entry_point(monkeypatch, tmp_path, name, package_name, files):
    """Build a fake installed package under ``tmp_path`` and register it as
    a ``bat.plugins`` entry point via monkeypatching
    ``importlib.metadata.entry_points``.

    ``files`` is a dict of path-relative-to-package-root -> file contents.
    """
    pkg_root = tmp_path / f"site-packages-{name}"
    for relpath, contents in files.items():
        write(pkg_root, relpath, contents)
    monkeypatch.syspath_prepend(str(pkg_root))

    entry_point = importlib.metadata.EntryPoint(
        name=name, value=package_name, group="bat.plugins"
    )
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [entry_point])
    return entry_point


# --- Local plugins/ directory discovery -----------------------------------


def test_local_nested_package_module_discovered(tmp_path, no_installed_plugins):
    plugins_dir = tmp_path / "plugins"
    write(plugins_dir, "lab/__init__.py", "")
    write(plugins_dir, "lab/ecg/__init__.py", "")
    write(plugins_dir, "lab/ecg/detect_rpeaks.py", RUN_AND_SCHEMA)

    registry = discover_plugins(plugins_dir)

    assert "lab.ecg.detect_rpeaks" in registry
    module = registry["lab.ecg.detect_rpeaks"]
    assert callable(module.run)
    assert hasattr(module, "schema")
    from bat.plugins.schema import ModuleSchema

    assert issubclass(module.schema, ModuleSchema)


def test_local_single_file_collection_discovered(tmp_path, no_installed_plugins):
    plugins_dir = tmp_path / "plugins"
    write(plugins_dir, "custom_lab.py", RUN_AND_SCHEMA)

    registry = discover_plugins(plugins_dir)

    assert "custom_lab" in registry
    assert callable(registry["custom_lab"].run)


def test_local_module_without_run_is_not_registered(tmp_path, no_installed_plugins):
    plugins_dir = tmp_path / "plugins"
    write(plugins_dir, "lab/__init__.py", "")
    write(plugins_dir, "lab/helpers.py", "CONSTANT = 1\n")

    registry = discover_plugins(plugins_dir)

    assert "lab.helpers" not in registry
    assert registry == {}


def test_local_directory_without_init_py_is_skipped(tmp_path, no_installed_plugins):
    plugins_dir = tmp_path / "plugins"
    # Not a regular package (no __init__.py) -- should not be treated as a
    # collection namespace, and must not raise.
    write(plugins_dir, "not_a_package/detect.py", RUN_ONLY)

    registry = discover_plugins(plugins_dir)

    assert registry == {}


# --- Plugin interface / schema conformance (CARD-007) -----------------------


def test_local_module_with_run_but_no_schema_raises(tmp_path, no_installed_plugins):
    plugins_dir = tmp_path / "plugins"
    write(plugins_dir, "lab/__init__.py", "")
    write(plugins_dir, "lab/detect_rpeaks.py", RUN_ONLY)

    with pytest.raises(PluginDiscoveryError) as exc_info:
        discover_plugins(plugins_dir)

    message = str(exc_info.value)
    assert "lab.detect_rpeaks" in message
    assert "schema" in message


def test_local_module_with_missing_citations_raises(tmp_path, no_installed_plugins):
    plugins_dir = tmp_path / "plugins"
    write(plugins_dir, "lab/__init__.py", "")
    write(
        plugins_dir,
        "lab/detect_rpeaks.py",
        RUN_AND_SCHEMA_MISSING_CITATIONS,
    )

    with pytest.raises(PluginDiscoveryError) as exc_info:
        discover_plugins(plugins_dir)

    message = str(exc_info.value)
    assert "lab.detect_rpeaks" in message
    assert "citations" in message


def test_local_module_with_citations_none_passes(tmp_path, no_installed_plugins):
    plugins_dir = tmp_path / "plugins"
    write(plugins_dir, "lab/__init__.py", "")
    write(plugins_dir, "lab/detect_rpeaks.py", RUN_AND_SCHEMA)

    registry = discover_plugins(plugins_dir)

    assert "lab.detect_rpeaks" in registry
    assert registry["lab.detect_rpeaks"].schema.Meta.citations == "none"


# --- Missing plugins/ directory --------------------------------------------


def test_missing_plugins_dir_returns_empty_registry(tmp_path, no_installed_plugins):
    registry = discover_plugins(tmp_path / "does-not-exist")
    assert registry == {}


def test_none_plugins_dir_returns_empty_registry(no_installed_plugins):
    registry = discover_plugins(None)
    assert registry == {}


# --- Entry-point (installed package) discovery -----------------------------


def test_entry_point_plugin_discovered(tmp_path, monkeypatch):
    make_entry_point(
        monkeypatch,
        tmp_path,
        name="lab",
        package_name="lab_ecg_plugin",
        files={
            "lab_ecg_plugin/__init__.py": "",
            "lab_ecg_plugin/ecg/__init__.py": "",
            "lab_ecg_plugin/ecg/detect_rpeaks.py": RUN_AND_SCHEMA,
        },
    )

    registry = discover_plugins(None)

    assert "lab.ecg.detect_rpeaks" in registry
    module = registry["lab.ecg.detect_rpeaks"]
    assert callable(module.run)
    assert hasattr(module, "schema")


def test_entry_point_plugin_submodule_without_run_is_not_registered(tmp_path, monkeypatch):
    make_entry_point(
        monkeypatch,
        tmp_path,
        name="lab",
        package_name="lab_ecg_plugin",
        files={
            "lab_ecg_plugin/__init__.py": "",
            "lab_ecg_plugin/utils.py": "CONSTANT = 1\n",
        },
    )

    registry = discover_plugins(None)

    assert registry == {}


# --- Merging both sources ---------------------------------------------------


def test_both_sources_merged_into_one_registry(tmp_path, monkeypatch):
    make_entry_point(
        monkeypatch,
        tmp_path,
        name="neurokit",
        package_name="neurokit_plugin",
        files={
            "neurokit_plugin/__init__.py": "",
            "neurokit_plugin/ecg/__init__.py": "",
            "neurokit_plugin/ecg/clean.py": RUN_AND_SCHEMA,
        },
    )

    plugins_dir = tmp_path / "plugins"
    write(plugins_dir, "custom_lab/__init__.py", "")
    write(plugins_dir, "custom_lab/rpeak_detector.py", RUN_AND_SCHEMA)

    registry = discover_plugins(plugins_dir)

    assert set(registry) == {"neurokit.ecg.clean", "custom_lab.rpeak_detector"}
    assert callable(registry["neurokit.ecg.clean"].run)
    assert callable(registry["custom_lab.rpeak_detector"].run)


# --- Duplicate detection -----------------------------------------------------


def test_duplicate_module_name_across_sources_raises(tmp_path, monkeypatch):
    make_entry_point(
        monkeypatch,
        tmp_path,
        name="lab",
        package_name="lab_ecg_plugin",
        files={
            "lab_ecg_plugin/__init__.py": "",
            "lab_ecg_plugin/ecg/__init__.py": "",
            "lab_ecg_plugin/ecg/detect_rpeaks.py": RUN_AND_SCHEMA,
        },
    )

    plugins_dir = tmp_path / "plugins"
    write(plugins_dir, "lab/__init__.py", "")
    write(plugins_dir, "lab/ecg/__init__.py", "")
    write(plugins_dir, "lab/ecg/detect_rpeaks.py", RUN_AND_SCHEMA)

    with pytest.raises(PluginDiscoveryError) as exc_info:
        discover_plugins(plugins_dir)

    message = str(exc_info.value)
    assert "lab.ecg.detect_rpeaks" in message
    assert "entry point" in message
    assert "local plugins directory" in message


def test_duplicate_module_name_within_entry_point_source_raises(tmp_path, monkeypatch):
    # Two different installed packages register entry points under the
    # *same* namespace name and both happen to define a submodule with the
    # same relative dotted path -- this is a duplicate "declared twice"
    # within a single source (entry points), not across sources.
    pkg_a_root = tmp_path / "site-packages-a"
    write(pkg_a_root, "lab_pkg_a/__init__.py", "")
    write(pkg_a_root, "lab_pkg_a/ecg/__init__.py", "")
    write(pkg_a_root, "lab_pkg_a/ecg/detect_rpeaks.py", RUN_AND_SCHEMA)

    pkg_b_root = tmp_path / "site-packages-b"
    write(pkg_b_root, "lab_pkg_b/__init__.py", "")
    write(pkg_b_root, "lab_pkg_b/ecg/__init__.py", "")
    write(pkg_b_root, "lab_pkg_b/ecg/detect_rpeaks.py", RUN_AND_SCHEMA)

    monkeypatch.syspath_prepend(str(pkg_a_root))
    monkeypatch.syspath_prepend(str(pkg_b_root))

    entry_point_a = importlib.metadata.EntryPoint(
        name="lab", value="lab_pkg_a", group="bat.plugins"
    )
    entry_point_b = importlib.metadata.EntryPoint(
        name="lab", value="lab_pkg_b", group="bat.plugins"
    )
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kwargs: [entry_point_a, entry_point_b],
    )

    with pytest.raises(PluginDiscoveryError) as exc_info:
        discover_plugins(None)

    message = str(exc_info.value)
    assert "lab.ecg.detect_rpeaks" in message
    assert "lab_pkg_a" in message
    assert "lab_pkg_b" in message
