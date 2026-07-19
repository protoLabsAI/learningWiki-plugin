"""learning_wiki — an adaptive learning wiki for protoAgent (ADR 0001).

Two ledgers, one loop: an LLM-maintained wiki of concept pages (the content
model) plus a learner ledger of strengths/misconceptions/cards (the learner
model). Only retrieval events move strength. register() is the only place
plugin code runs; host-only imports stay lazy so the test suite runs host-free.

Seam usage is documented seam-by-seam (used AND deliberately skipped) in
SEAMS.md — this plugin doubles as a plugin-SDK reference.
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


REVIEW_CRON_PROMPT = (
    "Scheduled review pass: task the review-coach subagent to run one spaced-repetition "
    "session over the due cards and relay its session. If it reports no cards due, reply "
    "with a single short line."
)

LINT_CRON_PROMPT = (
    "Scheduled wiki lint: task the wiki-lint subagent (read-only) and relay its report. "
    "Do not apply fixes unless the operator asks."
)


def _arm_crons(cfg: dict) -> None:
    """Plugin-owned recurring jobs (ADR 0054 pattern): a scheduled job fires a normal
    agent turn — no new scheduling machinery. Idempotent by job id; the loader sweeps
    `plugin:learning_wiki:*` jobs on disable/uninstall (#1642)."""
    jobs = []
    if cfg.get("review_cron"):
        jobs.append(("review-session", str(cfg["review_cron"]), REVIEW_CRON_PROMPT))
    if cfg.get("lint_cron"):
        jobs.append(("wiki-lint", str(cfg["lint_cron"]), LINT_CRON_PROMPT))
    if not jobs:
        return
    try:
        from graph.sdk import schedule_recurring  # host-only, lazy
    except ImportError:
        log.info("[learning_wiki] scheduler unavailable (no host) — crons not armed")
        return
    for job_id, cron, prompt in jobs:
        try:
            schedule_recurring(prompt, cron, plugin_id="learning_wiki", job_id=job_id)
            log.info("[learning_wiki] armed cron %s (%s)", job_id, cron)
        except Exception:  # noqa: BLE001
            log.exception("[learning_wiki] arming cron %s failed", job_id)


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
    nudge = None
    try:
        from .nudge import ReviewNudge

        nudge = ReviewNudge(cfg, get_store, emitter=getattr(registry, "emit", None))
        registry.register_surface(nudge.start, stop=nudge.stop, name="learning-wiki-nudge")
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering surface failed")

    # 3. Tools — the only mutation path the model gets. The registry rides along
    #    for save_media (wiki_map's inline SVG).
    try:
        from .tools import build_tools

        for t in build_tools(cfg, get_store, registry=registry):
            registry.register_tool(t)
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering tools failed")

    # 3b. Tutor knobs (graph.sdk Knobs): tutor_knobs/_tune/_preset tools; [] host-free.
    try:
        from .knobs import build_knob_tools

        for t in build_knob_tools():
            registry.register_tool(t)
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering knob tools failed")

    # 4. The tutor skill (probe-first, tiered, answer-holding — the policy layer).
    try:
        registry.register_skill_dir("skills")
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering skills failed")

    # 5. Subagents: review-coach (scheduled sessions) + wiki-lint (read-only curation).
    try:
        from .subagents import build_subagents

        for sub in build_subagents():
            registry.register_subagent(sub)
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering subagents failed")

    # 6. Ledger-verified learning goals: verifiers for /goal + watches, and a
    #    watch hook that retires a /learn study cadence once its target is met.
    try:
        from .goals import build_verifiers, make_on_watch_met

        if hasattr(registry, "register_goal_verifier"):
            for name, fn in build_verifiers(get_store):
                registry.register_goal_verifier(name, fn)
        if hasattr(registry, "register_watch_hook"):
            registry.register_watch_hook(on_met=make_on_watch_met(getattr(registry, "emit", None)))
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering goal seams failed")

    # 7. User-only control commands: /review, /wiki, /learn.
    try:
        from .commands import build_commands

        if hasattr(registry, "register_chat_command"):
            for name, handler in build_commands(cfg, get_store).items():
                registry.register_chat_command(name, handler)
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering chat commands failed")

    # 7b. A2A card skills: structured learner-status + quiz material for other agents.
    try:
        from .a2a import A2A_SKILLS

        if hasattr(registry, "register_a2a_skill"):
            for skill in A2A_SKILLS:
                registry.register_a2a_skill(skill)
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering a2a skills failed")

    # 8. Lifecycle: desktop wake → due check (the rail dot lights via reviews_due).
    try:
        if nudge is not None and hasattr(registry, "register_lifecycle_hook"):
            registry.register_lifecycle_hook(on_system_wake=lambda *a, **k: nudge.check_once())
    except Exception:  # noqa: BLE001
        log.exception("[learning_wiki] registering lifecycle hook failed")

    # 9. Plugin-owned crons: scheduled review sessions + weekly lint.
    _arm_crons(cfg)

    log.info("[learning_wiki] registered")
