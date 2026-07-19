"""Subagent specs: the review coach and the wiki lint pass.

Specs are plain dicts (host-free testable); ``build_subagents`` lazily imports
the host's SubagentConfig and returns [] when no host is present (same pattern
as plugins/coder). Both are scheduled via plugin-owned cron jobs in register()
— the dream/distill pattern (ADR 0054): a scheduled job fires a normal agent
turn, no new scheduling machinery.
"""

from __future__ import annotations

REVIEW_COACH_SPEC = {
    "name": "review-coach",
    "description": (
        "Runs one spaced-repetition review session over the learning wiki's due cards. "
        "Delegate to it for a scheduled or requested review pass."
    ),
    "system_prompt": (
        "You are the review coach for the learning wiki. Run ONE review session, then stop.\n"
        "1. Call review_next. If nothing is due, report 'no cards due' in one line and stop.\n"
        "2. For each card: present ONLY the prompt (never the answer), wait for the learner's "
        "attempt in the conversation, then grade honestly with review_grade "
        "(1 failed / 2 hard / 3 good / 4 easy). Never inflate a rating.\n"
        "3. A failed card returns within minutes — re-quiz it before ending the session.\n"
        "4. Close with a two-line summary: cards cleared, strength movement, next due date.\n"
        "Honesty discipline: miscalibrated grades poison the FSRS schedule; a kind lie now "
        "costs retention later."
    ),
    "tools": ["review_next", "review_grade", "ledger_status", "wiki_page"],
    "max_turns": 30,
}

WIKI_LINT_SPEC = {
    "name": "wiki-lint",
    "description": (
        "Read-only curation pass over the learning wiki: contradictions, orphan stubs, "
        "stale strengths, missing prerequisite edges. Reports; never edits."
    ),
    "system_prompt": (
        "You are the wiki lint pass (read-only — you have no write tools by design).\n"
        "Sweep the wiki and report, in priority order:\n"
        "- ORPHAN STUBS: pages with empty content that nothing links to.\n"
        "- STALE STRENGTH: concepts above novice whose last retrieval is >30 days old.\n"
        "- FRONTIER INCONSISTENCY: a page at frontier/fluent whose prerequisite pages "
        "have never been retrieved (strength 0).\n"
        "- OPEN MISCONCEPTIONS with no recheck card on the page.\n"
        "- CONTRADICTIONS between page contents you actually read.\n"
        "Output a compact markdown report with slugs and one suggested fix per finding. "
        "If the wiki is clean, say so in one line. Do not invent findings."
    ),
    "tools": ["wiki_index", "wiki_page", "ledger_status", "review_next"],
    "max_turns": 20,
}


def build_subagents():
    """Instantiate host SubagentConfig objects; [] when the host isn't importable."""
    try:
        from graph.subagents.config import SubagentConfig  # host-only, lazy
    except ImportError:
        return []
    return [SubagentConfig(**REVIEW_COACH_SPEC), SubagentConfig(**WIKI_LINT_SPEC)]
