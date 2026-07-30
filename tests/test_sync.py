import json
from pathlib import Path

import httpx
import pytest
import respx

from outline_backup.core import sync as sync_module
from outline_backup.core.manifest import MANIFEST_PATH
from outline_backup.core.outline_client import OutlineClient
from outline_backup.core.sync import SyncEngine
from outline_backup.destinations.base import DestinationConflictError
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
def test_sync_document_removes_archived_doc(tmp_path: Path):
    # documents.info happily returns archived docs; a sync job that lands
    # after an archive (event races, webhook retries) must remove the mirror
    # copy, not resurrect it
    eng, dest = engine(tmp_path)
    mock_doc_endpoints()
    eng.sync_document("doc1")
    archived = dict(DOC, archivedAt="2026-01-03T00:00:00Z", updatedAt="2026-01-03T00:00:00Z")
    mock_doc_endpoints(doc=archived)
    assert eng.sync_document("doc1") is True
    tree = dest.list_tree()
    assert "collections/guides-Ab12/intro-Xy9.md" not in tree
    assert json.loads(dest.read_file(MANIFEST_PATH))["documents"] == {}


@respx.mock
def test_sync_document_skips_never_mirrored_archived_doc(tmp_path: Path):
    eng, dest = engine(tmp_path)
    archived = dict(DOC, archivedAt="2026-01-03T00:00:00Z")
    mock_doc_endpoints(doc=archived)
    assert eng.sync_document("doc1") is False
    assert dest.list_tree() == {}


@respx.mock
def test_sync_document_removes_trashed_doc(tmp_path: Path):
    eng, dest = engine(tmp_path)
    mock_doc_endpoints()
    eng.sync_document("doc1")
    trashed = dict(DOC, deletedAt="2026-01-03T00:00:00Z")
    mock_doc_endpoints(doc=trashed)
    assert eng.sync_document("doc1") is True
    assert "collections/guides-Ab12/intro-Xy9.md" not in dest.list_tree()


@respx.mock
def test_sync_all_paces_between_documents(tmp_path: Path, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(sync_module, "_sleep", sleeps.append)

    dest = LocalDestination(tmp_path)
    eng = SyncEngine(OutlineClient(BASE, "tok"), dest, pace_seconds=0.5)
    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": [COL]})
    )
    respx.post(f"{BASE}/api/documents.list").mock(
        return_value=httpx.Response(200, json={"data": [DOC]})
    )
    mock_doc_endpoints()
    eng.sync_all()
    assert sleeps == [0.5]


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
def test_sync_all_heals_manifest_only_drift(tmp_path: Path):
    eng, dest = engine(tmp_path)
    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": [COL]})
    )
    respx.post(f"{BASE}/api/documents.list").mock(
        return_value=httpx.Response(200, json={"data": [DOC]})
    )
    mock_doc_endpoints()
    eng.sync_all()

    # Simulate a lost manifest update: the .md file is intact but the entry is gone.
    manifest = json.loads(dest.read_file(MANIFEST_PATH))
    del manifest["documents"]["doc1"]
    dest.write_files(
        {MANIFEST_PATH: (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()},
        "clobber",
    )

    assert eng.sync_all() == 1  # manifest re-committed even though no files changed
    assert "doc1" in json.loads(dest.read_file(MANIFEST_PATH))["documents"]


@respx.mock
def test_sync_document_heals_manifest_only_drift(tmp_path: Path):
    eng, dest = engine(tmp_path)
    mock_doc_endpoints()
    eng.sync_document("doc1")

    manifest = json.loads(dest.read_file(MANIFEST_PATH))
    del manifest["documents"]["doc1"]
    dest.write_files(
        {MANIFEST_PATH: (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()},
        "clobber",
    )

    assert eng.sync_document("doc1") is True
    assert "doc1" in json.loads(dest.read_file(MANIFEST_PATH))["documents"]


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


class RecordingDest(LocalDestination):
    def __init__(self, root: Path):
        super().__init__(root)
        self.write_calls: list[dict] = []

    def write_files(self, files, message, deletions=None):
        self.write_calls.append({"files": set(files), "deletions": list(deletions or [])})
        super().write_files(files, message, deletions=deletions)


@respx.mock
def test_rename_is_one_atomic_write(tmp_path: Path):
    dest = RecordingDest(tmp_path)
    eng = SyncEngine(OutlineClient(BASE, "tok"), dest)
    mock_doc_endpoints()
    eng.sync_document("doc1")
    dest.write_calls.clear()

    renamed = dict(DOC, title="Welcome")
    mock_doc_endpoints(doc=renamed, markdown="# Welcome\n")
    eng.sync_document("doc1")

    assert len(dest.write_calls) == 1
    call = dest.write_calls[0]
    assert "collections/guides-Ab12/intro-Xy9.md" in call["deletions"]
    assert "collections/guides-Ab12/welcome-Xy9.md" in call["files"]


@respx.mock
def test_delete_document_is_one_atomic_write(tmp_path: Path):
    dest = RecordingDest(tmp_path)
    eng = SyncEngine(OutlineClient(BASE, "tok"), dest)
    mock_doc_endpoints()
    eng.sync_document("doc1")
    dest.write_calls.clear()

    assert eng.delete_document("doc1") is True
    assert len(dest.write_calls) == 1
    call = dest.write_calls[0]
    assert call["files"] == {MANIFEST_PATH}
    assert "collections/guides-Ab12/intro-Xy9.md" in call["deletions"]


class ConflictOnceDest(LocalDestination):
    """First write conflicts; a concurrent writer's manifest entry lands in between."""

    def __init__(self, root: Path):
        super().__init__(root)
        self.conflicted = False

    def write_files(self, files, message, deletions=None):
        if not self.conflicted:
            self.conflicted = True
            manifest = {"version": 1, "collections": {}, "documents": {
                "doc-other": {"title": "Other", "slug": "other-Zz1",
                              "path": "collections/misc-Cc1/other-Zz1.md",
                              "collectionId": "col2", "parentDocumentId": None,
                              "updatedAt": "2026-01-05T00:00:00Z"}}}
            super().write_files(
                {MANIFEST_PATH: (json.dumps(manifest) + "\n").encode()}, "concurrent writer"
            )
            raise DestinationConflictError("ref moved")
        super().write_files(files, message, deletions=deletions)


@respx.mock
def test_conflict_retry_reapplies_on_fresh_manifest(tmp_path: Path):
    # Issue #10: after a non-fast-forward conflict the engine must re-read
    # the manifest and re-apply — resubmitting the stale blob would clobber
    # the concurrent writer's doc-other entry.
    dest = ConflictOnceDest(tmp_path)
    eng = SyncEngine(OutlineClient(BASE, "tok"), dest)
    mock_doc_endpoints()
    assert eng.sync_document("doc1") is True

    manifest = json.loads(dest.read_file(MANIFEST_PATH))
    assert "doc1" in manifest["documents"]
    assert "doc-other" in manifest["documents"]


class AlwaysConflictDest(LocalDestination):
    def write_files(self, files, message, deletions=None):
        raise DestinationConflictError("ref moved")


@respx.mock
def test_conflict_retry_gives_up_eventually(tmp_path: Path):
    eng = SyncEngine(OutlineClient(BASE, "tok"), AlwaysConflictDest(tmp_path))
    mock_doc_endpoints()
    with pytest.raises(DestinationConflictError):
        eng.sync_document("doc1")


@respx.mock
def test_sync_all_prune_removes_upstream_deleted(tmp_path: Path):
    eng, dest = engine(tmp_path)
    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": [COL]})
    )
    docs_route = respx.post(f"{BASE}/api/documents.list").mock(
        return_value=httpx.Response(200, json={"data": [DOC]})
    )
    mock_doc_endpoints()
    eng.sync_all()
    assert "collections/guides-Ab12/intro-Xy9.md" in dest.list_tree()

    # doc2 keeps the walk non-empty; doc1 is gone and its info now 404s
    doc2 = dict(DOC, id="doc2", title="Other", urlId="Zz2")
    docs_route.mock(return_value=httpx.Response(200, json={"data": [doc2]}))
    mock_doc_endpoints(doc=doc2, markdown="# Other\n")
    respx.post(f"{BASE}/api/documents.info").mock(
        side_effect=lambda request: httpx.Response(404, json={})
        if json.loads(request.content).get("id") == "doc1"
        else httpx.Response(200, json={"data": doc2})
    )
    eng.sync_all(prune=True)
    tree = dest.list_tree()
    assert "collections/guides-Ab12/intro-Xy9.md" not in tree
    assert "collections/guides-Ab12/intro-Xy9.comments.json" not in tree
    assert "doc1" not in json.loads(dest.read_file(MANIFEST_PATH))["documents"]


@respx.mock
def test_prune_keeps_doc_when_info_says_alive(tmp_path: Path):
    # documents.list not returning a doc is NOT proof of deletion (offset
    # pagination skips, transient empty responses). Only a definitive
    # documents.info verdict may delete from the mirror.
    eng, dest = engine(tmp_path)
    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": [COL]})
    )
    docs_route = respx.post(f"{BASE}/api/documents.list").mock(
        return_value=httpx.Response(200, json={"data": [DOC]})
    )
    mock_doc_endpoints()
    eng.sync_all()

    doc2 = dict(DOC, id="doc2", title="Other", urlId="Zz2")
    docs_route.mock(return_value=httpx.Response(200, json={"data": [doc2]}))
    mock_doc_endpoints(doc=doc2, markdown="# Other\n")
    respx.post(f"{BASE}/api/documents.info").mock(
        side_effect=lambda request: httpx.Response(200, json={"data": DOC})
        if json.loads(request.content).get("id") == "doc1"
        else httpx.Response(200, json={"data": doc2})
    )
    eng.sync_all(prune=True)
    assert "collections/guides-Ab12/intro-Xy9.md" in dest.list_tree()
    assert "doc1" in json.loads(dest.read_file(MANIFEST_PATH))["documents"]


@respx.mock
def test_prune_keeps_doc_on_transient_info_error(tmp_path: Path):
    eng, dest = engine(tmp_path)
    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": [COL]})
    )
    docs_route = respx.post(f"{BASE}/api/documents.list").mock(
        return_value=httpx.Response(200, json={"data": [DOC]})
    )
    mock_doc_endpoints()
    eng.sync_all()

    doc2 = dict(DOC, id="doc2", title="Other", urlId="Zz2")
    docs_route.mock(return_value=httpx.Response(200, json={"data": [doc2]}))
    mock_doc_endpoints(doc=doc2, markdown="# Other\n")
    respx.post(f"{BASE}/api/documents.info").mock(
        side_effect=lambda request: httpx.Response(500, json={})
        if json.loads(request.content).get("id") == "doc1"
        else httpx.Response(200, json={"data": doc2})
    )
    eng.sync_all(prune=True)  # must complete, and must not delete doc1
    assert "collections/guides-Ab12/intro-Xy9.md" in dest.list_tree()


@respx.mock
def test_prune_refuses_empty_walk(tmp_path: Path):
    # An empty workspace listing is indistinguishable from a sick API;
    # pruning everything off it would wipe the mirror in one commit.
    eng, dest = engine(tmp_path)
    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": [COL]})
    )
    docs_route = respx.post(f"{BASE}/api/documents.list").mock(
        return_value=httpx.Response(200, json={"data": [DOC]})
    )
    mock_doc_endpoints()
    eng.sync_all()

    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    docs_route.mock(return_value=httpx.Response(200, json={"data": []}))
    eng.sync_all(prune=True)
    assert "collections/guides-Ab12/intro-Xy9.md" in dest.list_tree()
    assert "doc1" in json.loads(dest.read_file(MANIFEST_PATH))["documents"]


@respx.mock
def test_sync_all_without_prune_keeps_upstream_deleted(tmp_path: Path):
    eng, dest = engine(tmp_path)
    respx.post(f"{BASE}/api/collections.list").mock(
        return_value=httpx.Response(200, json={"data": [COL]})
    )
    docs_route = respx.post(f"{BASE}/api/documents.list").mock(
        return_value=httpx.Response(200, json={"data": [DOC]})
    )
    mock_doc_endpoints()
    eng.sync_all()

    docs_route.mock(return_value=httpx.Response(200, json={"data": []}))
    assert eng.sync_all() == 0
    assert "collections/guides-Ab12/intro-Xy9.md" in dest.list_tree()


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
