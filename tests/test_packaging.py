"""Manifest sanity + version lockstep + trust posture."""

from __future__ import annotations

import tomllib

import yaml

from conftest import ROOT


def _manifest() -> dict:
    return yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())


def test_manifest_parses_and_ids_agree():
    m = _manifest()
    assert m["id"] == "learning_wiki"
    assert m["config_section"] == "learning_wiki"
    assert isinstance(m["config_section"], str)  # a list here breaks the host's reserved-section check


def test_version_lockstep_with_pyproject():
    m = _manifest()
    py = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert m["version"] == py["project"]["version"]


def test_ships_disabled():
    assert _manifest()["enabled"] is False


def test_no_runtime_pip_deps_declared():
    assert not _manifest().get("requires_pip")


def test_view_declared_on_public_prefix():
    views = _manifest()["views"]
    assert views and views[0]["path"] == "/plugins/learning_wiki/view"
    assert not views[0]["path"].startswith("/api/")


def test_config_defaults_present():
    cfg = _manifest()["config"]
    for key in ("data_dir", "desired_retention", "fsrs_weights", "nudge_interval_hours", "rh_enabled"):
        assert key in cfg
    assert cfg["rh_enabled"] is False
    assert cfg["nudge_interval_hours"] == 0
