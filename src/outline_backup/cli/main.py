"""outline-backup CLI: init, backfill, export, restore."""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer

from outline_backup.core.config import Settings, load_settings
from outline_backup.core.outline_client import OutlineClient
from outline_backup.core.sync import SyncEngine
from outline_backup.destinations.local import LocalDestination

app = typer.Typer(help="Break-glass backup for Outline: mirror, export, restore.")

CONFIG_OPT = typer.Option(None, "--config", help="Path to config.toml (defaults to env vars)")


def _settings(config: Path | None) -> Settings:
    settings = load_settings(config)
    if not settings.outline_url or not settings.outline_api_token:
        typer.echo("OUTLINE_URL and OUTLINE_API_TOKEN are required (env or --config).", err=True)
        raise typer.Exit(code=2)
    return settings


# Pace between documents on full-workspace walks (backfill/export) so we don't
# hammer the Outline API and trip rate limits; single-doc syncs don't need it.
BACKFILL_PACE_SECONDS = 0.5


def _engine(settings: Settings, dest=None, pace_seconds: float = 0.0) -> SyncEngine:
    if dest is None:
        from outline_backup.destinations import get_destination

        dest = get_destination(settings)
    return SyncEngine(
        OutlineClient(settings.outline_url, settings.outline_api_token), dest, pace_seconds=pace_seconds
    )


@app.command()
def backfill(config: Path = CONFIG_OPT) -> None:
    """Walk the whole workspace and commit anything missing or changed."""
    changed = _engine(_settings(config), pace_seconds=BACKFILL_PACE_SECONDS).sync_all()
    typer.echo(f"Synced {changed} changed file(s).")


@app.command()
def export(output_dir: Path, config: Path = CONFIG_OPT) -> None:
    """Snapshot the whole workspace to a local directory."""
    changed = _engine(
        _settings(config), dest=LocalDestination(output_dir), pace_seconds=BACKFILL_PACE_SECONDS
    ).sync_all()
    typer.echo(f"Exported {changed} file(s) to {output_dir}.")


@app.command()
def restore(
    source_dir: Path,
    target_url: str = typer.Option(..., "--target-url"),
    target_token: str = typer.Option(
        ...,
        "--target-token",
        envvar="OUTLINE_TARGET_TOKEN",
        prompt="Target Outline API token",
        hide_input=True,
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Restore a backup tree (local clone/export) into a fresh Outline instance."""
    from outline_backup.core.restore import restore as run_restore

    report = run_restore(
        OutlineClient(target_url, target_token), LocalDestination(source_dir), dry_run=dry_run
    )
    typer.echo(
        f"Collections: {report.collections} · documents matched: {report.documents_matched}"
        f" · comments created: {report.comments_created}"
    )
    if dry_run:
        for name, count in report.collection_files.items():
            typer.echo(f"{name}: {count} document(s)")
    for w in report.warnings:
        typer.echo(f"warning: {w}", err=True)


@app.command()
def init(
    outline_url: str = typer.Option(..., "--outline-url", prompt="Outline URL"),
    outline_token: str = typer.Option(..., "--outline-token", prompt="Outline API token", hide_input=True),
    dest_type: str = typer.Option(
        "github",
        "--dest-type",
        prompt="Destination type [github; gcs/s3 coming soon]",
        prompt_required=False,
    ),
    dest_repo: str = typer.Option(..., "--dest-repo", prompt="Destination GitHub repo (owner/name)"),
    dest_branch: str = typer.Option("main", "--dest-branch"),
    dest_path: str = typer.Option(
        "data",
        "--dest-path",
        prompt="Path inside the repo (root-level dir)",
        prompt_required=False,
    ),
    github_token: str = typer.Option(..., "--github-token", prompt="GitHub token", hide_input=True),
    create_webhook: bool = typer.Option(True, "--create-webhook/--no-create-webhook"),
    webhook_url: str = typer.Option("", "--webhook-url", help="Public base URL of this service"),
    output: Path = typer.Option(Path("config.toml"), "--output"),
) -> None:
    """Interactive setup: config file, repo-visibility check, optional webhook creation."""
    import secrets as pysecrets

    import httpx as _httpx

    if dest_type != "github":
        typer.echo(f"Destination '{dest_type}' is coming soon — only 'github' is available today.", err=True)
        raise typer.Exit(code=2)
    if "/" in dest_path.strip("/") or not dest_path.strip("/"):
        typer.echo("--dest-path must be a single root-level directory, e.g. 'data'.", err=True)
        raise typer.Exit(code=2)
    dest_path = dest_path.strip("/")

    resp = _httpx.get(
        f"https://api.github.com/repos/{dest_repo}",
        headers={"Authorization": f"Bearer {github_token}"},
    )
    if resp.status_code == 200:
        if resp.json().get("private") is False:
            typer.echo(
                f"WARNING: {dest_repo} is PUBLIC — everything backed up there will be public too. "
                "Use a private repo unless that is what you want."
            )
    else:
        typer.echo(
            f"WARNING: could not verify {dest_repo} (HTTP {resp.status_code}) — "
            "check the repo name and token.",
            err=True,
        )

    secret = pysecrets.token_hex(32)
    if create_webhook:
        if not webhook_url:
            typer.echo("--webhook-url is required with --create-webhook.", err=True)
            raise typer.Exit(code=2)
        OutlineClient(outline_url, outline_token).create_webhook(
            name="outline-github-backup",
            url=f"{webhook_url.rstrip('/')}/webhook",
            secret=secret,
            events=["documents", "collections", "comments"],
        )
        typer.echo("Webhook subscription created.")

    config_text = (
        f"OUTLINE_URL = {json.dumps(outline_url)}\n"
        f"OUTLINE_API_TOKEN = {json.dumps(outline_token)}\n"
        f"OUTLINE_WEBHOOK_SECRET = {json.dumps(secret)}\n"
        f"DEST_TYPE = {json.dumps(dest_type)}\n"
        f"DEST_REPO = {json.dumps(dest_repo)}\n"
        f"DEST_BRANCH = {json.dumps(dest_branch)}\n"
        f"DEST_PATH = {json.dumps(dest_path)}\n"
        f"GITHUB_TOKEN = {json.dumps(github_token)}\n"
        "DEBOUNCE_SECONDS = 60\n"
        "INCLUDE_ATTACHMENTS = true\n"
    )
    fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(config_text)
    typer.echo(f"Wrote {output}. Run the service with these values as env vars, or pass --config.")
    typer.echo("Config contains live credentials — kept owner-readable only (0600).")

    env_path = output.parent / ".env"
    env_text = (
        f"OUTLINE_URL={outline_url}\n"
        f"OUTLINE_API_TOKEN={outline_token}\n"
        f"OUTLINE_WEBHOOK_SECRET={secret}\n"
        f"DEST_TYPE={dest_type}\n"
        f"DEST_REPO={dest_repo}\n"
        f"DEST_BRANCH={dest_branch}\n"
        f"DEST_PATH={dest_path}\n"
        f"GITHUB_TOKEN={github_token}\n"
        "DEBOUNCE_SECONDS=60\n"
        "INCLUDE_ATTACHMENTS=true\n"
    )
    env_fd = os.open(str(env_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(env_fd, 0o600)
    with os.fdopen(env_fd, "w") as f:
        f.write(env_text)
    typer.echo(f"Wrote {env_path}. Use it with --env-file for docker run or local dev.")


if __name__ == "__main__":
    app()
