"""THE invariant: only retrieval events move strength. Everything else here
exists to prove reading/filing/linking can't."""

from __future__ import annotations

import pytest


def test_filing_reading_linking_never_move_strength(store):
    store.upsert_page("a", content_md="v1")
    store.upsert_page("a", content_md="v2")  # revision
    store.add_link("a", "b", "prerequisite")
    store.get_page("a")
    store.list_pages()
    store.add_card("a", "q?")
    assert store.get_page("a")["strength"] == 0.0
    assert store.get_page("b")["strength"] == 0.0


def test_retrieval_moves_strength_asymptotically(store):
    store.upsert_page("a")
    r1 = store.record_retrieval("a", "success")
    assert r1["strength"] == pytest.approx(0.35)
    r2 = store.record_retrieval("a", "success")
    assert r2["strength"] == pytest.approx(0.35 + 0.65 * 0.35)
    assert r2["strength"] < 1.0


def test_partial_and_failure_math(store):
    store.record_retrieval("a", "partial")
    assert store.get_page("a")["strength"] == pytest.approx(0.15)
    store.record_retrieval("a", "success")
    s = store.get_page("a")["strength"]
    store.record_retrieval("a", "failure")
    assert store.get_page("a")["strength"] == pytest.approx(s * 0.6)


def test_invalid_outcome_rejected(store):
    with pytest.raises(ValueError):
        store.record_retrieval("a", "vibes")


def test_tier_boundaries(store):
    from learning_wiki.store import tier

    assert tier(0.29) == "novice"
    assert tier(0.3) == "frontier"
    assert tier(0.7) == "frontier"
    assert tier(0.71) == "fluent"


def test_evidence_trail_and_last_retrieved(store):
    store.record_retrieval("a", "success", note="predicted the mechanism")
    page = store.get_page("a")
    assert page["last_retrieved"] is not None
    assert page["evidence"][-1]["note"] == "predicted the mechanism"
    assert page["evidence"][-1]["outcome"] == "success"


def test_misconception_lifecycle(store):
    store.add_misconception("a", "thinks reversal is about sample size")
    items = store.get_page("a")["misconceptions"]
    assert items[0]["status"] == "open"
    store.resolve_misconception("a", 0)
    items = store.get_page("a")["misconceptions"]
    assert items[0]["status"] == "resolved"
    with pytest.raises(IndexError):
        store.resolve_misconception("a", 5)


def test_grade_card_routes_into_strength(store):
    card = store.add_card("a", "what reverses in simpson's paradox?")
    out = store.grade_card(card["id"], 3)
    assert out["ledger"]["strength"] == pytest.approx(0.35)
    out = store.grade_card(card["id"], 1)
    assert out["ledger"]["strength"] == pytest.approx(0.35 * 0.6)
