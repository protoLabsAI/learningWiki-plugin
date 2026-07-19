"""Wiki store: pages, revisions with provenance, typed links, export."""

from __future__ import annotations

import pytest

from learning_wiki.store import extract_wikilinks, slugify


def test_slugify_and_wikilink_extraction():
    assert slugify("Simpson's Paradox!") == "simpson-s-paradox"
    assert slugify("  Édge  Cäse  ") == "edge-case"
    md = "See [[Conditional Probability]] and [[confounding|the confounder]] twice: [[Conditional Probability]]."
    assert extract_wikilinks(md) == ["conditional-probability", "confounding"]


def test_upsert_creates_page_revision_and_ledger_row(store):
    page = store.upsert_page("Simpson's Paradox", content_md="# It reverses", change_summary="first filing")
    assert page["slug"] == "simpson-s-paradox"
    assert page["strength"] == 0.0  # filing is not knowing
    assert page["tier"] == "novice"
    assert len(page["revisions"]) == 1
    assert page["revisions"][0]["change_summary"] == "first filing"


def test_update_records_revision_only_on_content_change(store):
    store.upsert_page("x", content_md="v1")
    store.upsert_page("x", content_md="v1")  # no-op content
    store.upsert_page("x", content_md="v2", source_kind="research", source_ref="r-123")
    page = store.get_page("x")
    assert len(page["revisions"]) == 2
    assert page["revisions"][0]["source_kind"] == "research"
    assert page["revisions"][0]["source_ref"] == "r-123"
    assert page["content_md"] == "v2"


def test_update_with_empty_content_preserves_existing(store):
    store.upsert_page("x", content_md="body", summary="s1")
    store.upsert_page("x", summary="s2")
    page = store.get_page("x")
    assert page["content_md"] == "body"
    assert page["summary"] == "s2"


def test_wikilinks_in_content_become_edges_with_stubs(store):
    store.upsert_page("a", content_md="depends on [[b]] and [[c|see c]]")
    page = store.get_page("a")
    assert {(l["slug"], l["rel"]) for l in page["links"]} == {("b", "related"), ("c", "related")}
    assert store.get_page("b")["content_md"] == ""  # stub, not overwritten
    assert {b["slug"] for b in store.get_page("b")["backlinks"]} == {"a"}


def test_typed_links_and_validation(store):
    store.add_link("a", "b", "prerequisite")
    assert store.get_page("a")["links"] == [{"slug": "b", "title": "B", "rel": "prerequisite"}]
    with pytest.raises(ValueError):
        store.add_link("a", "b", "blocks")
    with pytest.raises(ValueError):
        store.upsert_page("x", kind="diary")


def test_self_link_ignored(store):
    store.upsert_page("a", content_md="see [[a]]")
    assert store.get_page("a")["links"] == []


def test_export_markdown(store, tmp_path):
    store.upsert_page("a", title="Alpha", content_md="# body")
    store.upsert_page("b", title="Beta", content_md="text")
    n = store.export_markdown(tmp_path / "out")
    assert n == 2
    text = (tmp_path / "out" / "a.md").read_text()
    assert "title: Alpha" in text and "# body" in text


def test_stats_and_list(store):
    store.upsert_page("a")
    store.add_card("a", "prompt?")
    stats = store.stats()
    assert stats["pages"] == 1 and stats["cards"] == 1 and stats["due"] == 1
    pages = store.list_pages()
    assert pages[0]["due_cards"] == 1
