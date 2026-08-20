"""SQLite store: wiki pages + typed links + revisions, learner ledger + cards.

One file, instance-scoped (see __init__._data_dir). Everything is stdlib
sqlite3 behind a process-wide RLock (the tool loop, API routes, and the nudge
surface share one store).

THE INVARIANT (ADR 0001): only retrieval events move `concepts.strength` —
`record_retrieval()` is the single writer (card grading routes through it).
Filing, reading, linking, and listing never touch strength; the fluency of an
explanation must never be creditable as knowledge.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import srs

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'concept',
  summary TEXT NOT NULL DEFAULT '',
  content_md TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  content_md TEXT NOT NULL,
  change_summary TEXT NOT NULL DEFAULT '',
  source_kind TEXT NOT NULL DEFAULT 'chat',
  source_ref TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  from_page INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  to_page INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  rel TEXT NOT NULL DEFAULT 'related',
  PRIMARY KEY (from_page, to_page, rel)
);
CREATE TABLE IF NOT EXISTS concepts (
  page_id INTEGER PRIMARY KEY REFERENCES pages(id) ON DELETE CASCADE,
  strength REAL NOT NULL DEFAULT 0.0,
  last_retrieved TEXT,
  misconceptions TEXT NOT NULL DEFAULT '[]',
  evidence TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  prompt TEXT NOT NULL,
  answer TEXT NOT NULL DEFAULT '',
  origin TEXT NOT NULL DEFAULT 'restatement',
  stability REAL NOT NULL DEFAULT 0.0,
  difficulty REAL NOT NULL DEFAULT 0.0,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'new',
  due TEXT NOT NULL,
  last_review TEXT,
  suspended INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
  rating INTEGER NOT NULL,
  reviewed_at TEXT NOT NULL,
  interval_days REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cards_due ON cards (suspended, due);
CREATE INDEX IF NOT EXISTS idx_revisions_page ON revisions (page_id, created_at DESC);
"""

PAGE_KINDS = ("concept", "entity", "source-summary", "analysis")
LINK_RELS = ("related", "prerequisite", "part-of", "contrast")
SOURCE_KINDS = ("chat", "research", "ingest", "lint", "manual")
CARD_ORIGINS = ("miss", "misconception", "restatement", "transfer")

# Strength moves ONLY through record_retrieval: asymptotic gains on success,
# multiplicative decay on failure (a miss loses ground fast; regaining it is
# what the review loop is for).
_GAIN = {"success": 0.35, "partial": 0.15}
_FAIL_KEEP = 0.6
OUTCOMES = ("success", "partial", "failure")


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:80] or "untitled"


def tier(strength: float) -> str:
    if strength < 0.3:
        return "novice"
    if strength <= 0.7:
        return "frontier"
    return "fluent"


def extract_wikilinks(content_md: str) -> list[str]:
    """[[Target]] / [[target|label]] occurrences → deduped slugs, in order."""
    seen: list[str] = []
    for m in re.finditer(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", content_md):
        slug = slugify(m.group(1))
        if slug not in seen:
            seen.append(slug)
    return seen


class WikiStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── pages ────────────────────────────────────────────────────────────

    def upsert_page(
        self,
        slug: str,
        title: str = "",
        content_md: str = "",
        summary: str = "",
        kind: str = "concept",
        change_summary: str = "",
        source_kind: str = "chat",
        source_ref: str = "",
        now: datetime | None = None,
    ) -> dict:
        if kind not in PAGE_KINDS:
            raise ValueError(f"kind must be one of {PAGE_KINDS}")
        if source_kind not in SOURCE_KINDS:
            raise ValueError(f"source_kind must be one of {SOURCE_KINDS}")
        slug = slugify(slug)
        title = title or slug.replace("-", " ").title()
        ts = _now_iso(now)
        with self._lock, self._conn as c:
            row = c.execute("SELECT id, content_md FROM pages WHERE slug = ?", (slug,)).fetchone()
            if row is None:
                cur = c.execute(
                    "INSERT INTO pages (slug, title, kind, summary, content_md, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (slug, title, kind, summary, content_md, ts, ts),
                )
                page_id = cur.lastrowid
                changed = True
            else:
                page_id = row["id"]
                changed = content_md != "" and content_md != row["content_md"]
                c.execute(
                    "UPDATE pages SET title = ?, kind = ?, summary = COALESCE(NULLIF(?, ''), summary),"
                    " content_md = COALESCE(NULLIF(?, ''), content_md), updated_at = ? WHERE id = ?",
                    (title, kind, summary, content_md, ts, page_id),
                )
            if changed:
                c.execute(
                    "INSERT INTO revisions (page_id, content_md, change_summary, source_kind, source_ref, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (page_id, content_md, change_summary, source_kind, source_ref, ts),
                )
            # Ledger row exists from birth — at strength 0. Filing is not knowing.
            c.execute("INSERT OR IGNORE INTO concepts (page_id) VALUES (?)", (page_id,))
        # Wikilinks inside the content are edges too (Karpathy red-links → stubs).
        for target in extract_wikilinks(content_md):
            if target != slug:
                self.add_link(slug, target, "related", now=now)
        return self.get_page(slug)  # type: ignore[return-value]

    def ensure_page(self, slug: str, title: str = "", now: datetime | None = None) -> int:
        """Create a stub page if missing; return its id. Never overwrites content."""
        slug = slugify(slug)
        with self._lock, self._conn as c:
            row = c.execute("SELECT id FROM pages WHERE slug = ?", (slug,)).fetchone()
            if row:
                return row["id"]
            ts = _now_iso(now)
            cur = c.execute(
                "INSERT INTO pages (slug, title, kind, summary, content_md, created_at, updated_at)"
                " VALUES (?, ?, 'concept', '', '', ?, ?)",
                (slug, title or slug.replace("-", " ").title(), ts, ts),
            )
            c.execute("INSERT OR IGNORE INTO concepts (page_id) VALUES (?)", (cur.lastrowid,))
            return cur.lastrowid  # type: ignore[return-value]

    def get_page(self, slug: str) -> dict | None:
        slug = slugify(slug)
        with self._lock:
            row = self._conn.execute(
                "SELECT p.*, c.strength, c.last_retrieved, c.misconceptions, c.evidence"
                " FROM pages p LEFT JOIN concepts c ON c.page_id = p.id WHERE p.slug = ?",
                (slug,),
            ).fetchone()
            if row is None:
                return None
            page = dict(row)
            page["strength"] = float(page.get("strength") or 0.0)
            page["tier"] = tier(page["strength"])
            page["misconceptions"] = json.loads(page.get("misconceptions") or "[]")
            page["evidence"] = json.loads(page.get("evidence") or "[]")
            page["links"] = self._links_of(page["id"])
            page["backlinks"] = self._backlinks_of(page["id"])
            page["revisions"] = [
                dict(r)
                for r in self._conn.execute(
                    "SELECT change_summary, source_kind, source_ref, created_at FROM revisions"
                    " WHERE page_id = ? ORDER BY created_at DESC LIMIT 10",
                    (page["id"],),
                ).fetchall()
            ]
            return page

    def list_pages(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.id, p.slug, p.title, p.kind, p.summary, p.updated_at,"
                "       COALESCE(c.strength, 0) AS strength,"
                "       (SELECT COUNT(*) FROM cards k WHERE k.page_id = p.id AND k.suspended = 0"
                "          AND k.due <= ?) AS due_cards"
                " FROM pages p LEFT JOIN concepts c ON c.page_id = p.id"
                " ORDER BY p.updated_at DESC",
                (_now_iso(),),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tier"] = tier(float(d["strength"]))
            out.append(d)
        return out

    def _page_id(self, slug: str) -> int:
        with self._lock:
            row = self._conn.execute("SELECT id FROM pages WHERE slug = ?", (slugify(slug),)).fetchone()
        if row is None:
            raise KeyError(f"no page with slug {slug!r}")
        return row["id"]

    # ── links ────────────────────────────────────────────────────────────

    def add_link(self, from_slug: str, to_slug: str, rel: str = "related", now: datetime | None = None) -> None:
        if rel not in LINK_RELS:
            raise ValueError(f"rel must be one of {LINK_RELS}")
        a = self.ensure_page(from_slug, now=now)
        b = self.ensure_page(to_slug, now=now)
        if a == b:
            return
        with self._lock, self._conn as c:
            c.execute("INSERT OR IGNORE INTO links (from_page, to_page, rel) VALUES (?, ?, ?)", (a, b, rel))

    def _links_of(self, page_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT l.rel, p.slug, p.title FROM links l JOIN pages p ON p.id = l.to_page WHERE l.from_page = ?",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def all_links(self) -> list[dict]:
        """Every edge as {from_slug, to_slug, rel} — the knowledge-map's input."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT a.slug AS from_slug, b.slug AS to_slug, l.rel FROM links l"
                " JOIN pages a ON a.id = l.from_page JOIN pages b ON b.id = l.to_page"
                " ORDER BY a.slug, b.slug, l.rel"
            ).fetchall()
        return [dict(r) for r in rows]

    def _backlinks_of(self, page_id: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT l.rel, p.slug, p.title FROM links l JOIN pages p ON p.id = l.from_page WHERE l.to_page = ?",
            (page_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── learner ledger (THE single strength writer) ──────────────────────

    def record_retrieval(self, slug: str, outcome: str, note: str = "", now: datetime | None = None) -> dict:
        """Apply one retrieval event. This is the only method that moves strength."""
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        page_id = self.ensure_page(slug, now=now)
        ts = _now_iso(now)
        with self._lock, self._conn as c:
            row = c.execute("SELECT strength, evidence FROM concepts WHERE page_id = ?", (page_id,)).fetchone()
            s = float(row["strength"]) if row else 0.0
            evidence = json.loads(row["evidence"]) if row else []
            if outcome == "failure":
                s = s * _FAIL_KEEP
            else:
                s = s + (1.0 - s) * _GAIN[outcome]
            s = min(max(s, 0.0), 1.0)
            evidence.append({"at": ts, "outcome": outcome, "note": note[:500]})
            c.execute(
                "UPDATE concepts SET strength = ?, last_retrieved = ?, evidence = ? WHERE page_id = ?",
                (s, ts, json.dumps(evidence[-50:]), page_id),
            )
        return {"slug": slugify(slug), "strength": s, "tier": tier(s), "outcome": outcome}

    def add_misconception(self, slug: str, note: str, now: datetime | None = None) -> list[dict]:
        page_id = self.ensure_page(slug, now=now)
        with self._lock, self._conn as c:
            row = c.execute("SELECT misconceptions FROM concepts WHERE page_id = ?", (page_id,)).fetchone()
            items = json.loads(row["misconceptions"]) if row else []
            items.append({"note": note[:500], "status": "open", "noted_at": _now_iso(now)})
            c.execute("UPDATE concepts SET misconceptions = ? WHERE page_id = ?", (json.dumps(items), page_id))
        return items

    def resolve_misconception(self, slug: str, index: int, now: datetime | None = None) -> list[dict]:
        page_id = self._page_id(slug)
        with self._lock, self._conn as c:
            row = c.execute("SELECT misconceptions FROM concepts WHERE page_id = ?", (page_id,)).fetchone()
            items = json.loads(row["misconceptions"]) if row else []
            if not 0 <= index < len(items):
                raise IndexError(f"misconception index {index} out of range (0..{len(items) - 1})")
            items[index]["status"] = "resolved"
            items[index]["resolved_at"] = _now_iso(now)
            c.execute("UPDATE concepts SET misconceptions = ? WHERE page_id = ?", (json.dumps(items), page_id))
        return items

    def ledger(self) -> list[dict]:
        pages = self.list_pages()
        with self._lock:
            for p in pages:
                row = self._conn.execute(
                    "SELECT last_retrieved, misconceptions FROM concepts WHERE page_id = ?", (p["id"],)
                ).fetchone()
                p["last_retrieved"] = row["last_retrieved"] if row else None
                mis = json.loads(row["misconceptions"]) if row else []
                p["open_misconceptions"] = sum(1 for m in mis if m.get("status") == "open")
        return pages

    # ── cards + reviews ──────────────────────────────────────────────────

    def add_card(
        self, slug: str, prompt: str, answer: str = "", origin: str = "restatement", now: datetime | None = None
    ) -> dict:
        if origin not in CARD_ORIGINS:
            raise ValueError(f"origin must be one of {CARD_ORIGINS}")
        if not prompt.strip():
            raise ValueError("prompt must be non-empty")
        page_id = self.ensure_page(slug, now=now)
        ts = _now_iso(now)
        with self._lock, self._conn as c:
            cur = c.execute(
                "INSERT INTO cards (page_id, prompt, answer, origin, due, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (page_id, prompt, answer, origin, ts, ts),
            )
            return self._card(cur.lastrowid)

    def _card(self, card_id: int) -> dict:
        row = self._conn.execute(
            "SELECT k.*, p.slug FROM cards k JOIN pages p ON p.id = k.page_id WHERE k.id = ?", (card_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no card {card_id}")
        return dict(row)

    def due_cards(self, limit: int = 8, now: datetime | None = None) -> list[dict]:
        """Due cards interleaved round-robin across pages — never a same-topic block."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT k.*, p.slug FROM cards k JOIN pages p ON p.id = k.page_id"
                " WHERE k.suspended = 0 AND k.due <= ? ORDER BY k.due ASC",
                (_now_iso(now),),
            ).fetchall()
        by_page: dict[int, list[dict]] = {}
        order: list[int] = []
        for r in rows:
            d = dict(r)
            if d["page_id"] not in by_page:
                by_page[d["page_id"]] = []
                order.append(d["page_id"])
            by_page[d["page_id"]].append(d)
        out: list[dict] = []
        while len(out) < min(limit, len(rows)):
            for pid in order:
                if by_page[pid]:
                    out.append(by_page[pid].pop(0))
                    if len(out) >= limit:
                        break
        return out

    def due_count(self, now: datetime | None = None) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM cards WHERE suspended = 0 AND due <= ?", (_now_iso(now),)
            ).fetchone()
        return int(row["n"])

    def grade_card(
        self,
        card_id: int,
        rating: int,
        note: str = "",
        now: datetime | None = None,
        weights=None,
        desired_retention: float = 0.9,
    ) -> dict:
        """FSRS-update one card and route the outcome into concept strength."""
        with self._lock:
            card = self._card(card_id)
        updated = srs.review(card, rating, now=now, weights=weights, desired_retention=desired_retention)
        with self._lock, self._conn as c:
            c.execute(
                "UPDATE cards SET stability = ?, difficulty = ?, reps = ?, lapses = ?, state = ?,"
                " due = ?, last_review = ? WHERE id = ?",
                (
                    updated["stability"],
                    updated["difficulty"],
                    updated["reps"],
                    updated["lapses"],
                    updated["state"],
                    updated["due"],
                    updated["last_review"],
                    card_id,
                ),
            )
            c.execute(
                "INSERT INTO review_log (card_id, rating, reviewed_at, interval_days) VALUES (?, ?, ?, ?)",
                (card_id, rating, updated["last_review"], updated["interval_days"]),
            )
        outcome = "failure" if rating == srs.AGAIN else ("partial" if rating == srs.HARD else "success")
        ledger = self.record_retrieval(card["slug"], outcome, note=note or f"card #{card_id} rated {rating}", now=now)
        return {"card": {**card, **updated, "id": card_id}, "ledger": ledger}

    # ── export + stats ───────────────────────────────────────────────────

    def export_markdown(self, out_dir: str | Path) -> int:
        out = Path(out_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        n = 0
        for stub in self.list_pages():
            page = self.get_page(stub["slug"])
            if page is None:
                continue
            lines = [
                "---",
                f"title: {page['title']}",
                f"kind: {page['kind']}",
                f"strength: {round(page['strength'], 3)}",
                f"updated: {page['updated_at']}",
                "---",
                "",
                page["content_md"],
            ]
            (out / f"{page['slug']}.md").write_text("\n".join(lines), encoding="utf-8")
            n += 1
        return n

    # ── full-fidelity archive (migration between instances) ────────────────

    ARCHIVE_FORMAT = "learning-wiki-archive"
    ARCHIVE_VERSION = 1

    def export_archive(self, path: str | Path) -> dict:
        """Write EVERYTHING — pages, revisions, links, ledger, FSRS card state,
        review log — as one versioned, id-free JSON archive keyed by slug, so an
        import can rebuild it on any instance. This is the migration format;
        ``export_markdown`` stays the human-readable reading copy."""
        import json

        out = Path(path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            pages = [dict(r) for r in self._conn.execute("SELECT * FROM pages ORDER BY slug")]
            by_id = {p["id"]: p["slug"] for p in pages}
            doc_pages = []
            for pg in pages:
                pid = pg["id"]
                concept = self._conn.execute("SELECT * FROM concepts WHERE page_id = ?", (pid,)).fetchone()
                revisions = [
                    dict(r)
                    for r in self._conn.execute(
                        "SELECT content_md, change_summary, source_kind, source_ref, created_at "
                        "FROM revisions WHERE page_id = ? ORDER BY id",
                        (pid,),
                    )
                ]
                cards = []
                for c in self._conn.execute("SELECT * FROM cards WHERE page_id = ? ORDER BY id", (pid,)):
                    reviews = [
                        dict(r)
                        for r in self._conn.execute(
                            "SELECT rating, reviewed_at, interval_days FROM review_log WHERE card_id = ? ORDER BY id",
                            (c["id"],),
                        )
                    ]
                    card = {
                        k: c[k]
                        for k in (
                            "prompt",
                            "answer",
                            "origin",
                            "stability",
                            "difficulty",
                            "reps",
                            "lapses",
                            "state",
                            "due",
                            "last_review",
                            "suspended",
                            "created_at",
                        )
                    }
                    card["reviews"] = reviews
                    cards.append(card)
                doc_pages.append(
                    {
                        **{
                            k: pg[k]
                            for k in ("slug", "title", "kind", "summary", "content_md", "created_at", "updated_at")
                        },
                        "concept": (
                            {k: concept[k] for k in ("strength", "last_retrieved", "misconceptions", "evidence")}
                            if concept
                            else None
                        ),
                        "revisions": revisions,
                        "cards": cards,
                    }
                )
            links = [
                {"from": by_id[r["from_page"]], "to": by_id[r["to_page"]], "rel": r["rel"]}
                for r in self._conn.execute("SELECT * FROM links")
                if r["from_page"] in by_id and r["to_page"] in by_id
            ]
        doc = {
            "format": self.ARCHIVE_FORMAT,
            "version": self.ARCHIVE_VERSION,
            "exported_at": _now_iso(),
            "pages": doc_pages,
            "links": links,
        }
        out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        return {
            "path": str(out),
            "pages": len(doc_pages),
            "links": len(links),
            "cards": sum(len(p["cards"]) for p in doc_pages),
        }

    def import_archive(self, path: str | Path, mode: str = "merge") -> dict:
        """Restore an archive VERBATIM — raw inserts, never through
        ``record_retrieval``/``grade``, so imported strength and FSRS state land
        exactly as exported (the ledger invariant: only retrieval moves strength,
        and an import is not retrieval). ``mode="merge"`` inserts new slugs and
        SKIPS existing ones (reported); ``mode="replace"`` replaces any colliding
        page (cascade wipes its old revisions/cards/logs/links) with the archive's
        version. Other pages are never touched."""
        import json

        src = Path(path).expanduser()
        doc = json.loads(src.read_text(encoding="utf-8"))
        if doc.get("format") != self.ARCHIVE_FORMAT:
            raise ValueError(f"not a learning-wiki archive: {src}")
        if int(doc.get("version", 0)) > self.ARCHIVE_VERSION:
            raise ValueError(f"archive version {doc.get('version')} is newer than this plugin understands")
        if mode not in ("merge", "replace"):
            raise ValueError("mode must be 'merge' or 'replace'")

        imported, skipped = [], []
        with self._lock, self._conn:
            for pg in doc.get("pages", []):
                slug = str(pg.get("slug") or "").strip()
                if not slug:
                    continue
                existing = self._conn.execute("SELECT id FROM pages WHERE slug = ?", (slug,)).fetchone()
                if existing is not None:
                    if mode == "merge":
                        skipped.append(slug)
                        continue
                    self._conn.execute("DELETE FROM pages WHERE id = ?", (existing["id"],))
                cur = self._conn.execute(
                    "INSERT INTO pages (slug, title, kind, summary, content_md, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        slug,
                        pg.get("title") or slug,
                        pg.get("kind") or "concept",
                        pg.get("summary") or "",
                        pg.get("content_md") or "",
                        pg.get("created_at") or _now_iso(),
                        pg.get("updated_at") or _now_iso(),
                    ),
                )
                pid = cur.lastrowid
                c = pg.get("concept")
                if c:
                    self._conn.execute(
                        "INSERT INTO concepts (page_id, strength, last_retrieved, misconceptions, evidence) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            pid,
                            float(c.get("strength") or 0.0),
                            c.get("last_retrieved"),
                            c.get("misconceptions") or "[]",
                            c.get("evidence") or "[]",
                        ),
                    )
                for rv in pg.get("revisions", []):
                    self._conn.execute(
                        "INSERT INTO revisions (page_id, content_md, change_summary, source_kind, source_ref, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            pid,
                            rv.get("content_md") or "",
                            rv.get("change_summary") or "",
                            rv.get("source_kind") or "manual",
                            rv.get("source_ref") or "",
                            rv.get("created_at") or _now_iso(),
                        ),
                    )
                for cd in pg.get("cards", []):
                    ccur = self._conn.execute(
                        "INSERT INTO cards (page_id, prompt, answer, origin, stability, difficulty, reps, "
                        "lapses, state, due, last_review, suspended, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            pid,
                            cd.get("prompt") or "",
                            cd.get("answer") or "",
                            cd.get("origin") or "restatement",
                            float(cd.get("stability") or 0.0),
                            float(cd.get("difficulty") or 0.0),
                            int(cd.get("reps") or 0),
                            int(cd.get("lapses") or 0),
                            cd.get("state") or "new",
                            cd.get("due") or _now_iso(),
                            cd.get("last_review"),
                            int(cd.get("suspended") or 0),
                            cd.get("created_at") or _now_iso(),
                        ),
                    )
                    for rv in cd.get("reviews", []):
                        self._conn.execute(
                            "INSERT INTO review_log (card_id, rating, reviewed_at, interval_days) VALUES (?, ?, ?, ?)",
                            (
                                ccur.lastrowid,
                                int(rv.get("rating") or 0),
                                rv.get("reviewed_at") or _now_iso(),
                                float(rv.get("interval_days") or 0),
                            ),
                        )
                imported.append(slug)
            # Links resolve by slug AFTER all pages land (both endpoints must exist).
            linked = 0
            for ln in doc.get("links", []):
                a = self._conn.execute("SELECT id FROM pages WHERE slug = ?", (ln.get("from"),)).fetchone()
                b = self._conn.execute("SELECT id FROM pages WHERE slug = ?", (ln.get("to"),)).fetchone()
                if a and b:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO links (from_page, to_page, rel) VALUES (?, ?, ?)",
                        (a["id"], b["id"], ln.get("rel") or "related"),
                    )
                    linked += 1
        return {"imported": len(imported), "skipped": skipped, "links": linked, "mode": mode}

    def stats(self) -> dict:
        with self._lock:
            pages = self._conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()["n"]
            cards = self._conn.execute("SELECT COUNT(*) AS n FROM cards WHERE suspended = 0").fetchone()["n"]
            avg = self._conn.execute("SELECT COALESCE(AVG(strength), 0) AS s FROM concepts").fetchone()["s"]
        return {"pages": pages, "cards": cards, "due": self.due_count(), "avg_strength": round(float(avg), 3)}
