"""A2A card skills (register_a2a_skill seam): what OTHER agents can ask this
agent about its learner. Structured output enforced by the host's finalizer —
callers parse JSON, they don't scrape prose. Pure data, host-free testable."""

from __future__ import annotations

A2A_SKILLS = [
    {
        "id": "learning_status",
        "name": "Learning status",
        "description": (
            "Structured snapshot of this agent's learner ledger: page count, due review "
            "cards, average strength, and the weakest frontier concepts. Ground truth from "
            "the ledger (ledger_status) — strength only ever moves on retrieval events."
        ),
        "tags": ["learning", "wiki", "telemetry"],
        "examples": ["What are you currently learning?", "Send me your learning status."],
        "output_schema": {
            "type": "object",
            "required": ["pages", "due", "avg_strength", "weakest"],
            "properties": {
                "pages": {"type": "integer"},
                "due": {"type": "integer", "description": "review cards due now"},
                "avg_strength": {"type": "number"},
                "weakest": {
                    "type": "array",
                    "description": "up to 5 lowest-strength non-stub concepts",
                    "items": {
                        "type": "object",
                        "required": ["slug", "strength", "tier"],
                        "properties": {
                            "slug": {"type": "string"},
                            "strength": {"type": "number"},
                            "tier": {"type": "string", "enum": ["novice", "frontier", "fluent"]},
                        },
                    },
                },
            },
        },
        "result_mime": "application/json",
    },
    {
        "id": "quiz_me",
        "name": "Quiz material",
        "description": (
            "Due retrieval prompts from this agent's spaced-review queue (review_next), "
            "interleaved across concepts, answers withheld. The caller may relay them to a "
            "human; grading stays HERE via chat (review_grade needs the attempt in context)."
        ),
        "tags": ["learning", "quiz", "spaced-repetition"],
        "examples": ["Give me 5 quiz questions from your review queue."],
        "output_schema": {
            "type": "object",
            "required": ["cards"],
            "properties": {
                "cards": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["slug", "prompt", "origin"],
                        "properties": {
                            "slug": {"type": "string"},
                            "prompt": {"type": "string"},
                            "origin": {"type": "string", "enum": ["miss", "misconception", "restatement", "transfer"]},
                        },
                    },
                }
            },
        },
        "result_mime": "application/json",
    },
]
