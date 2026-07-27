"""Core sync: Outline documents/comments -> destination tree + manifest."""

from __future__ import annotations

import logging
import time

from outline_backup.core.manifest import MANIFEST_PATH, Manifest, sidecar_for
from outline_backup.core.outline_client import OutlineClient
from outline_backup.core.serialize import comments_sidecar, doc_slug, slugify
from outline_backup.destinations.base import Destination, git_blob_sha

logger = logging.getLogger("outline_backup.sync")

# Module-level indirection so tests can monkeypatch sleeping without real delays.
_sleep = time.sleep

# Safety cap on ancestor-chain walking: guards against cycles/pathological depth.
MAX_ANCESTOR_DEPTH = 50


class SyncEngine:
    def __init__(self, client: OutlineClient, dest: Destination, pace_seconds: float = 0.0):
        self.client = client
        self.dest = dest
        self.pace_seconds = pace_seconds

    # -- helpers ----------------------------------------------------------
    def _load_manifest(self, tree: dict[str, str]) -> Manifest:
        if MANIFEST_PATH in tree:
            return Manifest.from_bytes(self.dest.read_file(MANIFEST_PATH))
        return Manifest()

    def _collection_slug(self, manifest: Manifest, collection_id: str, col: dict | None = None) -> str:
        entry = manifest.collections.get(collection_id)
        if entry:
            return entry["slug"]
        if col is None:
            col = self.client.collection_info(collection_id)
        slug = f"{slugify(col.get('name', '')) or 'collection'}-{col.get('urlId', collection_id[:8])}"
        manifest.set_collection(collection_id, name=col.get("name", ""), slug=slug)
        return slug

    def _doc_path(self, manifest: Manifest, doc: dict, _resolving: frozenset[str] = frozenset()) -> str:
        col_slug = self._collection_slug(manifest, doc["collectionId"])
        parts: list[str] = []
        visited: set[str] = set(_resolving) | {doc.get("id", "")}
        parent_id = doc.get("parentDocumentId")
        while parent_id:
            if parent_id in visited or len(visited) >= MAX_ANCESTOR_DEPTH:
                logger.warning(
                    "Cycle or excessive ancestor depth detected resolving document %s "
                    "(stuck at parent %s); placing it directly under collection %s",
                    doc.get("id"), parent_id, col_slug,
                )
                parts = []
                break
            visited.add(parent_id)
            parent = manifest.documents.get(parent_id)
            if parent is None:
                info = self.client.document_info(parent_id)
                self._upsert_document(manifest, info, _resolving=frozenset(visited))  # recursively places ancestors
                parent = manifest.documents.get(parent_id)
                if parent is None:
                    # Ancestor's own cycle guard prevented placement; stop here too.
                    logger.warning(
                        "Could not resolve ancestor %s for document %s; placing it directly "
                        "under collection %s", parent_id, doc.get("id"), col_slug,
                    )
                    parts = []
                    break
            parts.insert(0, parent["slug"])
            parent_id = parent.get("parentDocumentId")
        slug = doc_slug(doc.get("title", ""), doc["urlId"])
        return "/".join(["collections", col_slug, *parts, f"{slug}.md"])

    def _upsert_document(self, manifest: Manifest, doc: dict, _resolving: frozenset[str] = frozenset()) -> str:
        path = self._doc_path(manifest, doc, _resolving)
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

    def _stale_for(
        self, tree: dict[str, str], old_path: str | None, new_path: str, files: dict[str, bytes]
    ) -> list[str]:
        """Paths in the destination tree that are no longer valid for this doc."""
        stale: list[str] = []
        if old_path and old_path != new_path:
            stale.extend(p for p in (old_path, sidecar_for(old_path)) if p in tree)
        sidecar_path = sidecar_for(new_path)
        if sidecar_path not in files and sidecar_path in tree and sidecar_path not in stale:
            stale.append(sidecar_path)
        return stale

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
        # documents.info returns archived/trashed docs; mirroring one would
        # resurrect it after a missed or out-of-order archive/delete event.
        if doc.get("archivedAt") or doc.get("deletedAt"):
            return self.delete_document(
                doc_id, message or f'backup: remove archived "{doc.get("title", doc_id)}"'
            )
        tree = self.dest.list_tree()
        manifest = self._load_manifest(tree)
        old_path = manifest.path_for(doc_id)
        path = self._upsert_document(manifest, doc)

        files = self._doc_files(doc_id, path)
        stale = self._stale_for(tree, old_path, path, files)

        changed = self._changed(files, tree)
        manifest_bytes = manifest.to_bytes()
        manifest_changed = tree.get(MANIFEST_PATH) != git_blob_sha(manifest_bytes)
        if not changed and not stale and not manifest_changed:
            return False
        changed[MANIFEST_PATH] = manifest_bytes
        msg = message or f'backup: sync "{doc.get("title", doc_id)}"'
        if stale:
            self.dest.delete_files(stale, msg)
        self.dest.write_files(changed, msg)
        return True

    def delete_document(self, doc_id: str, message: str | None = None) -> bool:
        tree = self.dest.list_tree()
        manifest = self._load_manifest(tree)
        existing_path = manifest.path_for(doc_id)
        if existing_path is None:
            return False
        paths = [p for p in manifest.remove_document(doc_id) if p in tree]
        msg = message or f"backup: delete document {doc_id}"
        if paths:
            self.dest.delete_files(paths, msg)
        self.dest.write_files({MANIFEST_PATH: manifest.to_bytes()}, msg)
        return True

    def sync_all(self, message: str = "backup: full sync") -> int:
        tree = self.dest.list_tree()
        manifest = self._load_manifest(tree)
        pending: dict[str, bytes] = {}
        stale: list[str] = []
        for col in self.client.list_collections():
            self._collection_slug(manifest, col["id"], col)
            for doc in self.client.list_documents(col["id"]):
                old_path = manifest.path_for(doc["id"])
                path = self._upsert_document(manifest, doc)
                files = self._doc_files(doc["id"], path)
                stale.extend(self._stale_for(tree, old_path, path, files))
                pending.update(files)
                _sleep(self.pace_seconds)
        stale = list(dict.fromkeys(stale))
        changed = self._changed(pending, tree)
        manifest_bytes = manifest.to_bytes()
        manifest_changed = tree.get(MANIFEST_PATH) != git_blob_sha(manifest_bytes)
        if not changed and not stale and not manifest_changed:
            return 0
        changed[MANIFEST_PATH] = manifest_bytes
        if stale:
            self.dest.delete_files(stale, message)
        self.dest.write_files(changed, message)
        return len(changed)
