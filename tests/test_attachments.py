import httpx
import pytest
import respx

from outline_backup.core.attachments import attachment_path, find_attachment_ids, rewrite_for_zip
from outline_backup.core.outline_client import OutlineClient, OutlineError

BASE = "https://wiki.example.com"
ID1 = "11111111-2222-3333-4444-555555555555"
ID2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

MD = (
    "# Doc\n"
    f"![img](/api/attachments.redirect?id={ID1})\n"
    f"[file.pdf](https://wiki.example.com/api/attachments.redirect?id={ID2})\n"
    f"![again](/api/attachments.redirect?id={ID1})\n"
)


def test_find_ids_ordered_and_deduped():
    assert find_attachment_ids(MD) == [ID1, ID2]


def test_find_ids_none():
    assert find_attachment_ids("# Plain\n\nNo files here.\n") == []


def test_rewrite_for_zip_relativizes_all_forms():
    out = rewrite_for_zip(MD, "Guides/Intro.md")
    assert f"![img](../attachments/{ID1})" in out
    assert f"[file.pdf](../attachments/{ID2})" in out
    assert "attachments.redirect" not in out


def test_rewrite_for_zip_depth_matches_member_nesting():
    out = rewrite_for_zip(MD, "Guides/Sub/Deep.md")
    assert f"![img](../../attachments/{ID1})" in out


@respx.mock
def test_download_attachment_enforces_cap_while_streaming():
    respx.get(f"{BASE}/api/attachments.redirect", params={"id": ID1}).mock(
        return_value=httpx.Response(200, content=b"PNGDATA")
    )
    with pytest.raises(OutlineError):
        OutlineClient(BASE, "tok").download_attachment(ID1, max_bytes=3)


@respx.mock
def test_download_attachment_under_cap_returns_content():
    respx.get(f"{BASE}/api/attachments.redirect", params={"id": ID1}).mock(
        return_value=httpx.Response(200, content=b"PNGDATA")
    )
    assert OutlineClient(BASE, "tok").download_attachment(ID1, max_bytes=100) == b"PNGDATA"


def test_attachment_path():
    assert attachment_path(ID1) == f"attachments/{ID1}"


@respx.mock
def test_download_attachment_follows_redirect():
    respx.get(f"{BASE}/api/attachments.redirect", params={"id": ID1}).mock(
        return_value=httpx.Response(302, headers={"Location": f"{BASE}/signed/{ID1}"})
    )
    respx.get(f"{BASE}/signed/{ID1}").mock(return_value=httpx.Response(200, content=b"PNGDATA"))
    assert OutlineClient(BASE, "tok").download_attachment(ID1) == b"PNGDATA"
