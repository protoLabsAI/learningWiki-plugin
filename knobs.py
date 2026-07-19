"""Tutor knobs (graph.sdk Knobs seam): live-tunable settings with presets.

Only knobs the code actually READS are declared — desired_retention feeds
review_grade's FSRS target, session_limit caps review_next. The generated
`tutor_knobs` / `tutor_tune` / `tutor_preset` agent tools come from
make_knob_tools. Host-free, everything degrades to the config/default value.
"""

from __future__ import annotations

KNOB_SPECS = [
    {
        "name": "desired_retention",
        "default": 0.9,
        "lo": 0.7,
        "hi": 0.97,
        "help": "FSRS target recall probability at review time; higher = shorter intervals, more reviews.",
    },
    {
        "name": "session_limit",
        "default": 8,
        "lo": 3,
        "hi": 30,
        "help": "Max cards served per review session (review_next).",
    },
]

PRESETS = {
    "exam-cram": ({"desired_retention": 0.95, "session_limit": 20}, "deadline mode: high retention, big sessions"),
    "steady": ({"desired_retention": 0.9, "session_limit": 8}, "the default cadence"),
    "light": ({"desired_retention": 0.85, "session_limit": 5}, "maintenance mode: fewer, longer-spaced reviews"),
}

_KNOBS = None


def get_knobs():
    """The process-wide Knobs instance, or None with no host."""
    global _KNOBS
    if _KNOBS is None:
        try:
            from graph.sdk import Knobs  # host-only, lazy
        except ImportError:
            return None
        k = Knobs()
        for s in KNOB_SPECS:
            k.define(s["name"], s["default"], lo=s["lo"], hi=s["hi"], help=s["help"])
        for name, (overrides, blurb) in PRESETS.items():
            k.preset(name, overrides, blurb=blurb)
        _KNOBS = k
    return _KNOBS


def knob_value(name: str, fallback):
    """Live knob value, or `fallback` (config/default) when host-free."""
    k = get_knobs()
    if k is None:
        return fallback
    try:
        return k.get(name)
    except Exception:  # noqa: BLE001 — a knob must never break a review
        return fallback


def build_knob_tools() -> list:
    """tutor_knobs/_tune/_preset tools, or [] with no host."""
    k = get_knobs()
    if k is None:
        return []
    from graph.sdk import make_knob_tools  # host-only, lazy

    return make_knob_tools(k, prefix="tutor")


def _reset_knobs_for_tests() -> None:
    global _KNOBS
    _KNOBS = None
