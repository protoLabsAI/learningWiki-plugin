"""Test bootstrap — the plugin imports with NO protoAgent host.

Registers a synthetic package whose __path__ is the repo root, so the modules'
relative imports (``from .store import ...``) resolve standalone. Executing
``__init__.py`` is safe because host-only imports live inside functions.

``host_stub`` fakes the ``graph.*`` host modules (sdk / goals / subagents) so
the host-gated seams (crons, verifiers, /learn) can be exercised and their
calls asserted — still with no real host anywhere near the suite.
"""

from __future__ import annotations

import importlib.util
import sys
import types
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
        self.subagents: list = []
        self.goal_verifiers: dict = {}
        self.watch_hooks: list = []
        self.lifecycle_hooks: list = []
        self.chat_commands: dict = {}
        self.a2a_skills: list = []
        self.media: list = []
        self.events: list = []

    def register_tool(self, t):
        self.tools.append(t)

    def register_router(self, router, prefix):
        self.routers.append((prefix, router))

    def register_surface(self, start, stop=None, name=None):
        self.surfaces.append({"start": start, "stop": stop, "name": name})

    def register_skill_dir(self, path):
        self.skill_dirs.append(path)

    def register_subagent(self, cfg):
        self.subagents.append(cfg)

    def register_goal_verifier(self, name, fn):
        self.goal_verifiers[name] = fn

    def register_watch_hook(self, on_met=None, on_expired=None, on_stalled=None):
        self.watch_hooks.append({"on_met": on_met, "on_expired": on_expired, "on_stalled": on_stalled})

    def register_lifecycle_hook(self, on_app_loaded=None, on_agent_active=None, on_system_wake=None):
        self.lifecycle_hooks.append(
            {"on_app_loaded": on_app_loaded, "on_agent_active": on_agent_active, "on_system_wake": on_system_wake}
        )

    def register_chat_command(self, name, handler):
        self.chat_commands[name] = handler

    def register_a2a_skill(self, spec):
        self.a2a_skills.append(spec)

    def save_media(self, data, mime, meta=None):
        self.media.append({"bytes": len(data), "mime": mime, "meta": meta or {}})
        return types.SimpleNamespace(id="m1", url="/media/m1.svg", path="/tmp/m1.svg", mime=mime)

    def emit(self, topic, data):
        self.events.append((topic, data))


@pytest.fixture
def store(tmp_path):
    from learning_wiki.store import WikiStore

    s = WikiStore(tmp_path / "wiki.db")
    yield s
    s.close()


def _reset_plugin_state():
    import learning_wiki
    from learning_wiki.knobs import _reset_knobs_for_tests

    learning_wiki._reset_store_for_tests()
    _reset_knobs_for_tests()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A registered plugin against an isolated tmp store (no host stubs)."""
    import learning_wiki

    monkeypatch.setenv("LEARNING_WIKI_DIR", str(tmp_path))
    _reset_plugin_state()
    reg = FakeRegistry()
    learning_wiki.register(reg)
    yield reg
    _reset_plugin_state()


@pytest.fixture
def host_stub(monkeypatch):
    """Fake graph.* modules; returns a call-capture dict."""
    calls = {"scheduled": [], "cancelled": [], "watches": [], "metrics": [], "loops": [], "stopped_loops": []}

    g = types.ModuleType("graph")
    goals_pkg = types.ModuleType("graph.goals")
    goal_types = types.ModuleType("graph.goals.types")

    class VerifyResult:
        def __init__(self, ok, detail="", value=""):
            self.ok, self.detail, self.value = ok, detail, value

    goal_types.VerifyResult = VerifyResult

    sub_pkg = types.ModuleType("graph.subagents")
    sub_cfg = types.ModuleType("graph.subagents.config")

    class SubagentConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    sub_cfg.SubagentConfig = SubagentConfig

    sdk = types.ModuleType("graph.sdk")

    def schedule_recurring(prompt, cron, *, plugin_id, job_id, session="", timezone=None):
        calls["scheduled"].append({"prompt": prompt, "cron": cron, "plugin_id": plugin_id, "job_id": job_id})
        return {"ok": True}

    def cancel_scheduled(job_id, *, plugin_id):
        calls["cancelled"].append({"job_id": job_id, "plugin_id": plugin_id})
        return True

    def create_watch(**kw):
        calls["watches"].append(kw)
        return {"ok": True, "watch_id": kw.get("watch_id", "")}

    def record_metric(name, value, *, ts=None, plugin_id):
        calls["metrics"].append((name, value, plugin_id))
        return {}

    class Knobs:
        def __init__(self, **kw):
            self.values_map: dict = {}
            self.presets_map: dict = {}

        def define(self, name, default, **kw):
            self.values_map[name] = default
            return self

        def preset(self, name, overrides, **kw):
            self.presets_map[name] = overrides
            return self

        def get(self, name):
            return self.values_map[name]

    def make_knob_tools(knobs, *, prefix, **kw):
        calls["knob_prefix"] = prefix
        return []

    def start_goal_loop(**kw):
        calls["loops"].append(kw)
        return {
            "ok": True,
            "goal": kw.get("goal", ""),
            "loop_id": kw.get("loop_id", ""),
            "watch_id": f"{kw.get('plugin_id', '')}:goal-loop:{kw.get('loop_id', '')}",
            "job_id": f"plugin:{kw.get('plugin_id', '')}:{kw.get('loop_id', '')}",
            "schedule": kw.get("every", ""),
            "message": "armed",
        }

    def stop_goal_loop(*, plugin_id, loop_id):
        calls["stopped_loops"].append({"plugin_id": plugin_id, "loop_id": loop_id})
        return {"ok": True}

    sdk.schedule_recurring = schedule_recurring
    sdk.cancel_scheduled = cancel_scheduled
    sdk.create_watch = create_watch
    sdk.record_metric = record_metric
    sdk.Knobs = Knobs
    sdk.make_knob_tools = make_knob_tools
    sdk.start_goal_loop = start_goal_loop
    sdk.stop_goal_loop = stop_goal_loop

    g.goals = goals_pkg
    g.subagents = sub_pkg
    g.sdk = sdk
    goals_pkg.types = goal_types
    sub_pkg.config = sub_cfg

    for name, mod in {
        "graph": g,
        "graph.goals": goals_pkg,
        "graph.goals.types": goal_types,
        "graph.subagents": sub_pkg,
        "graph.subagents.config": sub_cfg,
        "graph.sdk": sdk,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return calls


@pytest.fixture
def registry_hosted(tmp_path, monkeypatch, host_stub):
    """Registered plugin WITH the fake host — subagents/crons/verifiers all live."""
    import learning_wiki

    monkeypatch.setenv("LEARNING_WIKI_DIR", str(tmp_path))
    _reset_plugin_state()
    reg = FakeRegistry(config={"review_cron": "0 9 * * *", "lint_cron": "0 7 * * 1"})
    learning_wiki.register(reg)
    yield reg, host_stub
    _reset_plugin_state()


def tool_by_name(reg, name: str):
    for t in reg.tools:
        if t.name == name:
            return t
    raise AssertionError(f"tool {name!r} not registered; have {[t.name for t in reg.tools]}")
