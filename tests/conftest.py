"""Test bootstrap — the plugin imports with NO protoAgent host.

Registers a synthetic package whose __path__ is the repo root, so the modules'
relative imports (``from .store import ...``) resolve standalone. Executing
``__init__.py`` is safe because host-only imports live inside functions.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "learning_wiki"

if PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[PKG] = _mod
    _spec.loader.exec_module(_mod)


class FakeRegistry:
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.tools: list = []
        self.routers: list = []
        self.surfaces: list = []
        self.skill_dirs: list = []
        self.events: list = []

    def register_tool(self, t):
        self.tools.append(t)

    def register_router(self, router, prefix):
        self.routers.append((prefix, router))

    def register_surface(self, start, stop=None, name=None):
        self.surfaces.append({"start": start, "stop": stop, "name": name})

    def register_skill_dir(self, path):
        self.skill_dirs.append(path)

    def emit(self, topic, data):
        self.events.append((topic, data))


@pytest.fixture
def store(tmp_path):
    from learning_wiki.store import WikiStore

    s = WikiStore(tmp_path / "wiki.db")
    yield s
    s.close()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A registered plugin against an isolated tmp store."""
    import learning_wiki

    monkeypatch.setenv("LEARNING_WIKI_DIR", str(tmp_path))
    learning_wiki._reset_store_for_tests()
    reg = FakeRegistry()
    learning_wiki.register(reg)
    yield reg
    learning_wiki._reset_store_for_tests()


def tool_by_name(reg, name: str):
    for t in reg.tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not registered; have {[t.name for t in reg.tools]}")
