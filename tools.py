"""Agent-facing tools. Every tool returns a compact JSON string.

The tools are the ONLY mutation path the model gets, and they encode the
ledger discipline: `wiki_file` (filing) cannot move strength; `ledger_record`
and `review_grade` (retrieval events) are the only strength writers.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from langchain_core.tools import tool

log = logging.getLogger("protoagent.plugins.learning_wiki")


def _ok(**kw) -> str:
    return json.dumps({"ok": True, **kw}, default=str)


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": str(msg)[:500]})


def build_tools(cfg: dict, get_store):
    desired_retention = float(cfg.get("desired_retention") or 0.9)
    weights = list(cfg.get("fsrs_weights") or []) or None

    @tool
    def wiki_index() -> str:
        """List all wiki pages: slug, title, kind, learner tier, and count of due review cards per page."""
        try:
            pages = get_store().list_pages()
            slim = [{k: p[k] for k in ("slug", "title", "kind", "tier", "summary", "due_cards")} for p in pages]
            return _ok(pages=slim, count=len(slim))
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def wiki_page(slug: str) -> str:
        """Read one wiki page in full: content, links, backlinks, learner ledger (strength, tier, misconceptions), recent revisions."""
        try:
            page = get_store().get_page(slug)
            if page is None:
                return _err(f"no page '{slug}' — file it with wiki_file, or check wiki_index")
            return _ok(page=page)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def wiki_file(
        slug: str,
        title: str = "",
        content_md: str = "",
        summary: str = "",
        kind: str = "concept",
        change_summary: str = "",
        source_kind: str = "chat",
        source_ref: str = "",
        links_json: str = "",
    ) -> str:
        """File knowledge into the wiki: create or update a page (kinds: concept/entity/source-summary/analysis).

        Every content change records a revision with provenance (change_summary + source_kind:
        chat/research/ingest/lint/manual + source_ref). [[Wikilinks]] in content_md become
        'related' edges automatically; pass links_json like
        [{"slug": "conditional-probability", "rel": "prerequisite"}] for typed edges
        (rels: related/prerequisite/part-of/contrast). Filing NEVER changes learner strength —
        record actual retrieval with ledger_record instead. Prefer the learner's own words
        for content: the wiki should read like the person who learned it."""
        try:
            store = get_store()
            page = store.upsert_page(
                slug,
                title=title,
                content_md=content_md,
                summary=summary,
                kind=kind,
                change_summary=change_summary,
                source_kind=source_kind,
                source_ref=source_ref,
            )
            for link in json.loads(links_json) if links_json else []:
                store.add_link(page["slug"], link["slug"], link.get("rel", "related"))
            page = store.get_page(page["slug"])
            return _ok(slug=page["slug"], title=page["title"], links=page["links"], revisions=len(page["revisions"]))
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def wiki_link(from_slug: str, to_slug: str, rel: str = "related") -> str:
        """Add a typed edge between two pages (rel: related/prerequisite/part-of/contrast). Missing pages become stubs. 'A prerequisite B' means: to understand from_slug you first need to_slug."""
        try:
            get_store().add_link(from_slug, to_slug, rel)
            return _ok(link={"from": from_slug, "to": to_slug, "rel": rel})
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def ledger_status(slug: str = "") -> str:
        """Learner-ledger report: per-concept strength, tier (novice <0.3 / frontier / fluent >0.7), last retrieval, open misconceptions, due cards. Pass a slug for one concept, empty for the full frontier. Use this to pitch explanations at the right tier."""
        try:
            store = get_store()
            if slug:
                page = store.get_page(slug)
                if page is None:
                    return _err(f"no page '{slug}'")
                keys = ("slug", "strength", "tier", "last_retrieved", "misconceptions", "evidence")
                return _ok(concept={k: page[k] for k in keys})
            rows = store.ledger()
            slim = [
                {
                    k: p[k]
                    for k in ("slug", "title", "strength", "tier", "last_retrieved", "open_misconceptions", "due_cards")
                }
                for p in rows
            ]
            return _ok(ledger=slim, stats=store.stats())
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def ledger_record(slug: str, outcome: str, note: str = "") -> str:
        """Record a GENUINE retrieval event and update concept strength — the only path that moves strength.

        outcome: success (retrieved/applied unaided), partial (retrieved with help or gaps),
        failure (could not retrieve). Only real attempts count: the learner explaining back,
        answering a probe, solving a transfer problem. Reading, nodding along, or hearing a
        good explanation is NOT retrieval — never record those."""
        try:
            return _ok(**get_store().record_retrieval(slug, outcome, note=note))
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def ledger_misconception(slug: str, add: str = "", resolve_index: int = -1) -> str:
        """Track misconceptions on a concept: pass add='the wrong belief, precisely' to log one (open), or resolve_index=N to mark item N resolved after the learner demonstrates the correction. Corrected misconceptions deserve a recheck card (card_add origin=misconception)."""
        try:
            store = get_store()
            if add:
                items = store.add_misconception(slug, add)
            elif resolve_index >= 0:
                items = store.resolve_misconception(slug, resolve_index)
            else:
                return _err("pass add='...' or resolve_index=N")
            return _ok(slug=slug, misconceptions=items)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def card_add(slug: str, prompt: str, answer: str = "", origin: str = "restatement") -> str:
        """Create a retrieval card for a concept, due immediately. origin: miss (a failed question), misconception (recheck of a corrected error — highest value), restatement (reconstruct their own explanation), transfer (same concept, novel context). Generate cards from what happened in the session, not by bulk-converting pages."""
        try:
            card = get_store().add_card(slug, prompt, answer=answer, origin=origin)
            return _ok(card_id=card["id"], slug=card["slug"], due=card["due"])
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def review_next(limit: int = 8) -> str:
        """Fetch due review cards, interleaved across concepts (deliberately not blocked by topic). Quiz the learner one card at a time WITHOUT revealing answers, then grade each attempt with review_grade."""
        try:
            cards = get_store().due_cards(limit=limit)
            slim = [{k: c[k] for k in ("id", "slug", "prompt", "origin", "reps", "due")} for c in cards]
            return _ok(cards=slim, due_total=get_store().due_count())
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def review_grade(card_id: int, rating: int, note: str = "") -> str:
        """Grade one reviewed card honestly: 1=again (failed), 2=hard, 3=good, 4=easy. FSRS reschedules the card (again returns within ~10 min; success grows the interval) and the outcome updates concept strength. Never inflate a rating to be kind — miscalibration poisons the schedule."""
        try:
            out = get_store().grade_card(
                card_id, rating, note=note, weights=weights, desired_retention=desired_retention
            )
            card = out["card"]
            return _ok(
                card_id=card_id,
                slug=card["slug"],
                state=card["state"],
                next_due=card["due"],
                interval_days=card["interval_days"],
                ledger=out["ledger"],
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def wiki_export(out_dir: str = "") -> str:
        """Export every wiki page as a markdown file with front-matter (title, kind, strength). Default target: <data_dir>/export. Returns the directory and file count."""
        try:
            from . import _data_dir

            target = out_dir or str(_data_dir(cfg) / "export")
            n = get_store().export_markdown(target)
            return _ok(dir=target, files=n)
        except Exception as e:  # noqa: BLE001
            return _err(e)

    @tool
    def wiki_research(topic: str) -> str:
        """Research a topic via the rabbit-hole `rh` CLI (deep web research) and return the report markdown for filing. Requires learning_wiki.rh_enabled=true and `rh` on PATH; after reading the report, file the concepts with wiki_file (source_kind=research)."""
        if not cfg.get("rh_enabled"):
            return _err("rh integration is disabled — set learning_wiki.rh_enabled: true")
        rh_bin = str(cfg.get("rh_bin") or "rh")
        if shutil.which(rh_bin) is None:
            return _err(f"'{rh_bin}' not found on PATH — install @protolabsai/rabbit-hole-cli")
        try:
            proc = subprocess.run(
                [rh_bin, "research", topic, "--text"],
                capture_output=True,
                text=True,
                timeout=int(cfg.get("rh_timeout_s") or 300),
            )
            if proc.returncode != 0:
                return _err(f"rh research failed: {proc.stderr.strip()[:300]}")
            return _ok(topic=topic, report=proc.stdout)
        except subprocess.TimeoutExpired:
            return _err(f"rh research timed out after {cfg.get('rh_timeout_s') or 300}s")
        except Exception as e:  # noqa: BLE001
            return _err(e)

    return [
        wiki_index,
        wiki_page,
        wiki_file,
        wiki_link,
        ledger_status,
        ledger_record,
        ledger_misconception,
        card_add,
        review_next,
        review_grade,
        wiki_export,
        wiki_research,
    ]
