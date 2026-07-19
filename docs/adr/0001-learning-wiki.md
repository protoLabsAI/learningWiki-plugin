# ADR 0001 — The Learning Wiki: two ledgers, one loop

- Status: accepted
- Date: 2026-07-19
- Deciders: Josh Mabry
- Tags: architecture, learning-science, plugin-design

## Context

Two inputs motivated this plugin:

1. **Karpathy's "LLM Wiki" pattern** (gist, April 2026): an agent maintains a
   persistent, interlinked wiki — ingest updates pages, queries file their
   syntheses back, periodic lint keeps it coherent — so knowledge compounds
   instead of being re-derived per query.
2. **The learning-science evidence base.** Explanation-on-demand produces fluent
   non-learning: unguarded AI answer-giving lifted practice scores 48% while
   cutting exam scores 17% *below never having AI* (Bastani et al., PNAS).
   What durably works: retrieval practice (g ≈ 0.50, Rowland 2014), spacing
   (optimal gap ≈ 10–20% of the retention interval; Cepeda et al. 2006),
   attempt-before-instruction (Sinha & Kapur 2021), expertise-adapted guidance
   (worked examples for novices, faded guidance later — Sweller/Kalyuga), and
   teach-back (protégé effect, Chase et al. 2009). Known LLM-tutor failure
   modes: sycophantic validation of misconceptions (~1 pressured turn in 7)
   and multi-rule prompt decay over long chats.

Synthesis: Karpathy's wiki models the **content**; learning needs a second
ledger modeling the **learner**. Every surface is a render of
`f(content model, learner model)`; every interaction updates both.

### Why a protoAgent plugin (and not a standalone app)

A rabbit-hole.io pivot was scoped first (its PR was closed in favor of this).
protoAgent already has what the retention loop needs — a scheduler, an agent
runtime with skills/tools, an event bus, console rail views, mobile chat, and
multi-surface delivery (A2A/OpenAI/MCP) — while a standalone app would rebuild
all of that around a search engine. The two products compose instead:
rabbit-hole stays the self-hostable acquisition backend (`rh` CLI), this plugin
owns the wiki + ledger + review loop. The plugin is also deliberately a
**first-class example** of the plugin SDK: tools + skill + surface + view +
instance-scoped storage in one package.

## Decision

1. **Wiki layer (content model)** — pages in instance-scoped SQLite with typed
   edges (`related | prerequisite | part-of | contrast`); every content change
   records a revision with provenance (`change_summary`, `source_kind`,
   `source_ref`). `[[wikilinks]]` in content auto-materialize as edges with
   stub pages (red links → stubs).
2. **Learner ledger** — per-concept `strength ∈ [0,1]` (tiers: novice < 0.3,
   frontier ≤ 0.7, fluent), misconception log with open/resolved lifecycle,
   evidence trail. **The invariant: only `record_retrieval` moves strength**
   (card grading routes through it). Success gains are asymptotic
   (`s += (1-s)·0.35`; partial 0.15), failure is multiplicative (`s·0.6`).
3. **Review loop** — cards born from session events (miss / corrected
   misconception / restatement / transfer), scheduled by a pure-Python
   **FSRS-4.5** implementation (published default weights, config-overridable;
   `again` returns in ~10 minutes, success grows the interval toward the
   `desired_retention` target). Due cards are served interleaved round-robin
   across concepts, never topic-blocked.
4. **Tutor policy as a skill, not a prompt** — `skills/learning-tutor/SKILL.md`
   encodes probe-first diagnosis, tier-matched scaffolding, answer-holding,
   correction duty, honest grading, and end-of-session filing. Skill-level
   placement survives long chats where pasted mega-prompts decay.
5. **Push half optional** — a lifecycle-managed surface emits
   `learning_wiki.reviews_due` on the bus every `nudge_interval_hours` (0 =
   inert; the lazy pull path via `review_next` always works).

### Contested forks

| Fork | Decision | Rejected |
| --- | --- | --- |
| Platform | protoAgent plugin | rabbit-hole.io pivot (would rebuild scheduler/runtime/mobile around a search app) |
| Storage | instance-scoped SQLite, markdown via `wiki_export` | files-as-truth (sync bugs), host knowledge-store coupling (keep the plugin self-contained) |
| Scheduler math | FSRS-4.5 ported in-plugin, zero deps | `fsrs` pip dep (requires_pip friction), hand-rolled SM-2 (worse, and scheduling is solved) |
| Graph | SQLite adjacency with typed rels | any graph database (concept graphs are tiny) |
| Tutor placement | plugin skill + tool discipline | host middleware (core edit — plugins never edit core), per-chat prompts (decay) |

## Consequences

- Strength is trustworthy *because* it is hard to move: no code path outside
  retrieval events can touch it, and tests pin that.
- Cards accumulate only from real sessions, so the deck stays clearable —
  bulk page→card conversion is deliberately not offered.
- The rating scale's honesty matters more than its resolution; the skill
  instructs the agent to never inflate ratings (miscalibration poisons FSRS).
- Phase 2 candidates, in evidence order: lint pass as a scheduled curation
  subagent (contradictions/orphans/stale strengths), teach-back mode with a
  tutee subagent, `rh` deep-research auto-filing, prerequisite-aware "what
  should I learn next" from the edge graph.

## References

Karpathy LLM-wiki gist · Bastani et al. PNAS 2025 (crutch effect) · Dunlosky
et al. 2013 (technique utilities) · Rowland 2014 (retrieval g≈0.50) · Cepeda
et al. 2006/2008 (spacing) · Sinha & Kapur 2021 (productive failure) · Kalyuga
2007 (expertise reversal) · Chase et al. 2009 (protégé effect) · FSRS-4.5:
open-spaced-repetition/awesome-fsrs wiki (algorithm + default weights).
