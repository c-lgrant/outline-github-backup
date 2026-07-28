import httpx
import pytest
import respx

from outline_backup.core import outline_client as outline_client_module
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


@respx.mock
def test_429_retries_with_retry_after_header_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(outline_client_module, "_sleep", sleeps.append)

    responses = [
        httpx.Response(429, headers={"Retry-After": "3"}, json={}),
        httpx.Response(200, json={"data": "# Title\n\nBody"}),
    ]
    respx.post(f"{BASE}/api/documents.export").mock(side_effect=responses)

    assert client().document_export("doc1") == "# Title\n\nBody"
    assert sleeps == [3]


@respx.mock
def test_429_six_consecutive_times_raises(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(outline_client_module, "_sleep", sleeps.append)

    respx.post(f"{BASE}/api/documents.export").mock(return_value=httpx.Response(429, json={}))

    with pytest.raises(OutlineError):
        client().document_export("doc1")
    # 5 retries -> 5 sleeps recorded before giving up
    assert len(sleeps) == 5


@respx.mock
def test_max_429_retries_is_configurable(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(outline_client_module, "_sleep", sleeps.append)
    respx.post(f"{BASE}/api/documents.info").mock(return_value=httpx.Response(429, json={}))
    with pytest.raises(OutlineError):
        OutlineClient(BASE, "tok", max_429_retries=1).document_info("doc1")
    assert len(sleeps) == 1


@respx.mock
def test_retry_after_cap_is_configurable(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(outline_client_module, "_sleep", sleeps.append)
    respx.post(f"{BASE}/api/documents.info").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "500"}, json={}),
        httpx.Response(200, json={"data": {"id": "doc1"}}),
    ])
    OutlineClient(BASE, "tok", max_retry_after_seconds=5).document_info("doc1")
    assert sleeps == [5]


@respx.mock
def test_retries_beyond_backoff_table_reuse_last_delay(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(outline_client_module, "_sleep", sleeps.append)
    respx.post(f"{BASE}/api/documents.info").mock(return_value=httpx.Response(429, json={}))
    with pytest.raises(OutlineError):
        OutlineClient(BASE, "tok", max_429_retries=7).document_info("doc1")
    assert len(sleeps) == 7 and sleeps[-1] == 32
