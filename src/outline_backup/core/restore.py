"""Break-glass restore: rebuild MarkdownZips from a backup tree and import them."""

from __future__ import annotations

import io
import json
import time
import zipfile
from dataclasses import dataclass, field

import httpx

from outline_backup.core.manifest import MANIFEST_PATH, Manifest, sidecar_for
from outline_backup.core.outline_client import OutlineClient
from outline_backup.destinations.base import Destination


class RestoreError(Exception):
    pass


@dataclass
class RestoreReport:
    collections: int = 0
    documents_matched: int = 0
    comments_created: int = 0
    warnings: list[str] = field(default_factory=list)


def _zip_member(manifest: Manifest, entry: dict, col_name: str) -> str:
    """Map a manifest doc entry to `<Collection Name>/(parent titles…)/<Title>.md`."""
    titles: list[str] = []
    parent_id = entry.get("parentDocumentId")
    while parent_id:
        parent = manifest.documents.get(parent_id)
        if parent is None:
            break
        titles.insert(0, parent["title"].replace("/", "-") or "Untitled")
        parent_id = parent.get("parentDocumentId")
    title = entry["title"].replace("/", "-") or "Untitled"
    return "/".join([col_name, *titles, f"{title}.md"])


def build_markdown_zip(manifest: Manifest, read_file, collection_id: str) -> bytes:
    col = manifest.collections[collection_id]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in sorted(manifest.documents.values(), key=lambda e: e["path"]):
            if entry["collectionId"] != collection_id:
                continue
            zf.writestr(_zip_member(manifest, entry, col["name"]), read_file(entry["path"]))
    return buf.getvalue()


def _default_upload(url: str, form: dict, content: bytes) -> None:
    resp = httpx.post(url, data=form, files={"file": ("import.zip", content, "application/zip")}, timeout=120)
    if resp.status_code not in (200, 201, 204):
        raise RestoreError(f"upload failed: HTTP {resp.status_code}")


def _prefix_comment(data: dict, author: str | None, created_at: str | None) -> dict:
    prefix = f"(originally by {author or 'unknown'}, {created_at or 'unknown date'}) "
    content = data.get("content") or []
    if content and content[0].get("content"):
        first = content[0]["content"][0]
        if first.get("type") == "text":
            first["text"] = prefix + first.get("text", "")
            return data
    content.insert(0, {"type": "paragraph", "content": [{"type": "text", "text": prefix.strip()}]})
    data["content"] = content
    return data


def _wait_for_operation(client: OutlineClient, op_id: str) -> None:
    for _ in range(60):
        op = client.file_operation(op_id)
        if op.get("state") == "complete":
            return
        if op.get("state") == "error":
            raise RestoreError(f"import failed: {op.get('error')}")
        time.sleep(2)
    raise RestoreError("import timed out")


def restore(
    client: OutlineClient,
    source: Destination,
    *,
    dry_run: bool = False,
    upload=None,
) -> RestoreReport:
    upload = upload or _default_upload
    report = RestoreReport()
    manifest = Manifest.from_bytes(source.read_file(MANIFEST_PATH))

    for col_id, col in manifest.collections.items():
        report.collections += 1
        if dry_run:
            continue
        blob = build_markdown_zip(manifest, source.read_file, col_id)
        att = client.create_import_attachment(f"{col['slug']}.zip", len(blob))
        upload(att["uploadUrl"], att.get("form") or {}, blob)
        op = client.import_collection(att["attachment"]["id"])
        _wait_for_operation(client, op["fileOperation"]["id"])

        new_col = next((c for c in client.list_collections() if c["name"] == col["name"]), None)
        if new_col is None:
            report.warnings.append(f"imported collection not found by name: {col['name']}")
            continue
        by_title = {d["title"]: d["id"] for d in client.list_documents(new_col["id"])}

        for doc_id, entry in manifest.documents.items():
            if entry["collectionId"] != col_id:
                continue
            new_doc_id = by_title.get(entry["title"])
            if new_doc_id is None:
                report.warnings.append(f"no imported match for document: {entry['title']}")
                continue
            report.documents_matched += 1
            report.comments_created += _replay_comments(client, source, entry, new_doc_id, report)
    return report


def _replay_comments(
    client: OutlineClient, source: Destination, entry: dict, new_doc_id: str, report: RestoreReport
) -> int:
    tree = source.list_tree()
    sidecar_path = sidecar_for(entry["path"])
    if sidecar_path not in tree:
        return 0
    comments = json.loads(source.read_file(sidecar_path))
    id_map: dict[str, str] = {}
    created = 0
    for c in sorted(comments, key=lambda c: (c.get("createdAt") or "", c["id"])):
        data = _prefix_comment(c.get("data") or {"type": "doc", "content": []},
                               c.get("authorName"), c.get("createdAt"))
        parent = id_map.get(c.get("parentCommentId") or "")
        try:
            new = client.create_comment(new_doc_id, data, parent_comment_id=parent)
            id_map[c["id"]] = new["id"]
            created += 1
        except Exception as exc:  # keep going: one bad comment shouldn't sink the restore
            report.warnings.append(f"comment {c['id']} failed: {exc}")
    return created
