import json
from pathlib import Path

import httpx
import respx

from outline_backup.core.manifest import MANIFEST_PATH
from outline_backup.core.outline_client import OutlineClient
from outline_backup.core.sync import SyncEngine
from outline_backup.destinations.local import LocalDestination

BASE = "https://wiki.example.com"

DOC = {
    "id": "doc1", "title": "Intro", "urlId": "Xy9", "collectionId": "col1",
    "parentDocumentId": None, "updatedAt": "2026-01-02T00:00:00Z",
}
COL = {"id": "col1", "name": "Guides", "urlId": "Ab12"}
COMMENTS = [{"id": "c1", "createdAt": "2026-01-01T00:00:00Z", "parentCommentId": None,
             "data": {"type": "doc", "content": [{"type": "paragraph",
                      "content": [{"type": "text", "text": "hi"}]}]}}]


def mock_doc_endpoints(doc=DOC, markdown="# Intro\n", comments=COMMENTS):
    respx.post(f"{BASE}/api/documents.info").mock(return_value=httpx.Response(200, json={"data": doc}))
    respx.post(f"{BASE}/api/documents.export").mock(return_value=httpx.Response(200, json={"data": markdown}))
    respx.post(f"{BASE}/api/collections.info").mock(return_value=httpx.Response(200, json={"data": COL}))
    respx.post(f"{BASE}/api/comments.list").mock(return_value=httpx.Response(200, json={"data": comments}))


def engine(tmp_path: Path) -> tuple[SyncEngine, LocalDestination]:
    dest = LocalDestination(tmp_path)
    return SyncEngine(OutlineClient(BASE, "tok"), dest), dest


@respx.mock
def test_sync_document_writes_md_sidecar_manifest(tmp_path: Path):
    eng, dest = engine(tmp_path)
    mock_doc_endpoints()
    assert eng.sync_document("doc1") is True
    tree = dest.list_tree()
    assert "collections/guides-Ab12/intro-Xy9.md" in tree
    assert "collections/guides-Ab12/intro-Xy9.comments.json" in tree
    manifest = json.loads(dest.read_file(MANIFEST_PATH))
    assert manifest["documents"]["doc1"]["path"] == "collections/guides-Ab12/intro-Xy9.md"


@respx.mock
def test_sync_document_is_idempotent(tmp_path: Path):
    eng, _ = engine(tmp_path)
    mock_doc_endpoints()
    assert eng.sync_document("doc1") is True
    assert eng.sync_document("doc1") is False


@respx.mock
def test_rename_moves_file(tmp_path: Path):
    eng, dest = engine(tmp_path)
    mock_doc_endpoints()
    eng.sync_document("doc1")
    renamed = dict(DOC, title="Welcome")
    mock_doc_endpoints(doc=renamed, markdown="# Welcome\n")
    eng.sync_document("doc1")
    tree = dest.list_tree()
    assert "collections/guides-Ab12/welcome-Xy9.md" in tree
    assert "collections/guides-Ab12/intro-Xy9.md" not in tree


@respx.mock
def test_delete_document(tmp_path: Path):
    eng, dest = engine(tmp_path)
    mock_doc_endpoints()
    eng.sync_document("doc1")
    assert eng.delete_document("doc1") is True
    tree = dest.list_tree()
    assert "collections/guides-Ab12/intro-Xy9.md" not in tree
    assert json.loads(dest.read_file(MANIFEST_PATH))["documents"] == {}


@respx.mock
def test_sync_all_batches(tmp_path: Path):
    eng, dest = engine(tmp_path)
    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": [COL]})
    )
    respx.post(f"{BASE}/api/documents.list").mock(
        return_value=httpx.Response(200, json={"data": [DOC]})
    )
    mock_doc_endpoints()
    changed = eng.sync_all()
    assert changed >= 2
    assert "collections/guides-Ab12/intro-Xy9.md" in dest.list_tree()


@respx.mock
def test_delete_unknown_document_returns_false_and_is_noop(tmp_path: Path):
    eng, dest = engine(tmp_path)
    mock_doc_endpoints()
    assert eng.sync_document("doc1") is True
    tree_before = dest.list_tree()

    assert eng.delete_document("ghost") is False
    assert dest.list_tree() == tree_before


@respx.mock
def test_sync_all_cleans_up_renamed_paths(tmp_path: Path):
    eng, dest = engine(tmp_path)
    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": [COL]})
    )

    def documents_list_side_effect(request):
        payload = json.loads(request.content)
        if payload.get("offset", 0) > 0:
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [documents_list_side_effect.doc]})

    documents_list_side_effect.doc = DOC
    respx.post(f"{BASE}/api/documents.list").mock(side_effect=documents_list_side_effect)
    mock_doc_endpoints()
    eng.sync_all()

    renamed = dict(DOC, title="Welcome")
    documents_list_side_effect.doc = renamed
    mock_doc_endpoints(doc=renamed, markdown="# Welcome\n")
    eng.sync_all()

    tree = dest.list_tree()
    assert "collections/guides-Ab12/welcome-Xy9.md" in tree
    assert "collections/guides-Ab12/welcome-Xy9.comments.json" in tree
    assert "collections/guides-Ab12/intro-Xy9.md" not in tree
    assert "collections/guides-Ab12/intro-Xy9.comments.json" not in tree


@respx.mock
def test_sync_document_prunes_orphaned_sidecar(tmp_path: Path):
    eng, dest = engine(tmp_path)
    mock_doc_endpoints()
    assert eng.sync_document("doc1") is True
    assert "collections/guides-Ab12/intro-Xy9.comments.json" in dest.list_tree()

    mock_doc_endpoints(comments=[])
    assert eng.sync_document("doc1") is True
    tree = dest.list_tree()
    assert "collections/guides-Ab12/intro-Xy9.comments.json" not in tree
    assert "collections/guides-Ab12/intro-Xy9.md" in tree


@respx.mock
def test_sync_document_cycle_guard_avoids_recursion_error(tmp_path: Path):
    eng, dest = engine(tmp_path)
    doc = dict(DOC, parentDocumentId="parent1")
    parent = {
        "id": "parent1", "title": "Loopy", "urlId": "Pp1", "collectionId": "col1",
        "parentDocumentId": "parent1", "updatedAt": "2026-01-02T00:00:00Z",
    }

    def info_side_effect(request):
        payload = json.loads(request.content)
        data = parent if payload.get("id") == "parent1" else doc
        return httpx.Response(200, json={"data": data})

    respx.post(f"{BASE}/api/documents.info").mock(side_effect=info_side_effect)
    respx.post(f"{BASE}/api/documents.export").mock(return_value=httpx.Response(200, json={"data": "# Intro\n"}))
    respx.post(f"{BASE}/api/collections.info").mock(return_value=httpx.Response(200, json={"data": COL}))
    respx.post(f"{BASE}/api/comments.list").mock(return_value=httpx.Response(200, json={"data": COMMENTS}))

    assert eng.sync_document("doc1") is True
    tree = dest.list_tree()
    assert "collections/guides-Ab12/intro-Xy9.md" in tree
