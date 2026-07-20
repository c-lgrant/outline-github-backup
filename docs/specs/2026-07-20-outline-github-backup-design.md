# outline-github-backup — Design Spec

**Date:** 2026-07-20 · **Status:** Draft for review

## Overview

A standalone, open-source, break-glass backup service for [Outline](https://www.getoutline.com). Any Outline instance that points a webhook at it gets a live, human-readable mirror of its documents (plus comment threads and structure) committed to a git destination. A bundled CLI performs first-run backfill, manual exports, and — the break-glass case — restore of content into a fresh Outline instance via the public API, even when the original instance is gone.

Not a disaster-recovery clone: users, permissions, revision history, share links, and original comment authorship live only in Outline's database and are explicitly out of scope (documented limitation).

## Goals

- Live backup: every document edit lands in the destination within ~1 minute of the edit settling.
- Instance-agnostic: zero coupling to any specific deployment; configured entirely by URL + tokens.
- Break-glass restore: `import` rebuilds documents, hierarchy, and comment threads on any Outline instance using only the backed-up data.
- Community-usable: public repo, MIT license, Docker image, clear README, pluggable destination interface.

## Non-goals

- Database-level (identical) backup/restore — pg_dump territory, out of scope.
- Restoring users, permissions, revisions, share links, API keys, or comment authorship.
- Non-GitHub destinations at v1 (interface exists; GCS/S3 marked "coming soon").

## Repos

- **`outline-github-backup`** (public, MIT) — all code: service, CLI, docs, Dockerfile, CI.
- **Data destination** (user-supplied, typically private, e.g. `<owner>/outline-backup-data`) — where backups land. The README warns prominently: pointing the destination at a public repo (including this one) makes your backup public.

## Architecture

One Python package, three faces sharing a core library:

- **Core (`outline_backup/core`)** — Outline API client (httpx), signature verification, markdown/comment serialization, path mapping, destination interface.
- **Service (`outline_backup/service`)** — FastAPI webhook receiver. Stateless; deployable to any container host (Cloud Run, Fly.io, a VPS, …).
- **CLI (`outline_backup/cli`)** — Typer app: `init`, `backfill`, `export`, `import`.

### Destination interface

```python
class Destination(Protocol):
    def write_files(self, files: dict[str, bytes], message: str) -> None: ...
    def delete_files(self, paths: list[str], message: str) -> None: ...
    def read_tree(self) -> dict[str, bytes]: ...   # used by import/backfill diffing
```

- **`github`** (v1): commits via the GitHub Contents/Git Data API — no local clone, safe for stateless/scale-to-zero hosts. Config: `repo`, `branch` (default `main`), `path` — a single root-level directory, default `data/`.
- **`gcs`, `s3`**: registered names that raise `ComingSoonError` with a friendly message; interface is the contribution point for the community.

## Configuration

Environment variables (service) / config file `~/.config/outline-backup/config.toml` (CLI), identical keys:

| Key | Purpose |
|---|---|
| `OUTLINE_URL` | Base URL of the instance |
| `OUTLINE_API_TOKEN` | API token (member+; admin only needed for webhook auto-setup) |
| `OUTLINE_WEBHOOK_SECRET` | Signing secret for `Outline-Signature` verification |
| `DEST_TYPE` / `DEST_REPO` / `DEST_BRANCH` / `DEST_PATH` | Destination selection (`github` · `owner/repo` · `main` · `data/`) |
| `GITHUB_TOKEN` | Fine-grained PAT, contents:write on the data repo only |
| `DEBOUNCE_SECONDS` | Per-document commit debounce (default 60) |
| `INCLUDE_ATTACHMENTS` | Mirror attachments into the destination (default true) |

### `init` command

Interactive setup: prompts for Outline URL + token, destination type (menu shows `github` plus `gcs (coming soon)`, `s3 (coming soon)`), repo/branch/path (path constrained to one root-level dir, default `data/`). Checks destination repo visibility via the GitHub API and **warns loudly if it is public**. Optionally creates the webhook subscription via `webhookSubscriptions.create` (all `documents.*`, `collections.*`, `comments.*` events) and prints the generated signing secret. Writes the config file and an `.env` template for the service.

## Data layout (in the destination, under `DEST_PATH`)

```
data/
  _manifest.json                  # id↔path map, collection tree, doc metadata, schema version
  collections/
    <collection-slug>/
      <doc-slug>.md               # documents.export markdown
      <doc-slug>.comments.json    # comments.list snapshot: body, author name, createdAt, parentCommentId
      <child-doc-slug>/...        # nesting mirrors Outline's doc tree
  attachments/<attachment-id>/<filename>
```

Slugs derive from titles with the document's short urlId suffix for uniqueness; `_manifest.json` is the source of truth for id↔path so renames/moves are handled as git renames.

## Flows

### Live (service)

1. `POST /webhook`: verify HMAC-SHA256 of `timestamp.body` against `OUTLINE_WEBHOOK_SECRET` (reject 401 on mismatch, replay window 5 min) → enqueue → return `200` immediately (Outline's 5 s deadline).
2. Background worker debounces per document id (`DEBOUNCE_SECONDS`), then re-fetches authoritative state (`documents.info` + `documents.export` + `comments.list`) rather than trusting the event payload.
3. Serialize → content-hash compare against destination → commit only real changes. Commit message: `<event>: "<doc title>" (by <actor name>)`.
4. Event mapping: `documents.delete/archive` → delete/move files + manifest update · `documents.move` / `collections.*` → manifest + git renames · `comments.*` → sidecar rewrite for that document.
5. `GET /health` for monitors. Handler failures log loudly but still `200` (Outline auto-disables the webhook after 25 consecutive failures — a lost webhook is worse than a lost event; `backfill` heals gaps).

### `backfill` (CLI or `service --backfill-on-start`)

Walk `collections.list` → `documents.list` (paginated) → export each doc + comments → diff against destination tree → one batched commit. Uses member-level endpoints only (avoids admin-only `collections.export_all` and its 5/hr limit). Also the drift-repair tool.

### `export` (CLI)

Same walk, written to a local directory or zip instead of the destination. For ad-hoc manual snapshots.

### `import` (CLI — break-glass restore)

1. Read tree from destination (or local dir).
2. Rebuild an Outline **MarkdownZip** per collection from the markdown tree.
3. Target instance: `attachments.create` (upload zip) → `collections.import` (`format: outline-markdown`).
4. Poll the resulting fileOperation until complete; map new document ids by title/path.
5. Replay comments via `comments.create` in thread order (`parentCommentId`), each body prefixed `*(originally by <name>, <date>)*` since the API cannot impersonate authors.
6. `--dry-run` prints the plan; `--collections` filters scope.

## Error handling

- Webhook signature invalid → 401, logged. Malformed body → 400.
- Outline/GitHub API failures in the worker: retry with exponential backoff (3 attempts); on final failure, log at ERROR with the document id — never crash the receiver.
- GitHub Contents API race (409 on concurrent commit): serialize commits through a single worker queue; retry on 409 with fresh parent SHA.
- Rate limits: worker paces to well under GitHub's 5k/hr; Outline calls are per-event and negligible. Backfill paginates politely (100/page, small delay).
- Idempotency everywhere: re-delivered events and re-runs of backfill produce zero diff, zero commits.

## Security

- Secrets only via env/config file; never logged. Fine-grained PAT scoped to the single data repo.
- Signature verification mandatory (no unauthenticated mode).
- `init` public-repo warning (see above).
- No inbound auth beyond the signature — the endpoint is safe to expose publicly.

## Testing

- Unit: signature verify (valid/invalid/replayed), slug/path mapping, manifest updates, MarkdownZip building, comment thread ordering.
- Integration: mock Outline API (respx) + mock GitHub API — full live-flow and import-flow tests.
- Smoke: `docker compose` recipe in README (service + instructions against a real instance).
- CI: GitHub Actions — ruff + pytest on PR; image build + push to GHCR on tag.

## Community / repo hygiene

MIT license · README with 5-minute quickstart (docker run + init) · `CONTRIBUTING.md` pointing at the Destination interface as the extension seam · issue templates kept minimal.

## Known limitations (documented in README)

Restore recreates content, hierarchy, attachments, and comment threads — not users, permissions, revision history, share links, sessions, or comment authorship/timestamps. For an identical clone of an instance, back up Postgres + file storage at the infrastructure level; this tool is the portable, provider-independent content layer.
