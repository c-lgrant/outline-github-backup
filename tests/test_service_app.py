import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from outline_backup.core.config import Settings
from outline_backup.service.app import create_app

SECRET = "whsec_test"


class FakeWorker:
    def __init__(self):
        self.events = []
        self.full_syncs = []

    async def handle_event(self, event):
        self.events.append(event)

    async def run_full_sync(self, message):
        self.full_syncs.append(message)

    async def drain(self):
        pass


def signed(body: bytes) -> dict:
    ts = int(time.time() * 1000)
    sig = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"Outline-Signature": f"t={ts},s={sig}"}


def make_client() -> tuple[TestClient, FakeWorker]:
    worker = FakeWorker()
    app = create_app(Settings(outline_webhook_secret=SECRET), worker=worker)
    return TestClient(app), worker


def test_health():
    client, _ = make_client()
    assert client.get("/health").json() == {"status": "ok"}


def test_valid_event_enqueued():
    client, worker = make_client()
    body = json.dumps({"event": "documents.update", "payload": {"id": "d1", "model": {}}}).encode()
    resp = client.post("/webhook", content=body, headers=signed(body))
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert worker.events[0]["event"] == "documents.update"


def test_bad_signature_401():
    client, worker = make_client()
    body = b"{}"
    resp = client.post("/webhook", content=body, headers={"Outline-Signature": "t=1,s=bad"})
    assert resp.status_code == 401 and worker.events == []


def test_oversized_body_413():
    client, worker = make_client()
    body = b"x" * (1_048_576 + 1)
    resp = client.post("/webhook", content=body, headers=signed(body))
    assert resp.status_code == 413 and worker.events == []


def test_body_at_limit_still_processed():
    client, worker = make_client()
    payload = {"event": "documents.update", "payload": {"id": "d1", "model": {}}}
    body = json.dumps(payload).encode()
    body += b" " * (1_048_576 - len(body))  # trailing whitespace is valid JSON padding
    resp = client.post("/webhook", content=body, headers=signed(body))
    assert resp.status_code == 200 and worker.events[0]["event"] == "documents.update"


def test_bad_json_400():
    client, _ = make_client()
    body = b"not json"
    assert client.post("/webhook", content=body, headers=signed(body)).status_code == 400


def test_backfill_on_start_kicks_full_sync():
    worker = FakeWorker()
    app = create_app(Settings(outline_webhook_secret=SECRET, backfill_on_start=True), worker=worker)
    with TestClient(app):
        pass
    assert worker.full_syncs == ["backup: backfill on start"]


def test_backfill_on_start_defaults_off():
    client, worker = make_client()
    with client:
        pass
    assert worker.full_syncs == []


def test_startup_refuses_empty_webhook_secret():
    app = create_app(Settings())
    with pytest.raises(RuntimeError, match="OUTLINE_WEBHOOK_SECRET is required"), TestClient(app):
        pass
