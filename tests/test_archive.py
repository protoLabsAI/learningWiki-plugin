"""Archive export/import: full-fidelity round trip, verbatim state (an import is
NOT retrieval — the ledger invariant), and merge/replace collision semantics."""

from __future__ import annotations

import json

import pytest


def _far_future():
    from datetime import datetime, timezone

    return datetime(2099, 1, 1, tzinfo=timezone.utc)


from learning_wiki.store import WikiStore


def make_store(path):
    return WikiStore(path)


def _populated(tmp_path):
    s = make_store(tmp_path / "src.db")
    s.upsert_page("attention", title="Attention", content_md="# Attention\nQ·K then ·V", summary="core op")
    s.upsert_page("softmax", title="Softmax", content_md="# Softmax", summary="normalizer")
    s.add_link("softmax", "attention", rel="prerequisite")
    s.record_retrieval("attention", outcome="partial", note="Q·K vs Q·V swap")
    s.record_retrieval("attention", outcome="success", note="corrected pairing")
    card = s.add_card(
        "attention", prompt="What do attention weights score?", answer="Q against K", origin="misconception"
    )
    s.grade_card(card["id"], rating=3)
    return s


def test_round_trip_is_verbatim(tmp_path):
    src = _populated(tmp_path)
    before_page = src.get_page("attention")
    res = src.export_archive(tmp_path / "a.json")
    assert res["pages"] == 2 and res["links"] == 1 and res["cards"] == 1

    dst = make_store(tmp_path / "dst.db")
    out = dst.import_archive(tmp_path / "a.json")
    assert out["imported"] == 2 and out["skipped"] == [] and out["links"] == 1

    after = dst.get_page("attention")
    # strength/FSRS state land EXACTLY — imported, not re-earned
    assert after["strength"] == pytest.approx(before_page["strength"])
    assert after["content_md"] == before_page["content_md"]
    src_card = src.due_cards(limit=10, now=_far_future())[0]
    dst_card = dst.due_cards(limit=10, now=_far_future())[0]
    for k in ("prompt", "stability", "difficulty", "reps", "state", "due"):
        assert dst_card[k] == src_card[k], k


def test_merge_skips_existing_replace_overwrites(tmp_path):
    src = _populated(tmp_path)
    src.export_archive(tmp_path / "a.json")

    dst = make_store(tmp_path / "dst.db")
    dst.upsert_page("attention", title="My attention", content_md="local notes", summary="mine")
    out = dst.import_archive(tmp_path / "a.json", mode="merge")
    assert out["skipped"] == ["attention"] and out["imported"] == 1
    assert dst.get_page("attention")["content_md"] == "local notes"  # merge never clobbers

    out = dst.import_archive(tmp_path / "a.json", mode="replace")
    assert "attention" in [s for s in ("attention",) if out["imported"] >= 1]
    assert dst.get_page("attention")["content_md"].startswith("# Attention")  # replaced


def test_import_rejects_foreign_and_future_files(tmp_path):
    dst = make_store(tmp_path / "d.db")
    bad = tmp_path / "x.json"
    bad.write_text(json.dumps({"format": "something-else"}))
    with pytest.raises(ValueError):
        dst.import_archive(bad)
    future = tmp_path / "f.json"
    future.write_text(json.dumps({"format": "learning-wiki-archive", "version": 99, "pages": []}))
    with pytest.raises(ValueError):
        dst.import_archive(future)
