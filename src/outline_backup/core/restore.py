"""Break-glass restore: rebuild MarkdownZips from a backup tree and import them."""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field

import httpx

from outline_backup.core.attachments import attachment_path, find_attachment_ids, rewrite_for_zip
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
    collection_files: dict[str, int] = field(default_factory=dict)


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


def _zip_members(manifest: Manifest, collection_id: str) -> dict[str, str]:
    """Map doc_id -> zip member path, disambiguating filename collisions via slug.

    Builds paths the same way `_zip_member` does, but tracks which paths have
    already been used. On a collision the filename stem gets the doc's manifest
    slug appended in parens, e.g. `Guides/Untitled (untitled-Ab9).md`.
    """
    col = manifest.collections[collection_id]
    entries = [
        (doc_id, entry)
        for doc_id, entry in manifest.documents.items()
        if entry["collectionId"] == collection_id
    ]
    entries.sort(key=lambda item: item[1]["path"])

    used: set[str] = set()
    members: dict[str, str] = {}
    for doc_id, entry in entries:
        path = _zip_member(manifest, entry, col["name"])
        if path in used:
            directory, _, filename = path.rpartition("/")
            stem = filename.removesuffix(".md")
            disambiguated = f"{stem} ({entry.get('slug', '')}).md"
            path = f"{directory}/{disambiguated}" if directory else disambiguated
        used.add(path)
        members[doc_id] = path
    return members


def build_markdown_zip(
    manifest: Manifest, read_file, collection_id: str, tree: dict[str, str] | None = None
) -> bytes:
    members = _zip_members(manifest, collection_id)
    buf = io.BytesIO()
    bundled: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc_id, member_path in sorted(members.items(), key=lambda kv: kv[1]):
            markdown = read_file(manifest.documents[doc_id]["path"]).decode()
            zf.writestr(member_path, rewrite_for_zip(markdown))
            for att_id in find_attachment_ids(markdown):
                att_path = attachment_path(att_id)
                if att_path in bundled or tree is None or att_path not in tree:
                    continue
                bundled.add(att_path)
                zf.writestr(att_path, read_file(att_path))
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
    attempts = 60
    for i in range(attempts):
        op = client.file_operation(op_id)
        state = op.get("state")
        if state == "complete":
            return
        if state == "error":
            raise RestoreError(f"import failed: {op.get('error')}")
        if i < attempts - 1:
            time.sleep(2)
    raise RestoreError("import timed out")


def restore(
    client: OutlineClient,
    source: Destination,
    *,
    dry_run: bool = False,
    upload=None,
    collections: list[str] | None = None,
) -> RestoreReport:
    upload = upload or _default_upload
    report = RestoreReport()
    manifest = Manifest.from_bytes(source.read_file(MANIFEST_PATH))
    tree = source.list_tree()

    if collections is not None:
        known = {c["name"] for c in manifest.collections.values()}
        for name in collections:
            if name not in known:
                report.warnings.append(f"no such collection in backup: {name}")

    for col_id, col in manifest.collections.items():
        if collections is not None and col["name"] not in collections:
            continue
        report.collections += 1
        members = _zip_members(manifest, col_id)
        report.collection_files[col["name"]] = len(members)
        if dry_run:
            continue
        blob = build_markdown_zip(manifest, source.read_file, col_id, tree=tree)
        att = client.create_import_attachment(f"{col['slug']}.zip", len(blob))
        upload(att["uploadUrl"], att.get("form") or {}, blob)
        op = client.import_collection(att["attachment"]["id"])
        _wait_for_operation(client, op["fileOperation"]["id"])

        new_col = next((c for c in client.list_collections() if c["name"] == col["name"]), None)
        if new_col is None:
            report.warnings.append(f"imported collection not found by name: {col['name']}")
            continue
        imported_docs = client.list_documents(new_col["id"])
        by_title = {d["title"]: d["id"] for d in imported_docs}
        title_counts = Counter(d["title"] for d in imported_docs)

        for doc_id in members:
            entry = manifest.documents[doc_id]
            title = entry["title"]
            new_doc_id = by_title.get(title)
            if new_doc_id is None:
                report.warnings.append(f"no imported match for document: {title}")
                continue
            report.documents_matched += 1
            if title_counts[title] > 1:
                report.warnings.append(
                    f"ambiguous title match, skipping comment replay for: {title}"
                )
                continue
            report.comments_created += _replay_comments(client, source, entry, new_doc_id, report, tree)
    return report


def _replay_comments(
    client: OutlineClient,
    source: Destination,
    entry: dict,
    new_doc_id: str,
    report: RestoreReport,
    tree: dict[str, str],
) -> int:
    sidecar_path = sidecar_for(entry["path"])
    if sidecar_path not in tree:
        return 0
    comments = json.loads(source.read_file(sidecar_path))
    id_map: dict[str, str] = {}
    created = 0
    for c in sorted(comments, key=lambda c: (c.get("createdAt") or "", c["id"])):
        data = _prefix_comment(c.get("data") or {"type": "doc", "content": []},
                               c.get("authorName"), c.get("createdAt"))
        parent_comment_id = c.get("parentCommentId") or None
        parent = id_map.get(parent_comment_id) if parent_comment_id else None
        if parent_comment_id and parent is None:
            report.warnings.append(f"comment {c['id']}: parent missing, posted as top-level")
        try:
            new = client.create_comment(new_doc_id, data, parent_comment_id=parent)
            id_map[c["id"]] = new["id"]
            created += 1
        except Exception as exc:  # noqa: BLE001 — keep going: one bad comment shouldn't sink the restore
            report.warnings.append(f"comment {c['id']} failed: {exc}")
    return created
