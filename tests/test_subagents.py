"""Subagent specs: trust boundaries and host-gated construction."""

from __future__ import annotations

from learning_wiki.subagents import REVIEW_COACH_SPEC, WIKI_LINT_SPEC, build_subagents

WRITE_TOOLS = {"wiki_file", "wiki_link", "ledger_record", "ledger_misconception", "card_add", "wiki_export"}


def test_wiki_lint_is_read_only_by_construction():
    assert not set(WIKI_LINT_SPEC["tools"]) & WRITE_TOOLS
    assert "review_grade" not in WIKI_LINT_SPEC["tools"]


def test_review_coach_can_grade_but_not_edit_the_wiki():
    tools = set(REVIEW_COACH_SPEC["tools"])
    assert "review_grade" in tools and "review_next" in tools
    assert not tools & {"wiki_file", "wiki_link", "wiki_export"}


def test_specs_carry_the_honesty_discipline():
    assert "Never inflate a rating" in REVIEW_COACH_SPEC["system_prompt"]
    assert "read-only" in WIKI_LINT_SPEC["system_prompt"]
    for spec in (REVIEW_COACH_SPEC, WIKI_LINT_SPEC):
        assert spec["name"] and spec["description"] and spec["max_turns"] > 0


def test_build_returns_empty_without_host():
    assert build_subagents() == []


def test_build_constructs_host_configs_with_stub(host_stub):
    subs = build_subagents()
    assert [s.name for s in subs] == ["review-coach", "wiki-lint"]
    assert subs[0].tools == REVIEW_COACH_SPEC["tools"]


def test_register_adds_subagents_when_hosted(registry_hosted):
    reg, _ = registry_hosted
    assert [s.name for s in reg.subagents] == ["review-coach", "wiki-lint"]
