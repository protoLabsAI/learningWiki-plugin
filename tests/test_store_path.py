from __future__ import annotations

import sys
import types

import pytest

import learning_wiki


def test_default_store_comes_from_the_plugin_sdk(monkeypatch, tmp_path):
    monkeypatch.delenv("LEARNING_WIKI_DIR", raising=False)
    calls = []
    sdk = types.ModuleType("graph.sdk")

    def plugin_store(*, plugin_id):
        calls.append(plugin_id)
        tmp_path.mkdir(parents=True, exist_ok=True)
        return tmp_path

    sdk.plugin_store = plugin_store
    graph = types.ModuleType("graph")
    graph.sdk = sdk
    monkeypatch.setitem(sys.modules, "graph", graph)
    monkeypatch.setitem(sys.modules, "graph.sdk", sdk)

    assert learning_wiki._data_dir({}) == tmp_path
    assert calls == ["learning_wiki"]


@pytest.mark.parametrize("result", [None, RuntimeError("store unavailable")])
def test_default_store_refuses_an_unscoped_fallback(monkeypatch, result):
    monkeypatch.delenv("LEARNING_WIKI_DIR", raising=False)
    sdk = types.ModuleType("graph.sdk")

    def plugin_store(*, plugin_id):
        if isinstance(result, Exception):
            raise result
        return result

    sdk.plugin_store = plugin_store
    graph = types.ModuleType("graph")
    graph.sdk = sdk
    monkeypatch.setitem(sys.modules, "graph", graph)
    monkeypatch.setitem(sys.modules, "graph.sdk", sdk)

    with pytest.raises(RuntimeError, match="instance-scoped plugin store"):
        learning_wiki._data_dir({})


def test_config_and_env_overrides_stay_literal_and_skip_the_sdk(monkeypatch, tmp_path):
    environment = tmp_path / "environment"
    configured = tmp_path / "configured"
    monkeypatch.setenv("LEARNING_WIKI_DIR", str(environment))

    assert learning_wiki._data_dir({}) == environment
    assert learning_wiki._data_dir({"data_dir": str(configured)}) == configured
