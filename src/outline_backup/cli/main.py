"""outline-backup CLI: init, backfill, export, restore."""

from __future__ import annotations

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


def _engine(settings: Settings, dest=None) -> SyncEngine:
    if dest is None:
        from outline_backup.destinations import get_destination

        dest = get_destination(settings)
    return SyncEngine(OutlineClient(settings.outline_url, settings.outline_api_token), dest)


@app.command()
def backfill(config: Path = CONFIG_OPT) -> None:
    """Walk the whole workspace and commit anything missing or changed."""
    changed = _engine(_settings(config)).sync_all()
    typer.echo(f"Synced {changed} changed file(s).")


@app.command()
def export(output_dir: Path, config: Path = CONFIG_OPT) -> None:
    """Snapshot the whole workspace to a local directory."""
    changed = _engine(_settings(config), dest=LocalDestination(output_dir)).sync_all()
    typer.echo(f"Exported {changed} file(s) to {output_dir}.")


@app.command()
def restore(
    source_dir: Path,
    target_url: str = typer.Option(..., "--target-url"),
    target_token: str = typer.Option(..., "--target-token"),
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


if __name__ == "__main__":
    app()
