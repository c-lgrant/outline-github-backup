"""Thin client for the Outline HTTP API (all endpoints are POST JSON)."""

from __future__ import annotations

import httpx


class OutlineError(Exception):
    pass


class OutlineClient:
    def __init__(self, base_url: str, api_token: str, http: httpx.Client | None = None):
        self._http = http or httpx.Client(timeout=30.0)
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_token}"}

    def _post(self, endpoint: str, **payload) -> dict:
        resp = self._http.post(f"{self._base}/api/{endpoint}", json=payload, headers=self._headers)
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
