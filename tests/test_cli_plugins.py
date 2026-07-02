"""Tests for the ``bat plugins list`` command (CARD-017).

CLI-level, using ``click.testing.CliRunner`` end to end against small
on-disk plugin fixtures under a real ``plugins/`` directory relative to the
current working directory -- the same fixture pattern established in
``tests/test_plugins_discovery.py`` and ``tests/test_cli_run.py``. Installed
(entry-point) plugin coverage reuses the same entry-point-mocking technique
from ``tests/test_plugins_discovery.py``.
"""

from __future__ import annotations

import importlib.metadata
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from bat.cli import main

MODULE_TEMPLATE = """\
from pydantic import BaseModel, Field
from bat.plugins.schema import ModuleSchema, InputField, OutputField


class Schema(ModuleSchema):
    class Meta:
        name = {name!r}
        description = {description!r}
        citations = {citations!r}

    class Params(BaseModel):
{params_body}

    class Inputs(BaseModel):
{inputs_body}

    class Outputs(BaseModel):
{outputs_body}


schema = Schema


def run(inputs, params, context=None):
    return {{}}
"""


def render_module(
    name: str,
    description: str = "A test plugin module.",
    citations="none",
    params_body: str = "        pass",
    inputs_body: str = "        pass",
    outputs_body: str = "        pass",
) -> str:
    return MODULE_TEMPLATE.format(
        name=name,
        description=description,
        citations=citations,
        params_body=params_body,
        inputs_body=inputs_body,
        outputs_body=outputs_body,
    )


READ_MODULE = render_module(
    name="core.wfdb.read",
    description="Read a WFDB record from disk",
    citations="none",
    params_body='        path: str = Field(...)',
    inputs_body="        pass",
    outputs_body='        signal: OutputField(artifact_type="signal", artifact_format="wfdb")',
)

WRITE_MODULE = render_module(
    name="core.wfdb.write",
    description="Write a WFDB record to disk",
    citations="none",
)

RPEAKS_MODULE = render_module(
    name="custom_lab.rpeak_detector",
    description="Detect R-peaks using the Pan-Tompkins algorithm",
    citations=[
        "Pan J, Tompkins W. A real-time QRS detection algorithm. IEEE TBME. 1985."
    ],
    params_body='        min_distance_ms: float = Field(default=200.0)',
    inputs_body='        signal: InputField(artifact_type="signal", artifact_format="wfdb")',
    outputs_body='        rpeaks: OutputField(artifact_type="annotations", artifact_format="wfdb")',
)


def write(base: Path, relpath: str, contents: str) -> Path:
    path = base / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)
    return path


def write_local_plugins() -> None:
    """Write a local ``plugins/core/...`` collection under the current
    working directory (relative -- callers run this inside a CliRunner
    isolated filesystem)."""
    write(Path("plugins"), "core/__init__.py", "")
    write(Path("plugins"), "core/wfdb/__init__.py", "")
    write(Path("plugins"), "core/wfdb/read.py", READ_MODULE)
    write(Path("plugins"), "core/wfdb/write.py", WRITE_MODULE)


def write_local_custom_lab() -> None:
    write(Path("plugins"), "custom_lab/__init__.py", "")
    write(Path("plugins"), "custom_lab/rpeak_detector.py", RPEAKS_MODULE)


@pytest.fixture(autouse=True)
def _clean_sys_state():
    """Restore ``sys.path``/``sys.modules`` after every test (discovery
    imports plugin packages by dotted name into the real module cache; see
    ``tests/test_plugins_discovery.py`` for the full rationale)."""
    path_before = list(sys.path)
    modules_before = set(sys.modules)
    yield
    sys.path[:] = path_before
    for name in list(sys.modules):
        if name not in modules_before:
            del sys.modules[name]


def make_entry_point(monkeypatch, tmp_path, name, package_name, files):
    """Register a fake installed ``bat.plugins`` entry point, as in
    ``tests/test_plugins_discovery.py``."""
    pkg_root = tmp_path / f"site-packages-{name}"
    for relpath, contents in files.items():
        path = pkg_root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    monkeypatch.syspath_prepend(str(pkg_root))

    entry_point = importlib.metadata.EntryPoint(
        name=name, value=package_name, group="bat.plugins"
    )
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [entry_point])
    return entry_point


@pytest.fixture()
def no_installed_plugins(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: [])


# --------------------------------------------------------------------------
# Default (compact) listing
# --------------------------------------------------------------------------


def test_list_shows_all_discovered_modules_grouped_by_collection(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_plugins()
        write_local_custom_lab()

        result = runner.invoke(main, ["plugins", "list"])

        assert result.exit_code == 0, result.output
        assert "core.wfdb.read" in result.output
        assert "core.wfdb.write" in result.output
        assert "custom_lab.rpeak_detector" in result.output


def test_local_plugins_listed_separately_from_installed(monkeypatch, tmp_path):
    make_entry_point(
        monkeypatch,
        tmp_path,
        name="lab",
        package_name="lab_ecg_plugin",
        files={
            "lab_ecg_plugin/__init__.py": "",
            "lab_ecg_plugin/ecg/__init__.py": "",
            "lab_ecg_plugin/ecg/detect_rpeaks.py": render_module(
                name="lab.ecg.detect_rpeaks",
                citations="none",
            ),
        },
    )

    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_custom_lab()

        result = runner.invoke(main, ["plugins", "list"])

        assert result.exit_code == 0, result.output
        assert "Installed plugins:" in result.output
        assert "Local plugins (plugins/):" in result.output

        installed_idx = result.output.index("Installed plugins:")
        local_idx = result.output.index("Local plugins (plugins/):")
        lab_idx = result.output.index("lab.ecg.detect_rpeaks")
        custom_idx = result.output.index("custom_lab.rpeak_detector")

        # lab.ecg.detect_rpeaks belongs under "Installed plugins:", and
        # custom_lab.rpeak_detector belongs under "Local plugins (plugins/):".
        assert installed_idx < lab_idx < local_idx
        assert local_idx < custom_idx


def test_installed_plugin_shows_version(monkeypatch, tmp_path):
    make_entry_point(
        monkeypatch,
        tmp_path,
        name="lab",
        package_name="lab_ecg_plugin",
        files={
            "lab_ecg_plugin/__init__.py": "",
            "lab_ecg_plugin/ecg/__init__.py": "",
            "lab_ecg_plugin/ecg/detect_rpeaks.py": render_module(
                name="lab.ecg.detect_rpeaks", citations="none"
            ),
        },
    )

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["plugins", "list"])

        assert result.exit_code == 0, result.output
        assert "lab" in result.output
        # No installed distribution actually backs this fake entry point, so
        # the version lookup falls back to "unknown" -- what matters here is
        # that a version placeholder is rendered at all for installed plugins.
        assert "unknown" in result.output or "(" in result.output


def test_no_plugins_discovered_message(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(main, ["plugins", "list"])

        assert result.exit_code == 0, result.output
        assert "No plugins discovered." in result.output


# --------------------------------------------------------------------------
# Verbose listing
# --------------------------------------------------------------------------


def test_list_verbose_shows_schema_details_for_each_module(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_plugins()
        write_local_custom_lab()

        result = runner.invoke(main, ["plugins", "list", "--verbose"])

        assert result.exit_code == 0, result.output
        assert "core.wfdb.read" in result.output
        assert "Description : Read a WFDB record from disk" in result.output
        assert "Source      : local (core)" in result.output
        assert "Citations   : none" in result.output
        assert "Inputs      : (none)" in result.output
        assert "Params      : path (str, required)" in result.output
        assert "Outputs     : signal (type: signal, format: wfdb)" in result.output

        assert "custom_lab.rpeak_detector" in result.output
        assert (
            "Citations   : Pan J, Tompkins W. A real-time QRS detection "
            "algorithm. IEEE TBME. 1985." in result.output
        )
        assert "Inputs      : signal (type: signal, format: wfdb)" in result.output
        assert (
            "Params      : min_distance_ms (float, default: 200.0)" in result.output
        )
        assert (
            "Outputs     : rpeaks (type: annotations, format: wfdb)"
            in result.output
        )


# --------------------------------------------------------------------------
# --module filter
# --------------------------------------------------------------------------


def test_list_module_shows_details_for_specific_module(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_plugins()
        write_local_custom_lab()

        result = runner.invoke(
            main, ["plugins", "list", "--module", "core.wfdb.read"]
        )

        assert result.exit_code == 0, result.output
        assert "core.wfdb.read" in result.output
        assert "Description : Read a WFDB record from disk" in result.output
        # Other modules must not be included in the single-module output.
        assert "core.wfdb.write" not in result.output
        assert "custom_lab.rpeak_detector" not in result.output


def test_list_module_nonexistent_exits_with_error(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_plugins()

        result = runner.invoke(
            main, ["plugins", "list", "--module", "nonexistent"]
        )

        assert result.exit_code != 0
        assert "nonexistent" in result.output


# --------------------------------------------------------------------------
# Citations rendering
# --------------------------------------------------------------------------


def test_citations_none_shown_correctly(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_plugins()

        result = runner.invoke(
            main, ["plugins", "list", "--module", "core.wfdb.read"]
        )

        assert result.exit_code == 0, result.output
        assert "Citations   : none" in result.output


def test_citations_list_shown_correctly(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_custom_lab()

        result = runner.invoke(
            main, ["plugins", "list", "--module", "custom_lab.rpeak_detector"]
        )

        assert result.exit_code == 0, result.output
        assert (
            "Citations   : Pan J, Tompkins W. A real-time QRS detection "
            "algorithm. IEEE TBME. 1985." in result.output
        )


# --------------------------------------------------------------------------
# Runs without a protocol file
# --------------------------------------------------------------------------


def test_plugins_list_runs_without_protocol_file(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        # No protocol.yaml anywhere, no plugins/ directory either.
        result = runner.invoke(main, ["plugins", "list"])
        assert result.exit_code == 0, result.output


# --------------------------------------------------------------------------
# `bat plugins docs` (CARD-018)
# --------------------------------------------------------------------------


def test_docs_prints_markdown_to_stdout(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_plugins()
        write_local_custom_lab()

        result = runner.invoke(main, ["plugins", "docs"])

        assert result.exit_code == 0, result.output
        assert result.output.startswith("# BAT Plugin Reference")
        assert "Generated by `bat plugins docs` on " in result.output


def test_docs_includes_all_discovered_modules(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_plugins()
        write_local_custom_lab()

        result = runner.invoke(main, ["plugins", "docs"])

        assert result.exit_code == 0, result.output
        assert "### `core.wfdb.read`" in result.output
        assert "### `core.wfdb.write`" in result.output
        assert "### `custom_lab.rpeak_detector`" in result.output


def test_docs_groups_modules_by_namespace(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_plugins()
        write_local_custom_lab()

        result = runner.invoke(main, ["plugins", "docs"])

        assert result.exit_code == 0, result.output
        assert "## core" in result.output
        assert "## custom_lab" in result.output


def test_docs_shows_citations_and_params(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        write_local_plugins()
        write_local_custom_lab()

        result = runner.invoke(main, ["plugins", "docs"])

        assert result.exit_code == 0, result.output
        assert "**Citations:** none" in result.output
        assert (
            "**Citations:**\n- Pan J, Tompkins W. A real-time QRS detection "
            "algorithm. IEEE TBME. 1985." in result.output
        )
        assert "min_distance_ms" in result.output


def test_docs_runs_without_protocol_file(no_installed_plugins):
    runner = CliRunner()
    with runner.isolated_filesystem():
        # No protocol.yaml anywhere, no plugins/ directory either.
        result = runner.invoke(main, ["plugins", "docs"])
        assert result.exit_code == 0, result.output
        assert result.output.startswith("# BAT Plugin Reference")
