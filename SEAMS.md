# Plugin-SDK seam map

This plugin doubles as a **reference for the protoAgent plugin SDK**: it exercises
every seam the learning domain genuinely needs, and — just as deliberately —
documents the seams it declines and why. A showcase that uses every seam is a
worse teacher than one that explains its refusals. Ground truth for the seam
surface: `graph/plugins/registry.py` in the protoAgent repo.

## Seams exercised

| Seam | Where here | Why the domain wants it |
| --- | --- | --- |
| `register_tool` ×12 | `tools.py` | The only mutation path the model gets; the tool layer *is* the ledger discipline (only `ledger_record`/`review_grade` move strength). |
| `register_router` (both prefixes) | `api.py` | Public page at `/plugins/learning_wiki/view` (iframes can't carry a bearer), gated data at `/api/plugins/learning_wiki/*`. |
| `register_surface` | `nudge.py` | Lifecycle-managed due-card checker; inert at `nudge_interval_hours: 0` — the pull path (`review_next`) always works. |
| `register_skill_dir` | `skills/learning-tutor/` | The tutor *policy* lives in a skill, not a pasted prompt — skill-level placement survives the long chats where mega-prompts decay. |
| `register_subagent` ×2 | `subagents.py` | `review-coach` (runs one session, grades honestly) and `wiki-lint` (**read-only by tool allowlist** — curation can't edit). Trust boundaries expressed as tool lists. |
| `graph.sdk.schedule_recurring` | `__init__._arm_crons`, `/learn` | Plugin-owned crons (`plugin:learning_wiki:*`, swept on disable — #1642). Scheduled review + weekly lint = the dream/distill pattern (ADR 0054): a cron fires a normal agent turn; no new scheduling machinery. |
| `register_goal_verifier` ×2 | `goals.py` | Learning goals become ground-truthed conditions: `learning_wiki:strength` and `learning_wiki:reviews_clear` read the ledger, never the conversation. Usable from `/goal` and from watches. |
| `register_watch_hook` | `goals.py` | When a `/learn` watch trips (target strength reached), the hook cancels that topic's study cron and emits `goal_achieved` — the loop retires itself. |
| `graph.sdk.create_watch` | `commands.py` `/learn` | The self-driving loop: study cadence + a verifier-polled watch whose `run_prompt` fires the celebration/filing turn in the arming session. |
| `register_chat_command` ×3 | `commands.py` | `/review`, `/wiki`, `/learn` are **user-only** control actions the model cannot invoke — the right trust shape for peeking state and arming cadences (and the handler receives `session_id`, which tools don't get for free). |
| `register_lifecycle_hook` | `__init__.py` | `on_system_wake` → due check. Spaced repetition's most natural trigger is "the laptop opened in the morning" (ADR 0074). |
| `emit` + typed `emits:` | `nudge.py`, manifest | `reviews_due` / `goal_achieved` with payload schemas (#1636). Any `learning_wiki.*` topic also lights the Wiki rail icon's notification dot — free UI. |
| `graph.sdk.record_metric` | `nudge.py` | Plugin-owned metric series (`due_cards`) — strength-over-time charts later, zero storage code now. |
| Manifest `settings:` / `guide_url` / `capabilities` | manifest | Config renders in the console Settings dialog; setup guide links the README; capabilities declare network `[]` + scoped filesystem. |
| Manifest `views:` | manifest + `view.py` | Four-rules iframe rail view (slug-aware base, DS kit, gated data, no hand-rolled theming). |

## Seams deliberately skipped

| Seam | Why not here |
| --- | --- |
| `register_knowledge_store` | That seam **replaces the host's entire RAG backend**. The wiki is a knowledge *product*, not a retrieval backend; hijacking recall for every chat would be scope theft. |
| `register_embedder` | No embedding need — retrieval here is by slug/graph, not vectors. |
| `ChatAdapter` / `register_chat_surface` | We're not a message transport (that's Telegram/Discord/Slack's seam). |
| `slot: "chat"` view | Never hijack the chat panel for a side-surface; the tutor lives *in* chat already. |
| `register_late_tool_factory` | For meta-tools that must see the whole toolset (e.g. execute_code). No such need. |
| `register_thread_id_resolver` | Session mapping is the host's business; nothing here changes checkpointer identity. |
| `register_middleware` | Considered for frontier-injection before each model call; deferred — always-on context injection from an example plugin is invasive. If added, it ships config-gated **off**. |
| `builtin: true` | Reserved for core infra; a showcase must live and die by the normal trust model (`enabled: false`, operator opt-in). |
| `save_media` / `multimodal_tool_result` | Genuine fit (a tier-colored knowledge-map SVG the model can *see*) — queued for v0.3, not skipped. Same for `Knobs`, `register_a2a_skill`, a workflow recipe, and the `utility:` due-count pill. |

## Testing the seams host-free

Everything above is exercised with **no protoAgent checkout**: host-only imports
stay inside functions, and `tests/conftest.py` provides a `host_stub` fixture
that fakes `graph.sdk` / `graph.goals.types` / `graph.subagents.config` as pure
capture objects. `register()` degrades cleanly per seam (try/except per block,
`hasattr` guards for older hosts) — the pattern to copy for any standalone-repo
plugin.
