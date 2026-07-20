import httpx
import pytest
import respx

from outline_backup.core.outline_client import OutlineClient, OutlineError

BASE = "https://wiki.example.com"


def client() -> OutlineClient:
    return OutlineClient(BASE, "ol_tok")


@respx.mock
def test_document_export_returns_markdown():
    route = respx.post(f"{BASE}/api/documents.export").mock(
        return_value=httpx.Response(200, json={"data": "# Title\n\nBody"})
    )
    assert client().document_export("doc1") == "# Title\n\nBody"
    assert route.calls.last.request.headers["authorization"] == "Bearer ol_tok"


@respx.mock
def test_error_raises():
    respx.post(f"{BASE}/api/documents.info").mock(return_value=httpx.Response(403, json={}))
    with pytest.raises(OutlineError):
        client().document_info("doc1")


@respx.mock
def test_list_documents_paginates():
    pages = [
        httpx.Response(200, json={"data": [{"id": str(i)} for i in range(100)]}),
        httpx.Response(200, json={"data": [{"id": "100"}]}),
    ]
    respx.post(f"{BASE}/api/documents.list").mock(side_effect=pages)
    docs = client().list_documents("col1")
    assert len(docs) == 101


@respx.mock
def test_create_comment_payload():
    route = respx.post(f"{BASE}/api/comments.create").mock(
        return_value=httpx.Response(200, json={"data": {"id": "c1"}})
    )
    client().create_comment("doc1", {"type": "doc"}, parent_comment_id="p1")
    import json

    sent = json.loads(route.calls.last.request.content)
    assert sent == {"documentId": "doc1", "data": {"type": "doc"}, "parentCommentId": "p1"}
