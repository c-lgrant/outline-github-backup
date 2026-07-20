import json

from outline_backup.core.serialize import comment_text, comments_sidecar, doc_slug, slugify


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("  múltiple   spaces  ") == "multiple-spaces"
    assert doc_slug("Hello", "AbC123xyz") == "hello-AbC123xyz"
    assert doc_slug("???", "AbC123xyz") == "untitled-AbC123xyz"


def test_comment_text_walks_prosemirror():
    data = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Nice "},
                                              {"type": "text", "text": "doc!"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Second line."}]},
        ],
    }
    assert comment_text(data) == "Nice doc!\nSecond line."


def test_sidecar_is_deterministic_and_sorted():
    comments = [
        {"id": "c2", "createdAt": "2026-02-01T00:00:00Z", "parentCommentId": "c1",
         "data": {"type": "doc", "content": []}, "createdBy": {"name": "Alex"}},
        {"id": "c1", "createdAt": "2026-01-01T00:00:00Z", "parentCommentId": None,
         "data": {"type": "doc", "content": []}},
    ]
    raw = comments_sidecar(comments)
    assert raw == comments_sidecar(list(reversed(comments)))
    entries = json.loads(raw)
    assert [e["id"] for e in entries] == ["c1", "c2"]
    assert entries[1]["authorName"] == "Alex"
    assert entries[0]["authorName"] is None
