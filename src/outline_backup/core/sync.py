"""Core sync: Outline documents/comments -> destination tree + manifest."""

from __future__ import annotations

from outline_backup.core.manifest import MANIFEST_PATH, Manifest, sidecar_for
from outline_backup.core.outline_client import OutlineClient
from outline_backup.core.serialize import comments_sidecar, doc_slug, slugify
from outline_backup.destinations.base import Destination, git_blob_sha


class SyncEngine:
    def __init__(self, client: OutlineClient, dest: Destination):
        self.client = client
        self.dest = dest

    # -- helpers ----------------------------------------------------------
    def _load_manifest(self, tree: dict[str, str]) -> Manifest:
        if MANIFEST_PATH in tree:
            return Manifest.from_bytes(self.dest.read_file(MANIFEST_PATH))
        return Manifest()

    def _collection_slug(self, manifest: Manifest, collection_id: str) -> str:
        entry = manifest.collections.get(collection_id)
        if entry:
            return entry["slug"]
        col = self.client.collection_info(collection_id)
        slug = f"{slugify(col.get('name', '')) or 'collection'}-{col.get('urlId', collection_id[:8])}"
        manifest.set_collection(collection_id, name=col.get("name", ""), slug=slug)
        return slug

    def _doc_path(self, manifest: Manifest, doc: dict) -> str:
        col_slug = self._collection_slug(manifest, doc["collectionId"])
        parts: list[str] = []
        parent_id = doc.get("parentDocumentId")
        while parent_id:
            parent = manifest.documents.get(parent_id)
            if parent is None:
                info = self.client.document_info(parent_id)
                self._upsert_document(manifest, info)  # recursively places ancestors
                parent = manifest.documents[parent_id]
            parts.insert(0, parent["slug"])
            parent_id = parent.get("parentDocumentId")
        slug = doc_slug(doc.get("title", ""), doc["urlId"])
        return "/".join(["collections", col_slug, *parts, f"{slug}.md"])

    def _upsert_document(self, manifest: Manifest, doc: dict) -> str:
        path = self._doc_path(manifest, doc)
        manifest.set_document(
            doc["id"],
            title=doc.get("title", ""),
            slug=doc_slug(doc.get("title", ""), doc["urlId"]),
            path=path,
            collection_id=doc["collectionId"],
            parent_document_id=doc.get("parentDocumentId"),
            updated_at=doc.get("updatedAt"),
        )
        return path

    def _doc_files(self, doc_id: str, path: str) -> dict[str, bytes]:
        markdown = self.client.document_export(doc_id).encode()
        comments = self.client.list_comments(doc_id)
        files = {path: markdown}
        if comments:
            files[sidecar_for(path)] = comments_sidecar(comments)
        return files

    @staticmethod
    def _changed(files: dict[str, bytes], tree: dict[str, str]) -> dict[str, bytes]:
        return {p: data for p, data in files.items() if tree.get(p) != git_blob_sha(data)}

    # -- public API -------------------------------------------------------
    def sync_document(self, doc_id: str, message: str | None = None) -> bool:
        doc = self.client.document_info(doc_id)
        tree = self.dest.list_tree()
        manifest = self._load_manifest(tree)
        old_path = manifest.path_for(doc_id)
        path = self._upsert_document(manifest, doc)

        stale: list[str] = []
        if old_path and old_path != path:
            stale = [p for p in (old_path, sidecar_for(old_path)) if p in tree]

        files = self._doc_files(doc_id, path)
        changed = self._changed(files, tree)
        if not changed and not stale:
            return False
        changed[MANIFEST_PATH] = manifest.to_bytes()
        msg = message or f'backup: sync "{doc.get("title", doc_id)}"'
        if stale:
            self.dest.delete_files(stale, msg)
        self.dest.write_files(changed, msg)
        return True

    def delete_document(self, doc_id: str, message: str | None = None) -> bool:
        tree = self.dest.list_tree()
        manifest = self._load_manifest(tree)
        paths = [p for p in manifest.remove_document(doc_id) if p in tree]
        if not paths and manifest.path_for(doc_id) is None and MANIFEST_PATH not in tree:
            return False
        msg = message or f"backup: delete document {doc_id}"
        if paths:
            self.dest.delete_files(paths, msg)
        self.dest.write_files({MANIFEST_PATH: manifest.to_bytes()}, msg)
        return True

    def sync_all(self, message: str = "backup: full sync") -> int:
        tree = self.dest.list_tree()
        manifest = self._load_manifest(tree)
        pending: dict[str, bytes] = {}
        for col in self.client.list_collections():
            slug = f"{slugify(col.get('name', '')) or 'collection'}-{col.get('urlId', col['id'][:8])}"
            manifest.set_collection(col["id"], name=col.get("name", ""), slug=slug)
            for doc in self.client.list_documents(col["id"]):
                path = self._upsert_document(manifest, doc)
                pending.update(self._doc_files(doc["id"], path))
        changed = self._changed(pending, tree)
        if not changed:
            return 0
        changed[MANIFEST_PATH] = manifest.to_bytes()
        self.dest.write_files(changed, message)
        return len(changed)
