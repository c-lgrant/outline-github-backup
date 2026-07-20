from __future__ import annotations

from pathlib import Path

from .base import git_blob_sha


class LocalDestination:
    """Writes the backup tree to a local directory (used by `export` and tests)."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def write_files(self, files: dict[str, bytes], message: str) -> None:
        for rel, data in files.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def delete_files(self, paths: list[str], message: str) -> None:
        for rel in paths:
            target = self.root / rel
            if target.exists():
                target.unlink()

    def list_tree(self) -> dict[str, str]:
        return {
            str(p.relative_to(self.root)): git_blob_sha(p.read_bytes())
            for p in sorted(self.root.rglob("*"))
            if p.is_file()
        }

    def read_file(self, path: str) -> bytes:
        return (self.root / path).read_bytes()
