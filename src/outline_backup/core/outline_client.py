"""Thin client for the Outline HTTP API (all endpoints are POST JSON)."""

from __future__ import annotations

import time

import httpx

# Module-level indirection so tests can monkeypatch sleeping without real delays.
_sleep = time.sleep

MAX_429_RETRIES = 5
MAX_RETRY_AFTER_SECONDS = 60
# Exponential backoff used when the server doesn't send a Retry-After header.
_BACKOFF_SECONDS = [2, 4, 8, 16, 32]


class OutlineError(Exception):
    pass


class OutlineClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        http: httpx.Client | None = None,
        max_429_retries: int = MAX_429_RETRIES,
        max_retry_after_seconds: float = MAX_RETRY_AFTER_SECONDS,
    ):
        self._http = http or httpx.Client(timeout=30.0)
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_token}"}
        self._max_429_retries = max_429_retries
        self._max_retry_after = max_retry_after_seconds

    def _post(self, endpoint: str, **payload) -> dict:
        resp = None
        # 1 initial attempt + up to max_429_retries retries, only for 429 responses.
        for attempt in range(self._max_429_retries + 1):
            resp = self._http.post(f"{self._base}/api/{endpoint}", json=payload, headers=self._headers)
            if resp.status_code != 429:
                break
            if attempt == self._max_429_retries:
                raise OutlineError(f"{endpoint} failed: HTTP 429 rate_limit_exceeded")
            backoff = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
            retry_after = resp.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    delay: float = min(float(retry_after), self._max_retry_after)
                except ValueError:
                    delay = backoff
            else:
                delay = backoff
            _sleep(delay)
        if resp.status_code != 200:
            raise OutlineError(f"{endpoint} failed: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def _paginate(self, endpoint: str, **payload) -> list[dict]:
        items: list[dict] = []
        offset = 0
        while True:
            batch = self._post(endpoint, limit=100, offset=offset, **payload).get("data", [])
            items.extend(batch)
            if len(batch) < 100:
                return items
            offset += 100

    # -- documents / collections / comments ------------------------------
    def document_info(self, doc_id: str) -> dict:
        return self._post("documents.info", id=doc_id)["data"]

    def document_deleted_upstream(self, doc_id: str) -> bool | None:
        """Definitive deletion verdict: True (gone/archived), False (alive), None (unknown).

        Used by prune, where absence from a listing is not proof of deletion —
        only a 404 or an archived/deleted flag on documents.info is.
        """
        resp = self._http.post(
            f"{self._base}/api/documents.info", json={"id": doc_id}, headers=self._headers
        )
        if resp.status_code == 404:
            return True
        if resp.status_code == 200:
            data = resp.json().get("data") or {}
            return bool(data.get("archivedAt") or data.get("deletedAt"))
        return None

    def document_export(self, doc_id: str) -> str:
        return self._post("documents.export", id=doc_id)["data"]

    def collection_info(self, collection_id: str) -> dict:
        return self._post("collections.info", id=collection_id)["data"]

    def list_collections(self) -> list[dict]:
        return self._paginate("collections.list")

    def list_documents(self, collection_id: str) -> list[dict]:
        return self._paginate("documents.list", collectionId=collection_id)

    def list_comments(self, document_id: str) -> list[dict]:
        return self._paginate("comments.list", documentId=document_id)

    def create_comment(self, document_id: str, data: dict, parent_comment_id: str | None = None) -> dict:
        payload: dict = {"documentId": document_id, "data": data}
        if parent_comment_id:
            payload["parentCommentId"] = parent_comment_id
        return self._post("comments.create", **payload)["data"]

    def download_attachment(self, attachment_id: str) -> bytes:
        # attachments.redirect 302s to a signed URL. httpx keeps the auth
        # header on same-origin redirects (local storage) and strips it on
        # cross-origin ones (S3-style), which is exactly what we want.
        resp = self._http.get(
            f"{self._base}/api/attachments.redirect",
            params={"id": attachment_id},
            headers=self._headers,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            raise OutlineError(f"attachments.redirect failed: HTTP {resp.status_code}")
        return resp.content

    # -- webhooks / import / file operations -----------------------------
    def create_webhook(self, name: str, url: str, secret: str, events: list[str]) -> dict:
        return self._post("webhookSubscriptions.create", name=name, url=url, secret=secret, events=events)["data"]

    def create_import_attachment(self, name: str, size: int) -> dict:
        return self._post(
            "attachments.create",
            name=name,
            size=size,
            contentType="application/zip",
            preset="workspaceImport",
        )["data"]

    def import_collection(self, attachment_id: str, format: str = "outline-markdown") -> dict:
        return self._post("collections.import", attachmentId=attachment_id, format=format)["data"]

    def file_operation(self, op_id: str) -> dict:
        return self._post("fileOperations.info", id=op_id)["data"]
