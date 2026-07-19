"""Goal/watch verifiers grounded in the learner ledger (ADR 0028/0067 seams).

Learning goals become verifiable conditions: "get simpsons-paradox to fluent"
is `{"type": "plugin", "check": "learning_wiki:strength",
"args": {"slug": "simpsons-paradox", "min": 0.75}}` — usable from /goal, from
a watch, and from the /learn chat command's self-driving loop. Verifiers read
the ledger (ground truth), never the conversation.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.learning_wiki")

STUDY_JOB_PREFIX = "study-"
GOAL_WATCH_PREFIX = "learning-wiki-goal-"


def build_verifiers(get_store):
    """Return [(name, async_verifier)] — names get namespaced `learning_wiki:<name>`."""

    async def verify_strength(spec, ctx):
        from graph.goals.types import VerifyResult  # host-only, lazy

        args = spec.get("args") or {}
        slug = str(args.get("slug") or "")
        want = float(args.get("min", 0.75))
        page = get_store().get_page(slug) if slug else None
        if page is None:
            return VerifyResult(False, f"no wiki page '{slug}'", "")
        have = float(page["strength"])
        return VerifyResult(have >= want, f"{slug} strength {have:.2f} / {want:.2f} ({page['tier']})", f"{have:.2f}")

    async def verify_reviews_clear(spec, ctx):
        from graph.goals.types import VerifyResult  # host-only, lazy

        args = spec.get("args") or {}
        allow = int(args.get("max_due", 0))
        due = get_store().due_count()
        return VerifyResult(due <= allow, f"{due} card(s) due (allowed {allow})", str(due))

    return [("strength", verify_strength), ("reviews_clear", verify_reviews_clear)]


def make_on_watch_met(emitter=None):
    """Watch-hook: when a learning-goal watch trips, cancel its study cron + celebrate.

    The hook payload shape is host-versioned, so extract the watch id defensively
    from whatever arrives (dict payloads or objects with a watch_id/id attribute).
    """

    def _watch_id(args, kwargs) -> str:
        candidates = list(args) + list(kwargs.values())
        for c in candidates:
            if isinstance(c, dict):
                for key in ("watch_id", "id"):
                    if isinstance(c.get(key), str):
                        return c[key]
            for key in ("watch_id", "id"):
                v = getattr(c, key, None)
                if isinstance(v, str):
                    return v
            if isinstance(c, str) and c.startswith(GOAL_WATCH_PREFIX):
                return c
        return ""

    def on_met(*args, **kwargs):
        wid = _watch_id(args, kwargs)
        if not wid.startswith(GOAL_WATCH_PREFIX):
            return  # not one of ours
        slug = wid[len(GOAL_WATCH_PREFIX) :]
        try:
            from graph.sdk import cancel_scheduled  # host-only, lazy

            cancel_scheduled(f"{STUDY_JOB_PREFIX}{slug}", plugin_id="learning_wiki")
        except Exception:  # noqa: BLE001
            log.exception("[learning_wiki] cancelling study job for %s failed", slug)
        if emitter is not None:
            try:
                emitter("goal_achieved", {"slug": slug})
            except Exception:  # noqa: BLE001
                log.exception("[learning_wiki] emitting goal_achieved failed")
        log.info("[learning_wiki] learning goal met for %s — study cadence cancelled", slug)

    return on_met
