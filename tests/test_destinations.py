from pathlib import Path

import pytest

from outline_backup.core.config import Settings
from outline_backup.destinations import get_destination
from outline_backup.destinations.base import ComingSoonError, git_blob_sha
from outline_backup.destinations.local import LocalDestination


def test_git_blob_sha_matches_git():
    # printf 'hello\n' | git hash-object --stdin  → ce0136250...
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_local_destination_roundtrip(tmp_path: Path):
    dest = LocalDestination(tmp_path)
    dest.write_files({"a/b.md": b"# hi\n", "c.json": b"[]\n"}, "msg")
    tree = dest.list_tree()
    assert set(tree) == {"a/b.md", "c.json"}
    assert tree["a/b.md"] == git_blob_sha(b"# hi\n")
    assert dest.read_file("c.json") == b"[]\n"
    dest.write_files({"a/d.md": b"new\n"}, "msg", deletions=["c.json"])
    assert set(dest.list_tree()) == {"a/b.md", "a/d.md"}


def test_registry_coming_soon():
    with pytest.raises(ComingSoonError):
        get_destination(Settings(dest_type="gcs"))
    with pytest.raises(ValueError):
        get_destination(Settings(dest_type="ftp"))
