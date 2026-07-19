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

    def stats(self) -> dict:
        with self._lock:
            pages = self._conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()["n"]
            cards = self._conn.execute("SELECT COUNT(*) AS n FROM cards WHERE suspended = 0").fetchone()["n"]
            avg = self._conn.execute("SELECT COALESCE(AVG(strength), 0) AS s FROM concepts").fetchone()["s"]
        return {"pages": pages, "cards": cards, "due": self.due_count(), "avg_strength": round(float(avg), 3)}
