"""Ledger-verified goals: verifiers ground-truth against the store; the watch
hook retires a study cadence exactly once its own watch trips."""

from __future__ import annotations

import asyncio

from learning_wiki.goals import GOAL_WATCH_PREFIX, STUDY_JOB_PREFIX, build_verifiers, make_on_watch_met


def _run(coro):
    return asyncio.run(coro)


def test_verifiers_registered_under_expected_names(registry):
    assert set(registry.goal_verifiers) == {"strength", "reviews_clear"}
    assert registry.watch_hooks and registry.watch_hooks[0]["on_met"] is not None


def test_strength_verifier_reads_the_ledger(store, host_stub):
    verify_strength = dict(build_verifiers(lambda: store))["strength"]
    spec = {"args": {"slug": "bayes", "min": 0.3}}

    res = _run(verify_strength(spec, ctx=None))
    assert res.ok is False and "no wiki page" in res.detail

    store.upsert_page("bayes")
    res = _run(verify_strength(spec, ctx=None))
    assert res.ok is False and "0.00" in res.detail

    store.record_retrieval("bayes", "success")  # 0.35 ≥ 0.3
    res = _run(verify_strength(spec, ctx=None))
    assert res.ok is True and res.value == "0.35"


def test_reviews_clear_verifier(store, host_stub):
    verify = dict(build_verifiers(lambda: store))["reviews_clear"]
    assert _run(verify({"args": {}}, ctx=None)).ok is True
    store.add_card("a", "q?")
    assert _run(verify({"args": {}}, ctx=None)).ok is False
    assert _run(verify({"args": {"max_due": 1}}, ctx=None)).ok is True


def test_watch_hook_cancels_study_job_and_emits(host_stub):
    events = []
    on_met = make_on_watch_met(emitter=lambda t, d: events.append((t, d)))
    on_met({"watch_id": f"{GOAL_WATCH_PREFIX}bayes"})
    assert host_stub["cancelled"] == [{"job_id": f"{STUDY_JOB_PREFIX}bayes", "plugin_id": "learning_wiki"}]
    assert events == [("goal_achieved", {"slug": "bayes"})]


def test_watch_hook_ignores_foreign_watches(host_stub):
    on_met = make_on_watch_met(emitter=lambda t, d: (_ for _ in ()).throw(AssertionError("must not emit")))
    on_met({"watch_id": "someone-elses-watch"})
    on_met("random-string")
    assert host_stub["cancelled"] == []


def test_watch_hook_extracts_id_from_object_payloads(host_stub):
    class Payload:
        watch_id = f"{GOAL_WATCH_PREFIX}fsrs"

    make_on_watch_met()(Payload())
    assert host_stub["cancelled"][0]["job_id"] == f"{STUDY_JOB_PREFIX}fsrs"
