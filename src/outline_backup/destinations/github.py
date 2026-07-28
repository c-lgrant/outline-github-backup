"""GitHub destination: batched commits via the Git Data API, no local clone."""

from __future__ import annotations

import base64

import httpx

from .base import DestinationConflictError


class GitHubDestinationError(Exception):
    pass


class GitHubDestination:
    def __init__(
        self,
        repo: str,
        branch: str,
        path_prefix: str,
        token: str,
        http: httpx.Client | None = None,
        api_base: str = "https://api.github.com",
    ):
        self.repo = repo
        self.branch = branch
        self.prefix = path_prefix.strip("/")
        if not self.prefix:
            raise ValueError("path_prefix must be a non-empty root-level directory, e.g. 'data'")
        self._http = http or httpx.Client(timeout=30.0)
        self._base = f"{api_base}/repos/{repo}"
        self._seen_head: str | None = None
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

    # -- internals --------------------------------------------------------
    def _req(self, method: str, url: str, ok: tuple[int, ...] = (200, 201), **kw) -> httpx.Response:
        resp = self._http.request(method, url, headers=self._headers, **kw)
        if resp.status_code not in ok:
            raise GitHubDestinationError(f"{method} {url}: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    def _read_head(self) -> str:
        return self._req("GET", f"{self._base}/git/ref/heads/{self.branch}").json()["object"]["sha"]

    def _commit_tree(self, entries: list[dict], message: str) -> None:
        # Commit against the head observed at list_tree() time. If another
        # writer moved the ref since, the update fails non-fast-forward and
        # we surface a conflict so the caller recomputes from fresh state —
        # blindly rebasing here would clobber the other writer's manifest.
        commit_sha = self._seen_head or self._read_head()
        base_tree = self._req("GET", f"{self._base}/git/commits/{commit_sha}").json()["tree"]["sha"]
        tree = self._req(
            "POST", f"{self._base}/git/trees", json={"base_tree": base_tree, "tree": entries}
        ).json()["sha"]
        new_commit = self._req(
            "POST",
            f"{self._base}/git/commits",
            json={"message": message, "tree": tree, "parents": [commit_sha]},
        ).json()["sha"]
        resp = self._http.request(
            "PATCH",
            f"{self._base}/git/refs/heads/{self.branch}",
            headers=self._headers,
            json={"sha": new_commit},
        )
        if resp.status_code == 200:
            self._seen_head = new_commit
            return
        self._seen_head = None
        if resp.status_code in (409, 422):
            raise DestinationConflictError(f"ref update failed: HTTP {resp.status_code}")
        raise GitHubDestinationError(f"ref update failed: HTTP {resp.status_code}")

    # -- Destination protocol ---------------------------------------------
    def write_files(
        self, files: dict[str, bytes], message: str, deletions: list[str] | None = None
    ) -> None:
        if not files and not deletions:
            return
        entries: list[dict] = [
            {"path": f"{self.prefix}/{rel}", "mode": "100644", "type": "blob", "sha": None}
            for rel in sorted(deletions or [])
        ]
        for rel, data in sorted(files.items()):
            blob = self._req(
                "POST",
                f"{self._base}/git/blobs",
                json={"content": base64.b64encode(data).decode(), "encoding": "base64"},
            ).json()["sha"]
            entries.append({"path": f"{self.prefix}/{rel}", "mode": "100644", "type": "blob", "sha": blob})
        self._commit_tree(entries, message)

    def list_tree(self) -> dict[str, str]:
        commit_sha = self._read_head()
        tree_sha = self._req("GET", f"{self._base}/git/commits/{commit_sha}").json()["tree"]["sha"]
        self._seen_head = commit_sha
        items = self._req(
            "GET", f"{self._base}/git/trees/{tree_sha}", params={"recursive": "1"}
        ).json()["tree"]
        prefix = f"{self.prefix}/"
        return {
            item["path"].removeprefix(prefix): item["sha"]
            for item in items
            if item["type"] == "blob" and item["path"].startswith(prefix)
        }

    def read_file(self, path: str) -> bytes:
        data = self._req(
            "GET", f"{self._base}/contents/{self.prefix}/{path}", params={"ref": self.branch}
        ).json()
        return base64.b64decode(data["content"])
