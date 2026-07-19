"""A2A card skills: structured contracts other agents can rely on."""

from __future__ import annotations

from learning_wiki.a2a import A2A_SKILLS


def test_two_skills_with_json_contracts():
    assert [s["id"] for s in A2A_SKILLS] == ["learning_status", "quiz_me"]
    for s in A2A_SKILLS:
        assert s["result_mime"] == "application/json"
        assert s["output_schema"]["type"] == "object"
        assert s["description"] and s["examples"]


def test_quiz_me_never_promises_answers():
    quiz = next(s for s in A2A_SKILLS if s["id"] == "quiz_me")
    card_props = quiz["output_schema"]["properties"]["cards"]["items"]["properties"]
    assert "answer" not in card_props  # answers stay local; grading happens here
    assert "withheld" in quiz["description"]


def test_registered_on_the_registry(registry):
    assert [s["id"] for s in registry.a2a_skills] == ["learning_status", "quiz_me"]
