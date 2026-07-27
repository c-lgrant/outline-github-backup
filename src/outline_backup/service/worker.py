"""Per-document debounce queue between the webhook endpoint and the sync engine."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger("outline_backup.worker")

FULL_SYNC_KEY = "__all__"


class DebounceWorker:
    def __init__(self, engine, debounce_seconds: float):
        self.engine = engine
        self.debounce_seconds = debounce_seconds
        self._pending: dict[str, asyncio.Task] = {}
        self._immediate: list[asyncio.Task] = []
        # Sync jobs read-modify-write the shared manifest in the destination;
        # two running at once lose whichever update lands first.
        self._run_lock = asyncio.Lock()

    async def handle_event(self, event: dict) -> None:
        self._immediate = [t for t in self._immediate if not t.done()]
        self._pending = {k: t for k, t in self._pending.items() if not t.done()}

        name = event.get("event", "")
        payload = event.get("payload") or {}
        model_id = payload.get("id")
        model = payload.get("model") or {}

        if name in ("documents.delete", "documents.archive") and model_id:
            # A sync queued by an earlier create/update event would fire after
            # this removal and resurrect the doc in the mirror — cancel it.
            pending = self._pending.pop(model_id, None)
            if pending and not pending.done():
                pending.cancel()
            self._immediate.append(
                asyncio.create_task(self._run(self.engine.delete_document, model_id, f"backup: {name}"))
            )
        elif name.startswith("documents.") and model_id:
            self._schedule(model_id, lambda: self.engine.sync_document(model_id, f"backup: {name}"))
        elif name.startswith("comments.") and model.get("documentId"):
            doc_id = model["documentId"]
            self._schedule(doc_id, lambda: self.engine.sync_document(doc_id, f"backup: {name}"))
        elif name.startswith("collections."):
            self._schedule(FULL_SYNC_KEY, lambda: self.engine.sync_all(f"backup: {name}"))
        else:
            logger.info("ignoring event %s", name)

    def _schedule(self, key: str, job) -> None:
        existing = self._pending.get(key)
        if existing and not existing.done():
            existing.cancel()
        self._pending[key] = asyncio.create_task(self._debounced(job))

    async def _debounced(self, job) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
        except asyncio.CancelledError:
            return
        await self._run(job)

    async def _run(self, fn, *args) -> None:
        async with self._run_lock:
            try:
                await asyncio.to_thread(fn, *args)
            except Exception:
                logger.exception("sync job failed")

    async def drain(self) -> None:
        tasks = [t for t in [*self._pending.values(), *self._immediate] if not t.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
