from __future__ import annotations

from outline_backup.core.config import Settings

from .base import ComingSoonError, Destination


def get_destination(settings: Settings) -> Destination:
    if settings.dest_type == "github":
        from .github import GitHubDestination

        return GitHubDestination(
            repo=settings.dest_repo,
            branch=settings.dest_branch,
            path_prefix=settings.dest_path.strip("/"),
            token=settings.github_token,
        )
    if settings.dest_type in ("gcs", "s3"):
        raise ComingSoonError(f"{settings.dest_type} destination is coming soon — contributions welcome")
    raise ValueError(f"unknown destination type: {settings.dest_type}")
