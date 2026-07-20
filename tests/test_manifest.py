from outline_backup.core.manifest import Manifest, sidecar_for


def test_roundtrip_and_paths():
    m = Manifest()
    m.set_collection("col1", name="Guides", slug="guides-Ab12Cd34Ef")
    m.set_document(
        "doc1", title="Intro", slug="intro-Xy9", path="collections/guides-Ab12Cd34Ef/intro-Xy9.md",
        collection_id="col1", parent_document_id=None, updated_at="2026-01-01T00:00:00Z",
    )
    m2 = Manifest.from_bytes(m.to_bytes())
    assert m2.path_for("doc1") == "collections/guides-Ab12Cd34Ef/intro-Xy9.md"
    assert m2.collections["col1"]["slug"] == "guides-Ab12Cd34Ef"
    assert m2.to_bytes() == m.to_bytes()


def test_sidecar_and_removal():
    assert sidecar_for("a/b/page.md") == "a/b/page.comments.json"
    m = Manifest()
    m.set_document(
        "doc1", title="T", slug="t-Xy9", path="collections/c/t-Xy9.md",
        collection_id="col1", parent_document_id=None, updated_at=None,
    )
    assert m.remove_document("doc1") == ["collections/c/t-Xy9.md", "collections/c/t-Xy9.comments.json"]
    assert m.path_for("doc1") is None
    assert m.remove_document("ghost") == []
