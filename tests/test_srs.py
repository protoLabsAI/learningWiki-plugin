"""FSRS-4.5 behavioral properties — the scheduler must be trustworthy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from learning_wiki import srs

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
NEW = {"stability": 0.0, "difficulty": 0.0, "reps": 0, "lapses": 0, "state": "new", "last_review": None}


def test_first_review_uses_initial_stability_weights():
    for g in (1, 2, 3, 4):
        out = srs.review(dict(NEW), g, now=NOW)
        assert out["stability"] == pytest.approx(srs.DEFAULT_WEIGHTS[g - 1])
        assert out["reps"] == 1


def test_higher_rating_longer_interval():
    ivls = [srs.review(dict(NEW), g, now=NOW)["interval_days"] for g in (2, 3, 4)]
    assert ivls[0] <= ivls[1] <= ivls[2]
    assert ivls[2] > ivls[0]


def test_again_relearns_within_session_and_counts_lapse_only_from_review():
    first = srs.review(dict(NEW), 1, now=NOW)
    assert first["state"] == "learning"
    assert first["lapses"] == 0  # a stumble on first contact is not a lapse
    due = datetime.fromisoformat(first["due"])
    assert (due - NOW) <= timedelta(minutes=srs.RELEARN_MINUTES)

    reviewed = srs.review(dict(NEW), 3, now=NOW)
    lapsed = srs.review(reviewed, 1, now=NOW + timedelta(days=3))
    assert lapsed["state"] == "relearning"
    assert lapsed["lapses"] == 1


def test_success_grows_stability_over_reps():
    card = dict(NEW)
    stabilities = []
    t = NOW
    for _ in range(4):
        card = srs.review(card, 3, now=t)
        stabilities.append(card["stability"])
        t = datetime.fromisoformat(card["due"])
    assert stabilities == sorted(stabilities)
    assert stabilities[-1] > stabilities[0] * 2


def test_lapse_shrinks_stability():
    card = srs.review(dict(NEW), 4, now=NOW)
    s_before = card["stability"]
    lapsed = srs.review(card, 1, now=NOW + timedelta(days=10))
    assert lapsed["stability"] < s_before


def test_retrievability_declines_and_anchors_at_90pct():
    assert srs.retrievability(0, 10) == pytest.approx(1.0)
    assert srs.retrievability(10, 10) == pytest.approx(0.9)
    assert srs.retrievability(30, 10) < srs.retrievability(10, 10)


def test_lower_desired_retention_means_longer_intervals():
    lax = srs.next_interval_days(10, 0.8)
    strict = srs.next_interval_days(10, 0.95)
    assert lax > strict


def test_weight_override_changes_schedule_and_is_validated():
    w = list(srs.DEFAULT_WEIGHTS)
    w[2] = 10.0  # inflate initial stability for "good"
    out = srs.review(dict(NEW), 3, now=NOW, weights=w)
    assert out["stability"] == pytest.approx(10.0)
    with pytest.raises(ValueError):
        srs.review(dict(NEW), 3, now=NOW, weights=[1.0, 2.0])


def test_invalid_rating_rejected():
    with pytest.raises(ValueError):
        srs.review(dict(NEW), 5, now=NOW)


def test_difficulty_stays_clamped():
    card = dict(NEW)
    t = NOW
    for _ in range(10):
        card = srs.review(card, 1, now=t)
        t = t + timedelta(days=1)
        assert 1.0 <= card["difficulty"] <= 10.0
