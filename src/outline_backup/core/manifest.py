from __future__ import annotations

import json

MANIFEST_PATH = "_manifest.json"


def sidecar_for(md_path: str) -> str:
    return md_path.removesuffix(".md") + ".comments.json"


class Manifest:
    def __init__(self) -> None:
        self.collections: dict[str, dict] = {}
        self.documents: dict[str, dict] = {}

    @classmethod
    def from_bytes(cls, raw: bytes) -> Manifest:
        data = json.loads(raw)
        m = cls()
        m.collections = data.get("collections", {})
        m.documents = data.get("documents", {})
        return m

    def to_bytes(self) -> bytes:
        data = {"version": 1, "collections": self.collections, "documents": self.documents}
        return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()

    def set_collection(self, collection_id: str, *, name: str, slug: str) -> None:
        self.collections[collection_id] = {"name": name, "slug": slug}

    def set_document(
        self,
        doc_id: str,
        *,
        title: str,
        slug: str,
        path: str,
        collection_id: str,
        parent_document_id: str | None,
        updated_at: str | None,
    ) -> None:
        self.documents[doc_id] = {
            "title": title,
            "slug": slug,
            "path": path,
            "collectionId": collection_id,
            "parentDocumentId": parent_document_id,
            "updatedAt": updated_at,
        }

    def path_for(self, doc_id: str) -> str | None:
        entry = self.documents.get(doc_id)
        return entry["path"] if entry else None

    def remove_document(self, doc_id: str) -> list[str]:
        entry = self.documents.pop(doc_id, None)
        if not entry:
            return []
        return [entry["path"], sidecar_for(entry["path"])]
