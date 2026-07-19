"""Knowledge-map SVG: layered by prerequisites, tier-colored, deterministic."""

from __future__ import annotations

from learning_wiki.knowledge_map import COL_W, PAD, TIER_FILL, render_map


def _page(slug, tier="novice", due=0):
    return {"slug": slug, "title": slug.title(), "tier": tier, "due_cards": due}


def test_prerequisite_layering_and_edge_styles():
    pages = [_page("advanced", "frontier"), _page("basics", "fluent"), _page("aside")]
    links = [
        {"from_slug": "advanced", "to_slug": "basics", "rel": "prerequisite"},
        {"from_slug": "advanced", "to_slug": "aside", "rel": "related"},
    ]
    svg = render_map(pages, links)
    assert svg.startswith("<svg") or svg.startswith("<svg xmlns") or "<svg" in svg.split("\n")[0]
    # basics (the prerequisite) sits in column 0, advanced in column 1
    assert f'cx="{PAD}"' in svg
    assert f'cx="{PAD + COL_W}"' in svg
    # prerequisite edge carries the arrow; related is dashed
    assert 'marker-end="url(#arrow)"' in svg
    assert 'stroke-dasharray="4 4"' in svg
    # tiers color the nodes
    assert TIER_FILL["fluent"] in svg and TIER_FILL["frontier"] in svg and TIER_FILL["novice"] in svg


def test_deterministic_output():
    pages = [_page(s) for s in ("c", "a", "b")]
    links = [{"from_slug": "a", "to_slug": "b", "rel": "prerequisite"}]
    assert render_map(pages, links) == render_map(pages, links)


def test_cycle_guard_terminates():
    pages = [_page("a"), _page("b")]
    links = [
        {"from_slug": "a", "to_slug": "b", "rel": "prerequisite"},
        {"from_slug": "b", "to_slug": "a", "rel": "prerequisite"},
    ]
    assert "<svg" in render_map(pages, links)


def test_cap_limits_nodes_and_due_badges_render():
    pages = [_page(f"p{i}", due=1) for i in range(5)]
    svg = render_map(pages, [], cap=2)
    assert svg.count("<circle") == 2
    assert "due" in svg


def test_titles_are_escaped():
    svg = render_map([_page("x") | {"title": 'a<b>&"c'}], [])
    assert "<b>" not in svg and "&amp;" in svg
