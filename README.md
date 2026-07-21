# outline-github-backup

Break-glass backup for [Outline](https://www.getoutline.com). Point your instance's webhook at
this service and every document edit is mirrored — as human-readable markdown plus comment
threads — to a GitHub repo. If your Outline ever disappears, the bundled CLI restores your
content (documents, hierarchy, comment threads) into a fresh instance via the public API.

> ⚠️ **Use a private data repo.** Your backup inherits the visibility of the repo it lands in.

## How it works

- **Live mirror** — Outline webhook → this service → one commit per settled edit:
  `collections/<collection>/<doc>.md` + `<doc>.comments.json`, tracked in `_manifest.json`.
- **Backfill** — `outline-backup backfill` walks the whole workspace and commits anything missing.
- **Restore** — `outline-backup restore <clone-of-data-repo> --target-url … --target-token …`
  rebuilds each collection as a MarkdownZip, imports it, and replays comment threads
  (comments are re-created by your API user, prefixed with the original author and date).
  Pass `--dry-run` to preview the plan first — it prints the per-collection document counts
  without importing anything. Full step-by-step guide: [docs/restore.md](docs/restore.md).

## Quickstart

    pip install .                       # or: docker build -t outline-backup .
    outline-backup init                 # interactive: tokens, destination repo, webhook setup
                                         # writes both config.toml AND a matching .env
    outline-backup backfill --config config.toml
    uvicorn outline_backup.service.app:app --host 0.0.0.0 --port 8080
    # or: docker run --env-file .env -p 8080:8080 outline-backup

Expose the service at a public HTTPS URL and (if you skipped `init`'s auto-setup) add a webhook
in Outline → Settings → Webhooks pointing at `https://<your-host>/webhook` with the generated
signing secret, subscribed to document, collection, and comment events.

## Configuration

| Key | Default | Purpose |
| --- | --- | --- |
| `OUTLINE_URL` | — | Base URL of your instance |
| `OUTLINE_API_TOKEN` | — | API token used for exports/backfill |
| `OUTLINE_WEBHOOK_SECRET` | — | Signing secret for webhook verification |
| `DEST_TYPE` | `github` | Destination backend (`gcs`, `s3`: coming soon) |
| `DEST_REPO` | — | Data repo, `owner/name` — **make it private** |
| `DEST_BRANCH` | `main` | Branch to commit to |
| `DEST_PATH` | `data` | Root-level directory the backup lives under |
| `GITHUB_TOKEN` | — | Fine-grained PAT, contents read/write on the data repo only |
| `DEBOUNCE_SECONDS` | `60` | Per-document quiet period before committing |
| `INCLUDE_ATTACHMENTS` | `true` | Reserved: attachment mirroring is not yet implemented (planned) |

## What restore can and cannot bring back

Restores content: documents, hierarchy, collections, comment threads. Cannot restore: users,
permissions, revision history, share links, API keys, or original comment authorship/timestamps
(Outline's API cannot impersonate). For byte-identical disaster recovery, also back up your
Postgres database and file storage at the infrastructure level — this tool is the portable,
provider-independent content layer.

## Roadmap & known limitations

- `--backfill-on-start` service flag — planned. Today the service only reacts to webhooks after
  it starts; run `outline-backup backfill` yourself to catch up on anything missed while it
  was down.
- `restore --collections` filter — planned. Restore always rebuilds every collection in the
  backup tree; there is no way yet to restore a subset.
- Renames land as two commits on git destinations (one for the old path's removal, one for the
  new path), leaving a brief window where the tree looks inconsistent. A subsequent `backfill`
  heals this.
- `backfill` only adds and updates documents — it does not prune documents that were deleted
  upstream in Outline. Deletions are handled by the live webhook path, not by backfill.

## License

MIT
