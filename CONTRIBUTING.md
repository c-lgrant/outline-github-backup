# Contributing

Thanks for helping! Dev setup:

    python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
    .venv/bin/pytest && .venv/bin/ruff check .

## Adding a destination (GCS, S3, …)

Destinations implement the small protocol in `src/outline_backup/destinations/base.py`
(`write_files` / `delete_files` / `list_tree` / `read_file`) and register in
`src/outline_backup/destinations/__init__.py`. `LocalDestination` is the reference
implementation; `GitHubDestination` shows a remote one. Please include tests
(see `tests/test_github_destination.py` for the mocking pattern).

## Ground rules

- TDD: every change lands with tests.
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`).
- No real hostnames, tokens, or personal data in code, tests, or docs — use `*.example.com`.
