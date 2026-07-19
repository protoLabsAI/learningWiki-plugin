"""register() smoke + the tool layer end-to-end against a tmp store."""

from __future__ import annotations

import json

from conftest import tool_by_name

EXPECTED_TOOLS = {
    "wiki_index",
    "wiki_page",
    "wiki_file",
    "wiki_link",
    "ledger_status",
    "ledger_record",
    "ledger_misconception",
    "card_add",
    "review_next",
    "review_grade",
    "wiki_export",
    "wiki_research",
}


def test_register_contributes_everything(registry):
    assert {t.name for t in registry.tools} == EXPECTED_TOOLS
    prefixes = [p for p, _ in registry.routers]
    assert prefixes == ["/plugins/learning_wiki", "/api/plugins/learning_wiki"]
    assert registry.surfaces[0]["name"] == "learning-wiki-nudge"
    assert registry.skill_dirs == ["skills"]


def test_every_tool_has_a_description(registry):
    for t in registry.tools:
        assert t.description and len(t.description) > 20, f"{t.name} lost its docstring"


def test_file_then_read_roundtrip(registry):
    out = json.loads(
        tool_by_name(registry, "wiki_file").invoke(
            {
                "slug": "Simpson's Paradox",
                "content_md": "trend reverses; needs [[conditional probability]]",
                "links_json": '[{"slug": "confounding", "rel": "prerequisite"}]',
                "change_summary": "session filing",
            }
        )
    )
    assert out["ok"] and out["slug"] == "simpson-s-paradox"
    page = json.loads(tool_by_name(registry, "wiki_page").invoke({"slug": "simpson-s-paradox"}))["page"]
    rels = {(l["slug"], l["rel"]) for l in page["links"]}
    assert ("confounding", "prerequisite") in rels
    assert ("conditional-probability", "related") in rels

    idx = json.loads(tool_by_name(registry, "wiki_index").invoke({}))
    assert idx["count"] == 3  # page + 2 stubs


def test_review_flow_end_to_end(registry):
    card_id = json.loads(tool_by_name(registry, "card_add").invoke({"slug": "a", "prompt": "why?", "origin": "miss"}))[
        "card_id"
    ]
    due = json.loads(tool_by_name(registry, "review_next").invoke({}))
    assert due["due_total"] == 1 and due["cards"][0]["id"] == card_id
    graded = json.loads(tool_by_name(registry, "review_grade").invoke({"card_id": card_id, "rating": 3}))
    assert graded["ok"] and graded["state"] == "review" and graded["interval_days"] >= 1
    assert graded["ledger"]["strength"] > 0
    # scheduled into the future → no longer due
    assert json.loads(tool_by_name(registry, "review_next").invoke({}))["due_total"] == 0


def test_ledger_tools(registry):
    rec = json.loads(tool_by_name(registry, "ledger_record").invoke({"slug": "a", "outcome": "success"}))
    assert rec["strength"] > 0
    mis = json.loads(tool_by_name(registry, "ledger_misconception").invoke({"slug": "a", "add": "wrong belief"}))
    assert mis["misconceptions"][0]["status"] == "open"
    status = json.loads(tool_by_name(registry, "ledger_status").invoke({"slug": "a"}))
    assert status["concept"]["tier"] == "frontier"
    full = json.loads(tool_by_name(registry, "ledger_status").invoke({}))
    assert full["ledger"][0]["open_misconceptions"] == 1


def test_bad_inputs_return_errors_not_raises(registry):
    out = json.loads(tool_by_name(registry, "wiki_page").invoke({"slug": "nope"}))
    assert out["ok"] is False and "no page" in out["error"]
    out = json.loads(tool_by_name(registry, "ledger_record").invoke({"slug": "a", "outcome": "vibes"}))
    assert out["ok"] is False
    out = json.loads(tool_by_name(registry, "review_grade").invoke({"card_id": 999, "rating": 3}))
    assert out["ok"] is False


def test_rh_tool_gated_off_by_default(registry):
    out = json.loads(tool_by_name(registry, "wiki_research").invoke({"topic": "anything"}))
    assert out["ok"] is False and "disabled" in out["error"]


def test_rh_tool_missing_binary(tmp_path, monkeypatch):
    import learning_wiki
    from conftest import FakeRegistry

    monkeypatch.setenv("LEARNING_WIKI_DIR", str(tmp_path))
    learning_wiki._reset_store_for_tests()
    reg = FakeRegistry(config={"rh_enabled": True, "rh_bin": "definitely-not-a-real-binary"})
    learning_wiki.register(reg)
    out = json.loads(tool_by_name(reg, "wiki_research").invoke({"topic": "x"}))
    assert out["ok"] is False and "not found" in out["error"]
    learning_wiki._reset_store_for_tests()


def test_wiki_export_tool(registry, tmp_path):
    tool_by_name(registry, "wiki_file").invoke({"slug": "a", "content_md": "body"})
    out = json.loads(tool_by_name(registry, "wiki_export").invoke({"out_dir": str(tmp_path / "exp")}))
    assert out["ok"] and out["files"] == 1
