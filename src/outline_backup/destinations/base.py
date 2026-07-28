from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable


class ComingSoonError(NotImplementedError):
    pass


class DestinationConflictError(Exception):
    """The destination moved (non-fast-forward) since it was last read."""


def git_blob_sha(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


@runtime_checkable
class Destination(Protocol):
    def write_files(
        self, files: dict[str, bytes], message: str, deletions: list[str] | None = None
    ) -> None: ...
    def list_tree(self) -> dict[str, str]: ...
    def read_file(self, path: str) -> bytes: ...
