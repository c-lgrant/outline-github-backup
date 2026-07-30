"""FastAPI webhook receiver: verify, enqueue, ack fast."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from outline_backup.core.config import Settings, load_settings
from outline_backup.core.signature import SignatureError, verify_signature
from outline_backup.service.worker import DebounceWorker

logger = logging.getLogger("outline_backup.service")

MAX_BODY_BYTES = 1_048_576  # Outline events are small; anything larger is abuse


def create_app(settings: Settings | None = None, worker: DebounceWorker | None = None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.outline_webhook_secret:
            raise RuntimeError(
                "OUTLINE_WEBHOOK_SECRET is required — refusing to start unauthenticated"
            )
        if app.state.worker is None:
            from outline_backup.core.outline_client import OutlineClient
            from outline_backup.core.sync import SyncEngine
            from outline_backup.destinations import get_destination

            client = OutlineClient(
                settings.outline_url,
                settings.outline_api_token,
                max_429_retries=settings.max_429_retries,
                max_retry_after_seconds=settings.max_retry_after_seconds,
            )
            engine = SyncEngine(
                client,
                get_destination(settings),
                pace_seconds=settings.backfill_pace_seconds,
                include_attachments=settings.include_attachments,
                max_attachment_bytes=settings.max_attachment_bytes,
            )
            app.state.worker = DebounceWorker(engine, settings.debounce_seconds)
        backfill_task: asyncio.Task | None = None
        if settings.backfill_on_start:
            backfill_task = asyncio.create_task(
                app.state.worker.run_full_sync("backup: backfill on start")
            )
        yield
        if backfill_task is not None:
            await backfill_task
        await app.state.worker.drain()

    app = FastAPI(title="outline-github-backup", lifespan=lifespan)
    app.state.worker = worker

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook(request: Request) -> Response:
        chunks = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > MAX_BODY_BYTES:
                return Response(status_code=413)
            chunks.append(chunk)
        body = b"".join(chunks)
        try:
            verify_signature(
                request.headers.get("Outline-Signature"), body, settings.outline_webhook_secret
            )
        except SignatureError as exc:
            logger.warning("rejected webhook: %s", exc)
            return Response(status_code=401)
        try:
            event = json.loads(body)
        except json.JSONDecodeError:
            return Response(status_code=400)
        try:
            await request.app.state.worker.handle_event(event)
        except Exception:
            logger.exception("failed to enqueue event")  # still ack: retries + backfill heal
        return Response(content='{"ok": true}', media_type="application/json")

    return app


app = create_app()
