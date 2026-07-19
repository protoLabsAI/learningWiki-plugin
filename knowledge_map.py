"""Knowledge-map renderer: the wiki as a tier-colored SVG, pure Python.

Layout is a prerequisite-layered DAG: an edge A --prerequisite--> B means "to
understand A you first need B", so B sits in an earlier column. Colors match
the console view's tier fallbacks. Deterministic output (sorted everywhere) so
tests can pin it and repeated calls dedupe in the media store.

SVG (not PNG) keeps the plugin zero-dep — the console <img> renders it fine.
The trade-off, documented in SEAMS.md: no `multimodal_tool_result` for the map,
because vision models take raster formats and rasterizing needs a pip dep.
"""

from __future__ import annotations

MAP_CAP = 60  # most-recently-updated pages; the tool reports truncation loudly

TIER_FILL = {"novice": "#d9a441", "frontier": "#6f9bff", "fluent": "#57b98a"}
TEXT = "#8a94a3"
EDGE = "#7a8697"

COL_W = 230
ROW_H = 64
PAD = 40
R = 9


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _depths(slugs: list[str], links: list[dict]) -> dict[str, int]:
    """Column per node = longest prerequisite chain below it (cycle-guarded)."""
    keep = set(slugs)
    prereqs: dict[str, list[str]] = {s: [] for s in slugs}
    for e in links:
        if e["rel"] == "prerequisite" and e["from_slug"] in keep and e["to_slug"] in keep:
            prereqs[e["from_slug"]].append(e["to_slug"])
    memo: dict[str, int] = {}

    def depth(slug: str, trail: frozenset) -> int:
        if slug in memo:
            return memo[slug]
        if slug in trail:  # cycle — break it, don't recurse forever
            return 0
        below = [depth(p, trail | {slug}) for p in prereqs[slug]]
        memo[slug] = 1 + max(below) if below else 0
        return memo[slug]

    for s in slugs:
        depth(s, frozenset())
    return memo


def render_map(pages: list[dict], links: list[dict], cap: int = MAP_CAP) -> str:
    """pages: store.list_pages() rows (recency-ordered); links: store.all_links()."""
    kept = pages[:cap]
    slugs = [p["slug"] for p in kept]
    by_slug = {p["slug"]: p for p in kept}
    depths = _depths(slugs, links)

    cols: dict[int, list[str]] = {}
    for s in sorted(slugs):
        cols.setdefault(depths[s], []).append(s)
    pos: dict[str, tuple[int, int]] = {}
    for d, members in cols.items():
        for i, s in enumerate(members):
            pos[s] = (PAD + d * COL_W, PAD + i * ROW_H)

    n_cols = max(cols) + 1 if cols else 1
    n_rows = max((len(m) for m in cols.values()), default=1)
    width = PAD + n_cols * COL_W + 130
    height = PAD + n_rows * ROW_H + PAD

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui,sans-serif" font-size="12">',
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{EDGE}"/></marker></defs>',
    ]

    # Edges first (under the nodes). Prerequisite: solid arrow FROM the
    # prerequisite TO the dependent (the learning direction). Others: dashed.
    for e in sorted(links, key=lambda x: (x["from_slug"], x["to_slug"], x["rel"])):
        a, b = e["from_slug"], e["to_slug"]
        if a not in pos or b not in pos:
            continue
        if e["rel"] == "prerequisite":
            (x1, y1), (x2, y2) = pos[b], pos[a]  # fundamental → dependent
            extra = ' marker-end="url(#arrow)"'
        else:
            (x1, y1), (x2, y2) = pos[a], pos[b]
            extra = ' stroke-dasharray="4 4"'
        parts.append(
            f'<line x1="{x1 + R}" y1="{y1}" x2="{x2 - R}" y2="{y2}" stroke="{EDGE}" '
            f'stroke-opacity="0.55" stroke-width="1.3"{extra}/>'
        )

    for s in sorted(slugs):
        x, y = pos[s]
        p = by_slug[s]
        fill = TIER_FILL.get(p["tier"], TIER_FILL["novice"])
        title = p["title"][:22] + ("…" if len(p["title"]) > 22 else "")
        parts.append(f'<circle cx="{x}" cy="{y}" r="{R}" fill="{fill}" fill-opacity="0.9"/>')
        due = f' <tspan fill="{TIER_FILL["novice"]}">·{p["due_cards"]} due</tspan>' if p.get("due_cards") else ""
        parts.append(f'<text x="{x + R + 6}" y="{y + 4}" fill="{TEXT}">{_esc(title)}{due}</text>')

    parts.append("</svg>")
    return "\n".join(parts)
