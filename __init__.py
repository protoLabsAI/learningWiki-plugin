"""learning_wiki — an adaptive learning wiki for protoAgent (ADR 0001).

Two ledgers, one loop: an LLM-maintained wiki of concept pages (the content
model) plus a learner ledger of strengths/misconceptions/cards (the learner
model). Only retrieval events move strength. register() is the only place
plugin code runs; host-only imports stay lazy so the test suite runs host-free.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("protoagent.plugins.learning_wiki")

_STORE = None


def _data_dir(cfg: dict) -> Path:
    override = (cfg or {}).get("data_dir") or os.environ.get("LEARNING_WIKI_DIR")
    if override:
        p = Path(override).expanduser()
    else:
        try:
            from infra.paths import instance_paths  # host-only; lazy by design

            p = instance_paths().store("learning_wiki")
        except Exception:  # noqa: BLE001 — standalone / tests / older host
            p = Path.home() / ".protoagent" / "learning_wiki"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _make_get_store(cfg: dict):
    def get_store():
        global _STORE
        if _STORE is None:
            from .store import WikiStore

            _STORE = WikiStore(_data_dir(cfg) / "wiki.db")
        return _STORE

    return get_store


def _reset_store_for_tests() -> None:
    global _STORE
    if _STORE is not None:
        try:
            _STORE.close()
        except Exception:  # noqa: BLE001
            pass
    _STORE = None


def register(registry) -> None:
    cfg = registry.config or {}
    get_store = _make_get_store(cfg)

    # 1. Console view (public) + data API (gated) — two routers, two prefixes.
    try:
        from .api import build_data_router, build_view_router

        registry.register_router(build_view_router(cfg), prefix="/plugins/learning_wiki")
        registry.register_router(build_data_router(cfg, get_store), prefix="/api/plugins/learning_wiki")
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] mounting routers failed")

    # 2. Review-nudge surface (inert unless nudge_interval_hours > 0).
    try:
        from .nudge import ReviewNudge

        nudge = ReviewNudge(cfg, get_store, emitter=getattr(registry, "emit", None))
        registry.register_surface(nudge.start, stop=nudge.stop, name="learning-wiki-nudge")
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering surface failed")

    # 3. Tools — the only mutation path the model gets.
    try:
        from .tools import build_tools

        for t in build_tools(cfg, get_store):
            registry.register_tool(t)
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering tools failed")

    # 4. The tutor skill (probe-first, tiered, answer-holding — the policy layer).
    try:
        registry.register_skill_dir("skills")
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering skills failed")

    log.info("[learning_wiki] registered")
