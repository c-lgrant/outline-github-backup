import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

import outline_backup.core.restore as restore_module
from outline_backup.cli.main import app
from outline_backup.core.manifest import Manifest
from outline_backup.core.outline_client import OutlineClient
from outline_backup.core.restore import (
    RestoreError,
    RestoreReport,
    _replay_comments,
    _wait_for_operation,
    build_markdown_zip,
    restore,
)
from outline_backup.destinations.local import LocalDestination

BASE = "https://restore.example.com"
runner = CliRunner()


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


# -- Finding 1: title collisions ----------------------------------------


def test_build_markdown_zip_disambiguates_title_collisions(tmp_path: Path):
    dest = LocalDestination(tmp_path)
    m = Manifest()
    m.set_collection("col1", name="Guides", slug="guides-Ab12")
    m.set_document("doc1", title="Untitled", slug="untitled-Ab9",
                   path="collections/guides-Ab12/a.md",
                   collection_id="col1", parent_document_id=None, updated_at=None)
    m.set_document("doc2", title="Untitled", slug="untitled-Xy2",
                   path="collections/guides-Ab12/b.md",
                   collection_id="col1", parent_document_id=None, updated_at=None)
    dest.write_files({
        "_manifest.json": m.to_bytes(),
        "collections/guides-Ab12/a.md": b"first\n",
        "collections/guides-Ab12/b.md": b"second\n",
    }, "seed")
    manifest = Manifest.from_bytes(dest.read_file("_manifest.json"))
    blob = build_markdown_zip(manifest, dest.read_file, "col1")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        assert names == {"Guides/Untitled.md", "Guides/Untitled (untitled-Xy2).md"}
        assert zf.read("Guides/Untitled.md") == b"first\n"
        assert zf.read("Guides/Untitled (untitled-Xy2).md") == b"second\n"


@respx.mock
def test_restore_skips_replay_on_ambiguous_imported_titles(tmp_path: Path):
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
        "data": [
            {"id": "newdoc1", "title": "Intro", "urlId": "Qq11", "collectionId": "newcol"},
            {"id": "newdoc2", "title": "Intro", "urlId": "Qq12", "collectionId": "newcol"},
        ]
    }))
    comment_route = respx.post(f"{BASE}/api/comments.create").mock(
        return_value=httpx.Response(200, json={"data": {"id": "newc1"}})
    )
    report = restore(
        OutlineClient(BASE, "tok"), dest,
        upload=lambda url, form, content: None,
    )
    assert comment_route.call_count == 0
    assert report.comments_created == 0
    assert "ambiguous title match, skipping comment replay for: Intro" in report.warnings


# -- Finding 2: list_tree hoisted out of the per-document loop ----------


@respx.mock
def test_restore_calls_list_tree_once_for_multiple_documents(tmp_path: Path):
    dest = LocalDestination(tmp_path)
    m = Manifest()
    m.set_collection("col1", name="Guides", slug="guides-Ab12")
    m.set_document("doc1", title="Intro", slug="intro-Xy9",
                   path="collections/guides-Ab12/intro-Xy9.md",
                   collection_id="col1", parent_document_id=None, updated_at=None)
    m.set_document("doc2", title="Setup", slug="setup-Zz1",
                   path="collections/guides-Ab12/setup-Zz1.md",
                   collection_id="col1", parent_document_id=None, updated_at=None)
    dest.write_files({
        "_manifest.json": m.to_bytes(),
        "collections/guides-Ab12/intro-Xy9.md": b"# Intro\n",
        "collections/guides-Ab12/setup-Zz1.md": b"# Setup\n",
    }, "seed")

    calls = {"count": 0}
    real_list_tree = dest.list_tree

    def counting_list_tree():
        calls["count"] += 1
        return real_list_tree()

    dest.list_tree = counting_list_tree

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
        "data": [
            {"id": "newdoc1", "title": "Intro", "urlId": "Qq11", "collectionId": "newcol"},
            {"id": "newdoc2", "title": "Setup", "urlId": "Qq12", "collectionId": "newcol"},
        ]
    }))
    restore(OutlineClient(BASE, "tok"), dest, upload=lambda url, form, content: None)
    assert calls["count"] == 1


# -- Finding 3: missing parent comment is flattened with a warning ------


def test_replay_comments_warns_and_flattens_missing_parent(tmp_path: Path):
    dest = LocalDestination(tmp_path)
    entry = {"path": "collections/guides-Ab12/intro-Xy9.md", "title": "Intro"}
    sidecar = [{"id": "c2", "createdAt": "2026-01-01T00:00:01Z", "parentCommentId": "missing-parent",
                "authorName": "Alex", "data": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "reply"}]}]}}]
    dest.write_files({
        "collections/guides-Ab12/intro-Xy9.comments.json": (json.dumps(sidecar) + "\n").encode(),
    }, "seed")
    tree = dest.list_tree()

    class FakeClient:
        def __init__(self):
            self.calls: list[tuple[str, dict, str | None]] = []

        def create_comment(self, document_id, data, parent_comment_id=None):
            self.calls.append((document_id, data, parent_comment_id))
            return {"id": "newc2"}

    client = FakeClient()
    report = RestoreReport()
    created = _replay_comments(client, dest, entry, "newdoc", report, tree)
    assert created == 1
    assert client.calls[0][2] is None
    assert "comment c2: parent missing, posted as top-level" in report.warnings


# -- Finding 4: dry-run per-collection file counts -----------------------


def test_dry_run_reports_collection_file_counts(tmp_path: Path):
    dest = seed_source(tmp_path)
    report = restore(OutlineClient(BASE, "tok"), dest, dry_run=True)
    assert report.collection_files == {"Guides": 1}


def test_cli_dry_run_prints_collection_file_counts(tmp_path: Path):
    dest_dir = tmp_path / "src"
    dest_dir.mkdir()
    seed_source(dest_dir)
    result = runner.invoke(
        app,
        ["restore", str(dest_dir), "--target-url", BASE, "--target-token", "tok", "--dry-run"],
    )
    assert result.exit_code == 0
    assert "Guides: 1 document(s)" in result.output


def test_cli_restore_target_token_from_env(tmp_path: Path, monkeypatch):
    # --target-token deliberately omitted from argv; the CLI must accept the
    # token via the OUTLINE_TARGET_TOKEN env var instead of requiring it on
    # the command line (where it would leak into shell history/process list).
    dest_dir = tmp_path / "src"
    dest_dir.mkdir()
    seed_source(dest_dir)
    monkeypatch.setenv("OUTLINE_TARGET_TOKEN", "tok")
    result = runner.invoke(
        app,
        ["restore", str(dest_dir), "--target-url", BASE, "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "Guides: 1 document(s)" in result.output


# -- Finding 5: _wait_for_operation error branch + no trailing sleep -----


@respx.mock
def test_wait_for_operation_raises_on_error_state():
    respx.post(f"{BASE}/api/fileOperations.info").mock(
        return_value=httpx.Response(200, json={"data": {"id": "op1", "state": "error", "error": "boom"}})
    )
    client = OutlineClient(BASE, "tok")
    with pytest.raises(RestoreError, match="boom"):
        _wait_for_operation(client, "op1")


@respx.mock
def test_wait_for_operation_does_not_sleep_after_final_poll(monkeypatch):
    respx.post(f"{BASE}/api/fileOperations.info").mock(
        return_value=httpx.Response(200, json={"data": {"id": "op1", "state": "creating"}})
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr(restore_module.time, "sleep", lambda s: sleep_calls.append(s))
    client = OutlineClient(BASE, "tok")
    with pytest.raises(RestoreError, match="timed out"):
        _wait_for_operation(client, "op1")
    assert len(sleep_calls) == 59


# -- Fix: comment-replay lookup must use the manifest title, not the -----
# -- disambiguated zip member filename stem ------------------------------


@respx.mock
def test_restore_matches_disambiguated_doc_by_original_title(tmp_path: Path):
    dest = LocalDestination(tmp_path)
    m = Manifest()
    m.set_collection("col1", name="Guides", slug="guides-Ab12")
    # doc1's sanitized title collides with doc2's literal title, so doc2 (sorted
    # second by path) gets its zip member filename disambiguated with its slug —
    # even though doc2's title ("Foo/Bar") is unique across the whole manifest.
    m.set_document("doc1", title="Foo-Bar", slug="foo-bar-Aa1",
                   path="collections/guides-Ab12/a.md",
                   collection_id="col1", parent_document_id=None, updated_at=None)
    m.set_document("doc2", title="Foo/Bar", slug="foo-bar-Bb2",
                   path="collections/guides-Ab12/b.md",
                   collection_id="col1", parent_document_id=None, updated_at=None)
    sidecar = [{"id": "c1", "createdAt": "2026-01-01T00:00:00Z", "parentCommentId": None,
                "authorName": "Alex", "data": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "hi"}]}]}}]
    dest.write_files({
        "_manifest.json": m.to_bytes(),
        "collections/guides-Ab12/a.md": b"first\n",
        "collections/guides-Ab12/b.md": b"second\n",
        "collections/guides-Ab12/b.comments.json": (json.dumps(sidecar) + "\n").encode(),
    }, "seed")

    manifest = Manifest.from_bytes(dest.read_file("_manifest.json"))
    members = restore_module._zip_members(manifest, "col1")
    # Sanity check the fixture actually produces a disambiguated member for doc2.
    assert members["doc2"] == "Guides/Foo-Bar (foo-bar-Bb2).md"

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
        "data": [
            {"id": "newdoc1", "title": "Foo-Bar", "urlId": "Qq11", "collectionId": "newcol"},
            {"id": "newdoc2", "title": "Foo/Bar", "urlId": "Qq12", "collectionId": "newcol"},
        ]
    }))
    comment_route = respx.post(f"{BASE}/api/comments.create").mock(
        return_value=httpx.Response(200, json={"data": {"id": "newc1"}})
    )
    report = restore(
        OutlineClient(BASE, "tok"), dest,
        upload=lambda url, form, content: None,
    )
    assert report.documents_matched == 2
    assert report.comments_created == 1
    assert not any("no imported match" in w for w in report.warnings)
    sent = json.loads(comment_route.calls.last.request.content)
    assert sent["documentId"] == "newdoc2"


def seed_two_collections(tmp_path: Path) -> LocalDestination:
    dest = LocalDestination(tmp_path)
    m = Manifest()
    m.set_collection("col1", name="Guides", slug="guides-Ab12")
    m.set_collection("col2", name="Docs", slug="docs-Cd34")
    m.set_document("doc1", title="Intro", slug="intro-Xy9",
                   path="collections/guides-Ab12/intro-Xy9.md",
                   collection_id="col1", parent_document_id=None, updated_at=None)
    m.set_document("doc2", title="Setup", slug="setup-Qq2",
                   path="collections/docs-Cd34/setup-Qq2.md",
                   collection_id="col2", parent_document_id=None, updated_at=None)
    dest.write_files({
        "_manifest.json": m.to_bytes(),
        "collections/guides-Ab12/intro-Xy9.md": b"# Intro\n",
        "collections/docs-Cd34/setup-Qq2.md": b"# Setup\n",
    }, "seed")
    return dest


def test_restore_collections_filter_limits_scope(tmp_path: Path):
    dest = seed_two_collections(tmp_path)
    report = restore(OutlineClient(BASE, "tok"), dest, dry_run=True, collections=["Guides"])
    assert report.collections == 1
    assert report.collection_files == {"Guides": 1}


def test_restore_collections_filter_warns_on_unknown_name(tmp_path: Path):
    dest = seed_two_collections(tmp_path)
    report = restore(OutlineClient(BASE, "tok"), dest, dry_run=True, collections=["Nope"])
    assert report.collections == 0
    assert any("Nope" in w for w in report.warnings)


ATT_ID = "11111111-2222-3333-4444-555555555555"


def seed_with_attachment(tmp_path: Path) -> LocalDestination:
    dest = LocalDestination(tmp_path)
    m = Manifest()
    m.set_collection("col1", name="Guides", slug="guides-Ab12")
    m.set_document("doc1", title="Intro", slug="intro-Xy9",
                   path="collections/guides-Ab12/intro-Xy9.md",
                   collection_id="col1", parent_document_id=None, updated_at=None)
    md = f"# Intro\n![diagram](/api/attachments.redirect?id={ATT_ID})\n"
    dest.write_files({
        "_manifest.json": m.to_bytes(),
        "collections/guides-Ab12/intro-Xy9.md": md.encode(),
        f"attachments/{ATT_ID}": b"PNGDATA",
    }, "seed")
    return dest


def test_zip_rewrites_links_and_bundles_attachments(tmp_path: Path):
    dest = seed_with_attachment(tmp_path)
    manifest = Manifest.from_bytes(dest.read_file("_manifest.json"))
    blob = build_markdown_zip(manifest, dest.read_file, "col1", tree=dest.list_tree())
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert set(zf.namelist()) == {"Guides/Intro.md", f"attachments/{ATT_ID}"}
        content = zf.read("Guides/Intro.md").decode()
        assert f"![diagram](attachments/{ATT_ID})" in content
        assert "attachments.redirect" not in content
        assert zf.read(f"attachments/{ATT_ID}") == b"PNGDATA"


def test_zip_skips_attachments_missing_from_tree(tmp_path: Path):
    dest = seed_with_attachment(tmp_path)
    manifest = Manifest.from_bytes(dest.read_file("_manifest.json"))
    tree = {p: s for p, s in dest.list_tree().items() if not p.startswith("attachments/")}
    blob = build_markdown_zip(manifest, dest.read_file, "col1", tree=tree)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert zf.namelist() == ["Guides/Intro.md"]
