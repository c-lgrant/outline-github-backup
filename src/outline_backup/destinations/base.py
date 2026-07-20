from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable


class ComingSoonError(NotImplementedError):
    pass


def git_blob_sha(data: bytes) -> str:
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


@runtime_checkable
class Destination(Protocol):
    def write_files(self, files: dict[str, bytes], message: str) -> None: ...
    def delete_files(self, paths: list[str], message: str) -> None: ...
    def list_tree(self) -> dict[str, str]: ...
    def read_file(self, path: str) -> bytes: ...
