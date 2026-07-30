import base64
import json

import httpx
import pytest
import respx

from outline_backup.destinations.base import DestinationConflictError
from outline_backup.destinations.github import GitHubDestination, GitHubDestinationError

API = "https://api.github.com/repos/example/backup-data"


def dest() -> GitHubDestination:
    return GitHubDestination(repo="example/backup-data", branch="main", path_prefix="data", token="gh_tok")


def mock_write_flow() -> respx.Route:
    respx.get(f"{API}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "c-base"}})
    )
    respx.get(f"{API}/git/commits/c-base").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "t-base"}})
    )
    respx.post(f"{API}/git/blobs").mock(return_value=httpx.Response(201, json={"sha": "b1"}))
    tree_post = respx.post(f"{API}/git/trees").mock(return_value=httpx.Response(201, json={"sha": "t-new"}))
    respx.post(f"{API}/git/commits").mock(return_value=httpx.Response(201, json={"sha": "c-new"}))
    return tree_post


@respx.mock
def test_write_files_single_commit_with_prefix():
    tree_post = mock_write_flow()
    ref_patch = respx.patch(f"{API}/git/refs/heads/main").mock(
        return_value=httpx.Response(200, json={})
    )
    dest().write_files({"x.md": b"one", "y.md": b"two"}, "feat: two files")
    tree_body = json.loads(tree_post.calls.last.request.content)
    assert {e["path"] for e in tree_body["tree"]} == {"data/x.md", "data/y.md"}
    assert tree_body["base_tree"] == "t-base"
    assert ref_patch.called


@respx.mock
def test_ref_race_raises_conflict_not_silent_retry():
    mock_write_flow()
    respx.patch(f"{API}/git/refs/heads/main").mock(return_value=httpx.Response(422, json={}))
    with pytest.raises(DestinationConflictError):
        dest().write_files({"x.md": b"one"}, "msg")


@respx.mock
def test_deletions_ride_in_same_commit_as_writes():
    tree_post = mock_write_flow()
    ref_patch = respx.patch(f"{API}/git/refs/heads/main").mock(
        return_value=httpx.Response(200, json={})
    )
    dest().write_files({"new.md": b"moved"}, "backup: rename", deletions=["old.md"])
    assert tree_post.call_count == 1 and ref_patch.call_count == 1
    entries = {e["path"]: e["sha"] for e in json.loads(tree_post.calls.last.request.content)["tree"]}
    assert entries["data/old.md"] is None
    assert entries["data/new.md"] == "b1"


@respx.mock
def test_deletion_only_write_commits_null_shas():
    tree_post = mock_write_flow()
    respx.patch(f"{API}/git/refs/heads/main").mock(return_value=httpx.Response(200, json={}))
    dest().write_files({}, "chore: remove", deletions=["x.md"])
    entry = json.loads(tree_post.calls.last.request.content)["tree"][0]
    assert entry["path"] == "data/x.md" and entry["sha"] is None


@respx.mock
def test_write_commits_against_head_seen_at_list_tree():
    # list_tree observes head c-base; another writer then moves the ref to
    # c-other. The commit must still parent on c-base so the ref update
    # fails non-fast-forward instead of silently building on the other
    # writer's manifest with our stale blob.
    d = dest()
    respx.get(f"{API}/git/ref/heads/main").mock(
        side_effect=[
            httpx.Response(200, json={"object": {"sha": "c-base"}}),
            httpx.Response(200, json={"object": {"sha": "c-other"}}),
        ]
    )
    respx.get(f"{API}/git/commits/c-base").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "t-base"}})
    )
    respx.get(f"{API}/git/trees/t-base").mock(
        return_value=httpx.Response(200, json={"tree": []})
    )
    respx.post(f"{API}/git/blobs").mock(return_value=httpx.Response(201, json={"sha": "b1"}))
    respx.post(f"{API}/git/trees").mock(return_value=httpx.Response(201, json={"sha": "t-new"}))
    commit_post = respx.post(f"{API}/git/commits").mock(
        return_value=httpx.Response(201, json={"sha": "c-new"})
    )
    respx.patch(f"{API}/git/refs/heads/main").mock(return_value=httpx.Response(200, json={}))

    d.list_tree()
    d.write_files({"x.md": b"one"}, "msg")
    assert json.loads(commit_post.calls.last.request.content)["parents"] == ["c-base"]


@respx.mock
def test_list_tree_filters_prefix():
    respx.get(f"{API}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "c-base"}})
    )
    respx.get(f"{API}/git/commits/c-base").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "t-base"}})
    )
    respx.get(f"{API}/git/trees/t-base").mock(
        return_value=httpx.Response(
            200,
            json={"tree": [
                {"path": "data/x.md", "type": "blob", "sha": "s1"},
                {"path": "README.md", "type": "blob", "sha": "s2"},
                {"path": "data/sub", "type": "tree", "sha": "s3"},
            ]},
        )
    )
    assert dest().list_tree() == {"x.md": "s1"}


@respx.mock
def test_list_tree_truncated_fails_loudly():
    respx.get(f"{API}/git/ref/heads/main").mock(
        return_value=httpx.Response(200, json={"object": {"sha": "c-base"}})
    )
    respx.get(f"{API}/git/commits/c-base").mock(
        return_value=httpx.Response(200, json={"tree": {"sha": "t-base"}})
    )
    respx.get(f"{API}/git/trees/t-base").mock(
        return_value=httpx.Response(
            200,
            json={
                "truncated": True,
                "tree": [{"path": "data/x.md", "type": "blob", "sha": "s1"}],
            },
        )
    )
    with pytest.raises(GitHubDestinationError, match="truncated"):
        dest().list_tree()


def test_empty_path_prefix_rejected():
    with pytest.raises(ValueError):
        GitHubDestination(repo="example/backup-data", branch="main", path_prefix="", token="t")
    with pytest.raises(ValueError):
        GitHubDestination(repo="example/backup-data", branch="main", path_prefix="/", token="t")


@respx.mock
def test_read_file_decodes_base64():
    respx.get(f"{API}/contents/data/x.md").mock(
        return_value=httpx.Response(
            200, json={"content": base64.b64encode(b"hello").decode(), "encoding": "base64"}
        )
    )
    assert dest().read_file("x.md") == b"hello"
