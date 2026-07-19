"""/review, /wiki, /learn chat commands — user-only control surface."""

from __future__ import annotations

import asyncio

from learning_wiki.commands import build_commands
from learning_wiki.goals import GOAL_WATCH_PREFIX, STUDY_JOB_PREFIX


def _cmds(store, cfg=None):
    return build_commands(cfg or {}, lambda: store)


def _run(coro):
    return asyncio.run(coro)


def test_register_wires_all_three(registry):
    assert set(registry.chat_commands) == {"review", "wiki", "learn"}


def test_review_empty_and_due(store):
    cmds = _cmds(store)
    assert "Nothing due" in _run(cmds["review"]("", "s1"))
    store.add_card("bayes", "state the theorem")
    out = _run(cmds["review"]("", "s1"))
    assert "1 card(s) due" in out and "bayes" in out


def test_wiki_frontier_and_page_peek(store):
    cmds = _cmds(store)
    assert "empty" in _run(cmds["wiki"]("", "s1"))
    store.upsert_page("bayes", title="Bayes", content_md="posterior ∝ likelihood × prior")
    out = _run(cmds["wiki"]("", "s1"))
    assert "`bayes`" in out and "novice" in out
    out = _run(cmds["wiki"]("Bayes", "s1"))
    assert "posterior" in out
    assert "No page" in _run(cmds["wiki"]("nope", "s1"))


def test_learn_usage_and_host_free_degradation(store):
    cmds = _cmds(store)
    assert "Usage" in _run(cmds["learn"]("", "s1"))
    out = _run(cmds["learn"]("bayes theorem", "s1"))
    assert "needs the protoAgent host" in out


def test_learn_arms_cron_and_watch(store, host_stub):
    cmds = _cmds(store, cfg={"study_cron": "0 8 * * *"})
    out = _run(cmds["learn"]("bayes theorem 0.8", "session-42"))
    assert "Learning loop armed" in out and "bayes-theorem" in out

    job = host_stub["scheduled"][0]
    assert job["job_id"] == f"{STUDY_JOB_PREFIX}bayes-theorem"
    assert job["cron"] == "0 8 * * *" and job["plugin_id"] == "learning_wiki"

    watch = host_stub["watches"][0]
    assert watch["watch_id"] == f"{GOAL_WATCH_PREFIX}bayes-theorem"
    assert watch["verifier"] == "learning_wiki:strength"
    assert watch["verifier_args"] == {"slug": "bayes-theorem", "min": 0.8}
    assert watch["run_session"] == "session-42"

    # the stub page exists so the verifier has ground truth from day one
    assert store.get_page("bayes-theorem") is not None


def test_learn_target_defaults_and_bad_target_treated_as_topic(store, host_stub):
    cmds = _cmds(store)
    _run(cmds["learn"]("fsrs", "s1"))
    assert host_stub["watches"][0]["verifier_args"]["min"] == 0.75
    _run(cmds["learn"]("chapter 7", "s1"))  # trailing int 7 > 1.0 → part of the topic
    assert host_stub["watches"][1]["verifier_args"]["slug"] == "chapter-7"
