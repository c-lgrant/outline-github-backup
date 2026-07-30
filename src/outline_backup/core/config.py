from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    outline_url: str = ""
    outline_api_token: str = ""
    outline_webhook_secret: str = ""
    dest_type: str = "github"
    dest_repo: str = ""
    dest_branch: str = "main"
    dest_path: str = "data"
    github_token: str = ""
    debounce_seconds: float = 60.0
    include_attachments: bool = True
    backfill_on_start: bool = False
    # Rate-limit knobs for self-hosters with stricter Outline API limits.
    max_429_retries: int = 5
    max_retry_after_seconds: float = 60.0
    backfill_pace_seconds: float = 0.5


def load_settings(config_file: Path | None = None) -> Settings:
    if config_file is not None and config_file.exists():
        data = tomllib.loads(config_file.read_text())
        return Settings(**{k.lower(): v for k, v in data.items()})
    return Settings()
