"""Review-nudge surface: a lifecycle-managed background loop that checks for
due cards every `nudge_interval_hours` and emits `reviews_due` on the plugin
event bus (namespaced by the host to `learning_wiki.reviews_due`). Interval 0
(the default) leaves the loop inert — spaced review still works lazily via
`review_next`; the nudge is the push half."""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("protoagent.plugins.learning_wiki")


class ReviewNudge:
    def __init__(self, cfg: dict, get_store, emitter=None):
        self.interval_s = float(cfg.get("nudge_interval_hours") or 0) * 3600.0
        self._get_store = get_store
        self._emit = emitter
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check_once(self) -> int:
        due = self._get_store().due_count()
        try:
            from graph.sdk import record_metric  # host-only, lazy; best-effort

            record_metric("due_cards", float(due), plugin_id="learning_wiki")
        except Exception:  # noqa: BLE001 — metrics never gate the nudge
            pass
        if due > 0 and self._emit is not None:
            try:
                self._emit("reviews_due", {"due": due})
            except Exception:  # noqa: BLE001
                log.exception("[learning_wiki] emitting reviews_due failed")
        return due

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                due = self.check_once()
                if due:
                    log.info("[learning_wiki] %d review card(s) due", due)
            except Exception:  # noqa: BLE001
                log.exception("[learning_wiki] nudge check failed")

    def start(self) -> None:
        if self.interval_s <= 0:
            log.info("[learning_wiki] nudge surface inert (nudge_interval_hours=0)")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="learning-wiki-nudge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
