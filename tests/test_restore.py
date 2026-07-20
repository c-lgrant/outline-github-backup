import io
import json
import zipfile
from pathlib import Path

import httpx
import respx

from outline_backup.core.manifest import Manifest
from outline_backup.core.outline_client import OutlineClient
from outline_backup.core.restore import build_markdown_zip, restore
from outline_backup.destinations.local import LocalDestination

BASE = "https://restore.example.com"


def seed_source(tmp_path: Path) -> LocalDestination:
    dest = LocalDestination(tmp_path)
    m = Manifest()
    m.set_collection("col1", name="Guides", slug="guides-Ab12")
    m.set_document("doc1", title="Intro", slug="intro-Xy9",
                   path="collections/guides-Ab12/intro-Xy9.md",
                   collection_id="col1", parent_document_id=None, updated_at=None)
    sidecar = [{"id": "c1", "createdAt": "2026-01-01T00:00:00Z", "parentCommentId": None,
                "authorName": "Alex",
                "text": "hi", "data": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "hi"}]}]}}]
    dest.write_files({
        "_manifest.json": m.to_bytes(),
        "collections/guides-Ab12/intro-Xy9.md": b"# Intro\n\nBody\n",
        "collections/guides-Ab12/intro-Xy9.comments.json": (json.dumps(sidecar) + "\n").encode(),
    }, "seed")
    return dest


def test_build_markdown_zip_structure(tmp_path: Path):
    dest = seed_source(tmp_path)
    manifest = Manifest.from_bytes(dest.read_file("_manifest.json"))
    blob = build_markdown_zip(manifest, dest.read_file, "col1")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.namelist() == ["Guides/Intro.md"]
        assert zf.read("Guides/Intro.md") == b"# Intro\n\nBody\n"


@respx.mock
def test_restore_imports_and_replays_comments(tmp_path: Path):
    dest = seed_source(tmp_path)
    respx.post(f"{BASE}/api/attachments.create").mock(return_value=httpx.Response(200, json={
        "data": {"attachment": {"id": "att1"}, "uploadUrl": "https://upload.example.com/u", "form": {}}
    }))
    respx.post(f"{BASE}/api/collections.import").mock(return_value=httpx.Response(200, json={
        "data": {"fileOperation": {"id": "op1", "state": "creating"}}
    }))
    respx.post(f"{BASE}/api/fileOperations.info").mock(return_value=httpx.Response(200, json={
        "data": {"id": "op1", "state": "complete"}
    }))
    respx.post(f"{BASE}/api/collections.list").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "newcol", "name": "Guides", "urlId": "Zz99"}]
    }))
    respx.post(f"{BASE}/api/documents.list").mock(return_value=httpx.Response(200, json={
        "data": [{"id": "newdoc", "title": "Intro", "urlId": "Qq11", "collectionId": "newcol"}]
    }))
    comment_route = respx.post(f"{BASE}/api/comments.create").mock(
        return_value=httpx.Response(200, json={"data": {"id": "newc1"}})
    )
    uploads: list[tuple[str, bytes]] = []
    report = restore(
        OutlineClient(BASE, "tok"), dest,
        upload=lambda url, form, content: uploads.append((url, content)),
    )
    assert report.collections == 1 and report.documents_matched == 1 and report.comments_created == 1
    assert uploads[0][0] == "https://upload.example.com/u"
    sent = json.loads(comment_route.calls.last.request.content)
    assert sent["documentId"] == "newdoc"
    first_text = sent["data"]["content"][0]["content"][0]["text"]
    assert first_text.startswith("(originally by Alex, 2026-01-01T00:00:00Z) ")


def test_dry_run_makes_no_calls(tmp_path: Path):
    dest = seed_source(tmp_path)
    report = restore(OutlineClient(BASE, "tok"), dest, dry_run=True)
    assert report.collections == 1 and report.comments_created == 0
