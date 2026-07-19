"""Tutor knobs: host-free fallback, hosted definition, and live read-through."""

from __future__ import annotations

import json

from learning_wiki.knobs import KNOB_SPECS, PRESETS, _reset_knobs_for_tests, build_knob_tools, get_knobs, knob_value


def test_specs_only_declare_knobs_the_code_reads():
    assert {s["name"] for s in KNOB_SPECS} == {"desired_retention", "session_limit"}
    assert set(PRESETS) == {"exam-cram", "steady", "light"}
    for overrides, _blurb in PRESETS.values():
        assert set(overrides) <= {s["name"] for s in KNOB_SPECS}


def test_host_free_fallbacks():
    _reset_knobs_for_tests()
    assert get_knobs() is None
    assert knob_value("desired_retention", 0.88) == 0.88
    assert build_knob_tools() == []


def test_hosted_knobs_define_and_read(host_stub):
    _reset_knobs_for_tests()
    k = get_knobs()
    assert k is not None
    assert k.get("desired_retention") == 0.9
    assert k.presets_map["exam-cram"]["session_limit"] == 20
    assert build_knob_tools() == [] and host_stub["knob_prefix"] == "tutor"
    _reset_knobs_for_tests()


def test_session_limit_knob_caps_review_next(registry_hosted):
    reg, _ = registry_hosted
    from conftest import tool_by_name
    from learning_wiki.knobs import get_knobs

    for i in range(4):
        tool_by_name(reg, "card_add").invoke({"slug": "a", "prompt": f"q{i}?"})
    get_knobs().values_map["session_limit"] = 2  # live tune
    out = json.loads(tool_by_name(reg, "review_next").invoke({}))
    assert len(out["cards"]) == 2
    out = json.loads(tool_by_name(reg, "review_next").invoke({"limit": 4}))
    assert len(out["cards"]) == 4  # explicit limit still wins
