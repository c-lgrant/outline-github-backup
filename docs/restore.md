# Restore runbook

Step-by-step guide for restoring a backup tree into a **fresh Outline instance**. This is the
break-glass path: your original Outline is gone (or you are migrating), and all you have is the
data repo this service has been committing to.

Time estimate: ~10 minutes of hands-on work, plus import time (roughly a minute per few hundred
documents, dominated by comment replay).

## What you need

1. **The backup data repo** — the private GitHub repo the service mirrors into
   (`DEST_REPO`). You need clone access; nothing else from the old instance is required.
2. **A running target Outline instance** — brand new or empty. Complete the first-login flow so
   a workspace (team) and your admin user exist.
3. **An API token from the target instance** — created by an **admin** user (imports require
   admin rights): target Outline → **Settings → API** → *New API key*. Copy it once; it is not
   shown again.
4. **This CLI installed** on any machine that can reach the target instance:

   ```bash
   git clone https://github.com/OWNER/outline-github-backup
   cd outline-github-backup
   pip install .
   ```

## Step 1 — clone the data repo

```bash
git clone https://github.com/OWNER/YOUR-DATA-REPO /tmp/outline-restore
```

The backup tree lives under the directory you configured as `DEST_PATH` (default `data/`).
Sanity-check it before restoring:

```bash
ls /tmp/outline-restore/data
# collections/   _manifest.json
python -c "import json; m=json.load(open('/tmp/outline-restore/data/_manifest.json')); \
print(len(m['collections']), 'collections,', len(m['documents']), 'documents')"
```

If the counts look wrong (e.g. far fewer documents than you expect), stop and check out an
earlier commit of the data repo — every backup commit is a usable restore point:

```bash
git -C /tmp/outline-restore log --oneline -20   # pick a commit
git -C /tmp/outline-restore checkout <sha>
```

## Step 2 — dry run

Always preview first. A dry run reads only the local tree — it does not touch the target.

```bash
outline-backup restore /tmp/outline-restore/data \
  --target-url https://your-new-outline.example.com \
  --target-token $TARGET_TOKEN \
  --dry-run
```

Expected output: one line per collection with its document count, plus a summary line. Verify
the collection names and counts match what you expect before continuing.

## Step 3 — restore

Same command without `--dry-run`:

```bash
outline-backup restore /tmp/outline-restore/data \
  --target-url https://your-new-outline.example.com \
  --target-token $TARGET_TOKEN
```

(If you omit `--target-token`, the CLI prompts for it with hidden input; it also reads the
`OUTLINE_TARGET_TOKEN` environment variable.)

For each collection the CLI:

1. rebuilds a MarkdownZip from the backup tree (nested documents become nested folders),
2. uploads it and triggers Outline's native collection import,
3. waits for the import operation to complete,
4. replays each document's comment threads in original order, preserving reply structure.

The final line reports totals:

```
Collections: 6 · documents matched: 328 · comments created: 41
```

## Step 4 — verify

- Open the target instance: every collection from the dry run should exist, with the same
  document hierarchy (parent/child nesting) as the original.
- Spot-check a few documents for content, and one with comments for the replayed thread.
- Compare `documents matched` against the dry-run total — they should be equal. Any gap is
  explained by a `warning:` line (see below).

## Reading the warnings

Warnings go to stderr and never abort the restore — the remaining collections, documents, and
comments still process.

| Warning | Meaning | What to do |
| --- | --- | --- |
| `no imported match for document: <title>` | The import succeeded but that title was not found in the imported collection. | Check the document manually; re-import that collection alone if needed. |
| `ambiguous title match, skipping comment replay for: <title>` | Two or more documents in the collection share a title; comments cannot be safely attached, so the documents are restored **without** their comments. | Attach the comments by hand from the `.comments.json` sidecar next to the document in the backup tree. |
| `comment <id>: parent missing, posted as top-level` | A reply's parent comment failed to restore; the reply was posted as a top-level comment instead. | Cosmetic — thread nesting lost for that comment only. |
| `comment <id> failed: <error>` | One comment could not be created. | The sidecar file still holds it; add it manually if it matters. |
| `imported collection not found by name: <name>` | The import finished but the collection could not be located afterwards. | Check the target's Settings → Import/Export page for the operation's status, then retry that collection. |

## Known limitations

- **Comment authorship**: all comments are created by your API token's user. The original
  author and timestamp are preserved as a text prefix: `(originally by Jane, 2026-01-15…)`.
- **Not restored**: users, groups, permissions, revision history, share links, API keys,
  document-level settings. See the README's "What restore can and cannot bring back".
- **Restore is all-or-nothing per tree**: every collection in the backup is imported. A
  `--collections` filter is planned. To restore a subset today, delete the unwanted collection
  directories *and* their entries in `_manifest.json` from a scratch copy of the tree.
- **Re-running restore duplicates content**: imports always create new collections; running the
  command twice gives you two copies. Restore into a clean instance, or delete the imported
  collections before retrying.

## Troubleshooting

- **HTTP 401** — the token is wrong, or belongs to a non-admin user.
- **HTTP 429 / rate limiting** — the CLI retries with backoff automatically; very large comment
  sets just take longer.
- **HTTP 403 from a proxy/WAF in front of the target** — some CDN/WAF setups block non-browser
  clients. Run the restore from a host with direct network access to the instance, or allowlist
  API traffic.
- **`import failed:` / `import timed out`** — Outline's import operation itself failed; check
  the target's server logs and Settings → Import/Export. Timeouts usually mean a very large
  collection; the operation may still complete server-side — verify before retrying.
