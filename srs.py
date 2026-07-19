"""FSRS-4.5 scheduler — pure Python, no dependencies.

Implements the FSRS-4.5 memory model (open-spaced-repetition): two state
variables per card — stability S (days until retrievability decays to 90%)
and difficulty D in [1, 10] — updated per review by a rating G in
{1 again, 2 hard, 3 good, 4 easy}. Scheduling math is a solved problem, so
this ports the published formulas instead of inventing intervals (ADR 0001);
the 17 default weights are the published FSRS-4.5 defaults and can be
overridden via `learning_wiki.fsrs_weights`.

Reference: https://github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

DECAY = -0.5
FACTOR = 19.0 / 81.0  # chosen so R(S, S) = 0.9

# FSRS-4.5 published defaults, w0..w16.
DEFAULT_WEIGHTS = (
    0.4872,
    1.4003,
    3.7145,
    13.8206,
    5.1618,
    1.2298,
    0.8975,
    0.031,
    1.6474,
    0.1367,
    1.0461,
    2.1072,
    0.0793,
    0.3246,
    1.587,
    0.2272,
    2.8755,
)

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4
RELEARN_MINUTES = 10  # a missed card comes back within the session, not tomorrow
MIN_STABILITY = 0.1
MAX_INTERVAL_DAYS = 36500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability of recall after `elapsed_days` at stability S. R(S, S) = 0.9."""
    stability = max(stability, MIN_STABILITY)
    return (1.0 + FACTOR * max(elapsed_days, 0.0) / stability) ** DECAY


def next_interval_days(stability: float, desired_retention: float) -> float:
    """Interval after which retrievability falls to `desired_retention`."""
    stability = max(stability, MIN_STABILITY)
    ivl = (stability / FACTOR) * (desired_retention ** (1.0 / DECAY) - 1.0)
    return min(max(ivl, 1.0), MAX_INTERVAL_DAYS)


def _clamp_d(d: float) -> float:
    return min(max(d, 1.0), 10.0)


def _init_stability(w, g: int) -> float:
    return max(w[g - 1], MIN_STABILITY)


def _init_difficulty(w, g: int) -> float:
    return _clamp_d(w[4] - math.exp(w[5] * (g - 1)) + 1.0)


def _next_difficulty(w, d: float, g: int) -> float:
    # Linear damping (FSRS-4.5): the delta shrinks as D approaches 10 …
    delta = -w[6] * (g - 3)
    d_damped = d + delta * (10.0 - d) / 9.0
    # … then mean reversion toward D0(easy).
    return _clamp_d(w[7] * _init_difficulty(w, EASY) + (1.0 - w[7]) * d_damped)


def _stability_success(w, d: float, s: float, r: float, g: int) -> float:
    hard_penalty = w[15] if g == HARD else 1.0
    easy_bonus = w[16] if g == EASY else 1.0
    grow = math.exp(w[8]) * (11.0 - d) * s ** (-w[9]) * (math.exp(w[10] * (1.0 - r)) - 1.0)
    return max(s * (1.0 + grow * hard_penalty * easy_bonus), MIN_STABILITY)


def _stability_fail(w, d: float, s: float, r: float) -> float:
    s_fail = w[11] * d ** (-w[12]) * ((s + 1.0) ** w[13] - 1.0) * math.exp(w[14] * (1.0 - r))
    # Post-lapse stability never exceeds the pre-lapse stability.
    return max(min(s_fail, s), MIN_STABILITY)


def review(
    card: dict,
    rating: int,
    now: datetime | None = None,
    weights=None,
    desired_retention: float = 0.9,
) -> dict:
    """Apply one review to a card state and return the updated state.

    `card` needs: stability, difficulty, reps, lapses, state, last_review
    (ISO string or None). Returns a new dict with those fields updated plus
    `due` (ISO) and `interval_days` (the scheduled gap; 0 for the in-session
    relearn step).
    """
    if rating not in (AGAIN, HARD, GOOD, EASY):
        raise ValueError(f"rating must be 1..4, got {rating!r}")
    w = tuple(weights) if weights else DEFAULT_WEIGHTS
    if len(w) != len(DEFAULT_WEIGHTS):
        raise ValueError(f"fsrs_weights must have {len(DEFAULT_WEIGHTS)} floats, got {len(w)}")
    now = now or _utcnow()

    reps = int(card.get("reps") or 0)
    lapses = int(card.get("lapses") or 0)
    state = card.get("state") or "new"

    if reps == 0 or not card.get("last_review"):
        s = _init_stability(w, rating)
        d = _init_difficulty(w, rating)
    else:
        last = datetime.fromisoformat(card["last_review"])
        elapsed = max((now - last).total_seconds() / 86400.0, 0.0)
        s0 = max(float(card.get("stability") or MIN_STABILITY), MIN_STABILITY)
        d0 = _clamp_d(float(card.get("difficulty") or 5.0))
        r = retrievability(elapsed, s0)
        d = _next_difficulty(w, d0, rating)
        s = _stability_fail(w, d0, s0, r) if rating == AGAIN else _stability_success(w, d0, s0, r, rating)

    if rating == AGAIN:
        if state == "review":
            lapses += 1
        new_state = "relearning" if state in ("review", "relearning") else "learning"
        due = now + timedelta(minutes=RELEARN_MINUTES)
        interval_days = 0.0
    else:
        new_state = "review"
        interval_days = round(next_interval_days(s, desired_retention))
        due = now + timedelta(days=interval_days)

    return {
        "stability": s,
        "difficulty": d,
        "reps": reps + 1,
        "lapses": lapses,
        "state": new_state,
        "last_review": now.isoformat(),
        "due": due.isoformat(),
        "interval_days": float(interval_days),
    }
