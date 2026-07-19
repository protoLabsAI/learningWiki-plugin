# learning-wiki (protoAgent plugin)

An **adaptive learning wiki**: the agent maintains a persistent, interlinked wiki
of concept pages — Karpathy's LLM-wiki pattern — *plus* the layer that pattern is
missing for learning: a **learner ledger** (per-concept strength, misconception
log, evidence trail) and **FSRS-scheduled retrieval cards**. Every page render and
tutoring move is a function of both models; the wiki compounds with understanding,
not just with reading.

The load-bearing invariant, enforced in the store and tested: **only retrieval
events move strength.** Reading, filing, and hearing a great explanation never do
— that's the fluency illusion, and it's designed out. Design + evidence base:
[ADR 0001](./docs/adr/0001-learning-wiki.md).

It is also a **plugin-SDK showcase**: every seam it uses (and every seam it
deliberately declines) is documented in [SEAMS.md](./SEAMS.md).

## What it contributes

- **12 tools** — `wiki_index` / `wiki_page` / `wiki_file` / `wiki_link` (content
  model, typed edges incl. `prerequisite`), `ledger_status` / `ledger_record` /
  `ledger_misconception` (learner model), `card_add` / `review_next` /
  `review_grade` (FSRS review loop), `wiki_export` (markdown dump),
  `wiki_research` (optional [rabbit-hole](https://github.com/protoLabsAI/rabbit-hole.io)
  `rh` CLI acquisition, off by default).
- **`learning-tutor` skill** — the evidence-based session policy: reviews first,
  probe-first diagnosis (never trust self-report), tier-matched scaffolding
  (novice → worked examples, frontier → one-idea-per-tier generation, fluent →
  teach-back), answer-holding, plain-spoken correction, end-of-session filing +
  card generation *from the session*.
- **Two subagents** — `review-coach` (one honest session over the due cards;
  scheduled via `review_cron`) and `wiki-lint` (read-only curation pass:
  orphans, stale strengths, frontier inconsistencies; `lint_cron`). Both ride
  plugin-owned cron jobs that are swept automatically on disable.
- **Chat commands** — `/review` (what's due), `/wiki [topic]` (page peek /
  frontier), and `/learn <topic> [target]` — arms a **self-driving learning
  loop**: a study cadence plus a ledger-verified watch
  (`learning_wiki:strength`) that cancels the cadence when you actually reach
  the target. Goal verifiers also work with `/goal`.
- **Console view** — a "Wiki" rail icon: page list with tier dots + due badges,
  page reader with `[[wikilink]]` navigation, links/backlinks. Any
  `learning_wiki.*` event lights its notification dot.
- **Review-nudge surface + wake hook** — inert by default; with
  `nudge_interval_hours > 0` it emits `learning_wiki.reviews_due` (typed
  contract in the manifest) when cards are due, and a desktop wake triggers a
  due check via the lifecycle-hook seam.

Storage is instance-scoped SQLite (`instance_paths().store("learning_wiki")`),
no runtime pip deps; the FSRS-4.5 scheduler is pure Python with the published
default weights (overridable via config).

## Install & enable

```bash
protoagent plugin install https://github.com/protoLabsAI/learningWiki-plugin
```

Then enable it (install ≠ enable ≠ trust — it ships disabled):

```yaml
# langgraph-config.yaml
plugins:
  enabled: [learning_wiki]

# optional overrides
learning_wiki:
  desired_retention: 0.9    # FSRS target recall at review time
  nudge_interval_hours: 24  # push "reviews due" onto the event bus daily
  rh_enabled: false         # opt-in web research via the rh CLI
```

Say "teach me X" / "quiz me" / "let's review" — the `learning-tutor` skill picks
it up. The wiki grows as you learn; reviews surface on the schedule the evidence
says works (spacing ≈ 10–20% of the retention interval, handled by FSRS).

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt ruff
.venv/bin/pytest -q          # host-free: no protoAgent checkout needed
.venv/bin/ruff check .
```

## License

Apache-2.0
