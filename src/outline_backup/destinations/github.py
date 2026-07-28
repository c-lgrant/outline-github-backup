"""GitHub destination: batched commits via the Git Data API, no local clone."""

from __future__ import annotations

import base64

import httpx


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

    def _head(self) -> tuple[str, str]:
        commit_sha = self._req("GET", f"{self._base}/git/ref/heads/{self.branch}").json()["object"]["sha"]
        tree_sha = self._req("GET", f"{self._base}/git/commits/{commit_sha}").json()["tree"]["sha"]
        return commit_sha, tree_sha

    def _commit_tree(self, entries: list[dict], message: str) -> None:
        last_error: Exception | None = None
        for _ in range(3):
            commit_sha, base_tree = self._head()
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
                return
            last_error = GitHubDestinationError(f"ref update failed: HTTP {resp.status_code}")
        raise last_error or GitHubDestinationError("ref update failed")

    # -- Destination protocol ---------------------------------------------
    def write_files(self, files: dict[str, bytes], message: str) -> None:
        if not files:
            return
        entries = []
        for rel, data in sorted(files.items()):
            blob = self._req(
                "POST",
                f"{self._base}/git/blobs",
                json={"content": base64.b64encode(data).decode(), "encoding": "base64"},
            ).json()["sha"]
            entries.append({"path": f"{self.prefix}/{rel}", "mode": "100644", "type": "blob", "sha": blob})
        self._commit_tree(entries, message)

    def delete_files(self, paths: list[str], message: str) -> None:
        if not paths:
            return
        entries = [
            {"path": f"{self.prefix}/{rel}", "mode": "100644", "type": "blob", "sha": None}
            for rel in sorted(paths)
        ]
        self._commit_tree(entries, message)

    def list_tree(self) -> dict[str, str]:
        _, tree_sha = self._head()
        listing = self._req(
            "GET", f"{self._base}/git/trees/{tree_sha}", params={"recursive": "1"}
        ).json()
        if listing.get("truncated"):
            raise GitHubDestinationError(
                "recursive tree listing was truncated by the GitHub API; "
                "refusing to diff against an incomplete tree (mirror too large "
                "for a single listing — split it across repos or path prefixes)"
            )
        items = listing["tree"]
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
