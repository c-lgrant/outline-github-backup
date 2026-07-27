import asyncio
import time
from unittest.mock import MagicMock

from outline_backup.service.worker import DebounceWorker


def ev(name: str, model_id: str, model: dict | None = None) -> dict:
    return {"event": name, "payload": {"id": model_id, "model": model or {}}}


async def test_debounce_collapses_bursts():
    engine = MagicMock()
    worker = DebounceWorker(engine, debounce_seconds=0.05)
    for _ in range(5):
        await worker.handle_event(ev("documents.update", "d1"))
    await asyncio.sleep(0.15)
    await worker.drain()
    engine.sync_document.assert_called_once()
    assert engine.sync_document.call_args.args[0] == "d1"


async def test_delete_is_immediate():
    engine = MagicMock()
    worker = DebounceWorker(engine, debounce_seconds=5.0)
    await worker.handle_event(ev("documents.delete", "d1"))
    await worker.drain()
    engine.delete_document.assert_called_once()


async def test_archive_cancels_pending_sync_for_same_document():
    # create → archive inside the debounce window: the immediate removal must
    # also cancel the queued sync, or the stale job re-mirrors the archived doc
    engine = MagicMock()
    worker = DebounceWorker(engine, debounce_seconds=0.05)
    await worker.handle_event(ev("documents.publish", "d1"))
    await worker.handle_event(ev("documents.archive", "d1"))
    await asyncio.sleep(0.15)
    await worker.drain()
    engine.delete_document.assert_called_once()
    engine.sync_document.assert_not_called()


async def test_delete_cancels_pending_sync_for_same_document():
    engine = MagicMock()
    worker = DebounceWorker(engine, debounce_seconds=0.05)
    await worker.handle_event(ev("documents.update", "d1"))
    await worker.handle_event(ev("documents.delete", "d1"))
    await asyncio.sleep(0.15)
    await worker.drain()
    engine.delete_document.assert_called_once()
    engine.sync_document.assert_not_called()


async def test_comment_event_targets_parent_document():
    engine = MagicMock()
    worker = DebounceWorker(engine, debounce_seconds=0.05)
    await worker.handle_event(ev("comments.create", "c1", {"documentId": "d9"}))
    await asyncio.sleep(0.15)
    await worker.drain()
    assert engine.sync_document.call_args.args[0] == "d9"


async def test_collection_event_schedules_full_sync():
    engine = MagicMock()
    worker = DebounceWorker(engine, debounce_seconds=0.05)
    await worker.handle_event(ev("collections.update", "col1"))
    await asyncio.sleep(0.15)
    await worker.drain()
    engine.sync_all.assert_called_once()


async def test_engine_errors_are_swallowed():
    engine = MagicMock()
    engine.sync_document.side_effect = RuntimeError("boom")
    worker = DebounceWorker(engine, debounce_seconds=0.01)
    await worker.handle_event(ev("documents.update", "d1"))
    await asyncio.sleep(0.05)
    await worker.drain()  # must not raise


async def test_jobs_for_different_documents_never_overlap():
    running = 0
    max_running = 0

    def sync_document(doc_id, message=None):
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        time.sleep(0.05)
        running -= 1

    engine = MagicMock()
    engine.sync_document.side_effect = sync_document
    worker = DebounceWorker(engine, debounce_seconds=0.01)
    await worker.handle_event(ev("documents.update", "d1"))
    await worker.handle_event(ev("documents.update", "d2"))
    await worker.handle_event(ev("documents.update", "d3"))
    await asyncio.sleep(0.05)
    await worker.drain()

    assert engine.sync_document.call_count == 3
    assert max_running == 1


async def test_finished_tasks_are_pruned():
    engine = MagicMock()
    worker = DebounceWorker(engine, debounce_seconds=0.01)
    await worker.handle_event(ev("documents.delete", "d1"))
    await worker.handle_event(ev("documents.update", "d2"))
    await asyncio.sleep(0.05)
    await worker.drain()

    await worker.handle_event(ev("documents.update", "d2"))

    assert len(worker._immediate) == 0
    assert worker._pending
    assert all(not t.done() for t in worker._pending.values())
