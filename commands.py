"""User-only /chat commands (ADR seam: register_chat_command).

Chat commands are deliberately NOT agent tools — the model cannot invoke them.
That makes them the right home for the two control actions here: peeking at
state (/wiki, /review) and arming the self-driving learning loop (/learn),
which needs the session_id the command handler receives. /learn prefers the
host's one-call `start_goal_loop` (protoAgent #2061) and composes the identical
loop from schedule_recurring + create_watch on hosts that predate it.
"""

from __future__ import annotations

import logging

from .goals import GOAL_WATCH_PREFIX, LOOP_PREFIX, STUDY_JOB_PREFIX
from .store import slugify, tier

log = logging.getLogger("protoagent.plugins.learning_wiki")

DEFAULT_TARGET = 0.75
WATCH_INTERVAL_S = 6 * 3600.0


def build_commands(cfg: dict, get_store):
    async def review_command(rest: str, session_id: str):
        """/review — what's due now, interleaved."""
        store = get_store()
        due = store.due_count()
        if due == 0:
            stats = store.stats()
            return f'Nothing due. {stats["pages"]} pages, avg strength {stats["avg_strength"]:.2f} — say "teach me <topic>" to grow the wiki.'
        cards = store.due_cards(limit=8)
        lines = [f"**{due} card(s) due.** First up ({min(due, 8)} shown, interleaved):"]
        lines += [f"- `{c['slug']}` · {c['origin']} · {c['prompt'][:80]}" for c in cards]
        lines.append('Say **"start my review"** and the coach will quiz you one card at a time.')
        return "\n".join(lines)

    async def wiki_command(rest: str, session_id: str):
        """/wiki [slug] — quick page peek, or the frontier when no slug given."""
        store = get_store()
        slug = slugify(rest.strip()) if rest.strip() else ""
        if not slug:
            pages = store.list_pages()[:12]
            if not pages:
                return "The wiki is empty — learn something and it will file itself."
            lines = ["**Frontier** (top 12 by recency):"]
            lines += [
                f"- `{p['slug']}` · {p['tier']} ({p['strength']:.2f})"
                + (f" · {p['due_cards']} due" if p["due_cards"] else "")
                for p in pages
            ]
            return "\n".join(lines)
        page = store.get_page(slug)
        if page is None:
            return f'No page `{slug}` yet — say "teach me {rest.strip()}" to create it.'
        mis = sum(1 for m in page["misconceptions"] if m.get("status") == "open")
        head = f"**{page['title']}** · {page['kind']} · {page['tier']} ({page['strength']:.2f})" + (
            f" · ⚠ {mis} open misconception(s)" if mis else ""
        )
        body = page["content_md"][:600] or "*stub — nothing filed yet*"
        links = ", ".join(f"`{l['slug']}` ({l['rel']})" for l in page["links"]) or "none"
        return f"{head}\n\n{body}\n\n**Links:** {links}"

    async def learn_command(rest: str, session_id: str):
        """/learn <topic> [target] — arm the self-driving loop. Prefers the host's
        one-call `start_goal_loop` (protoAgent #2061: watch + tick under a shared
        id, idempotent, rolled back together); on older hosts it composes the
        same loop by hand from schedule_recurring + create_watch."""
        parts = rest.strip().split()
        if not parts:
            return "Usage: `/learn <topic> [target-strength 0..1]` — e.g. `/learn simpsons paradox 0.8`"
        target = DEFAULT_TARGET
        if len(parts) > 1:
            try:
                maybe = float(parts[-1])
                if 0.0 < maybe <= 1.0:
                    target = maybe
                    parts = parts[:-1]
            except ValueError:
                pass
        topic = " ".join(parts)
        slug = slugify(topic)
        get_store().ensure_page(slug, title=topic.title())
        cron = str(cfg.get("study_cron") or "0 9 * * *")
        study_prompt = (
            f"Scheduled study tick for '{slug}': run a SHORT session per the learning-tutor "
            f"skill — reviews on this concept first, then one scaffolded tier. Target "
            f"strength {target}. Check ledger_status('{slug}') and stop early if already there."
        )
        done_prompt = (
            f"The learning goal on '{slug}' just PASSED verification (strength ≥ {target}). "
            f"Congratulate the learner concretely, file a short progress note on the page "
            f"(wiki_file, source_kind=chat), and suggest the natural next concept from its links."
        )
        try:
            try:
                from graph.sdk import start_goal_loop  # one-call loop, protoAgent #2061+
            except ImportError:
                start_goal_loop = None

            if start_goal_loop is not None:
                res = start_goal_loop(
                    goal=f"'{slug}' reaches strength {target}",
                    verifier="learning_wiki:strength",
                    verifier_args={"slug": slug, "min": target},
                    every=cron,
                    prompt=study_prompt,
                    plugin_id="learning_wiki",
                    loop_id=f"{LOOP_PREFIX}{slug}",
                    session_id=session_id or "",
                    done_prompt=done_prompt if session_id else "",
                    interval_s=WATCH_INTERVAL_S,
                )
                if not (res or {}).get("ok", False):
                    return f"Could not arm the loop: {(res or {}).get('message', 'unknown')}"
                armed_via = "host goal loop (`start_goal_loop`, one call — re-running `/learn` replaces it)"
            else:
                # Older host: compose the identical loop from the two primitives.
                from graph.sdk import create_watch, schedule_recurring  # host-only, lazy

                schedule_recurring(
                    study_prompt,
                    cron,
                    plugin_id="learning_wiki",
                    job_id=f"{STUDY_JOB_PREFIX}{slug}",
                    session=session_id or "",
                )
                watch = create_watch(
                    condition=f"'{slug}' reaches strength {target}",
                    verifier="learning_wiki:strength",
                    verifier_args={"slug": slug, "min": target},
                    watch_id=f"{GOAL_WATCH_PREFIX}{slug}",
                    interval_s=WATCH_INTERVAL_S,
                    run_prompt=done_prompt,
                    run_session=session_id or "",
                )
                if not (watch or {}).get("ok", True):
                    return (
                        f"Study cadence armed (`{cron}`), but the watch was refused: {watch.get('message', 'unknown')}."
                    )
                armed_via = "composed cron + watch (host predates `start_goal_loop`)"
        except ImportError:
            return "The goal loop needs the protoAgent host (graph.sdk) — running standalone?"
        except Exception as e:  # noqa: BLE001
            log.exception("[learning_wiki] /learn arming failed")
            return f"Could not arm the loop: {e}"
        current = get_store().get_page(slug)
        return (
            f"**Learning loop armed for `{slug}`** — now {tier(current['strength'])} ({current['strength']:.2f}), "
            f"target {target}.\n- Study tick: `{cron}` (cancels itself at target)\n"
            f"- Verifier: `learning_wiki:strength` checks the ledger every {int(WATCH_INTERVAL_S / 3600)}h\n"
            f"- Armed via: {armed_via}\n"
            f"Only real retrieval moves the number — the schedule does the remembering."
        )

    return {"review": review_command, "wiki": wiki_command, "learn": learn_command}
