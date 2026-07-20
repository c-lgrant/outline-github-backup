import asyncio
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
