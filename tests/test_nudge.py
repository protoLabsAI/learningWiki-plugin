"""Nudge surface: inert at 0, emits when cards are due, stops cleanly."""

from __future__ import annotations

from learning_wiki.nudge import ReviewNudge


def test_interval_zero_is_inert(store):
    nudge = ReviewNudge({"nudge_interval_hours": 0}, lambda: store)
    nudge.start()
    assert nudge._thread is None
    nudge.stop()  # must not raise


def test_check_once_emits_only_when_due(store):
    events = []
    nudge = ReviewNudge({"nudge_interval_hours": 1}, lambda: store, emitter=lambda t, d: events.append((t, d)))
    assert nudge.check_once() == 0
    assert events == []
    store.add_card("a", "q?")
    assert nudge.check_once() == 1
    assert events == [("reviews_due", {"due": 1})]


def test_emitter_failure_is_swallowed(store):
    def boom(topic, data):
        raise RuntimeError("bus down")

    store.add_card("a", "q?")
    nudge = ReviewNudge({"nudge_interval_hours": 1}, lambda: store, emitter=boom)
    assert nudge.check_once() == 1  # no raise


def test_start_stop_thread_lifecycle(store):
    nudge = ReviewNudge({"nudge_interval_hours": 0.0001}, lambda: store)  # ~0.36s ticks
    nudge.start()
    assert nudge._thread is not None and nudge._thread.is_alive()
    nudge.stop()
    assert nudge._thread is None
