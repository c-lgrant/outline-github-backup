import tomllib

import httpx
import respx
from typer.testing import CliRunner

from outline_backup.cli.main import app

runner = CliRunner()
BASE = "https://wiki.example.com"


def init_args(tmp_path, extra=()):
    return [
        "init",
        "--outline-url", BASE,
        "--outline-token", "tok",
        "--dest-type", "github",
        "--dest-repo", "example/backup-data",
        "--github-token", "gh_tok",
        "--webhook-url", "https://backup.example.com",
        "--output", str(tmp_path / "config.toml"),
        *extra,
    ]


@respx.mock
def test_init_writes_config_and_creates_webhook(tmp_path):
    respx.get("https://api.github.com/repos/example/backup-data").mock(
        return_value=httpx.Response(200, json={"private": True})
    )
    webhook = respx.post(f"{BASE}/api/webhookSubscriptions.create").mock(
        return_value=httpx.Response(200, json={"data": {"id": "wh1"}})
    )
    result = runner.invoke(app, init_args(tmp_path))
    assert result.exit_code == 0, result.output
    cfg = tomllib.loads((tmp_path / "config.toml").read_text())
    assert cfg["DEST_REPO"] == "example/backup-data"
    assert len(cfg["OUTLINE_WEBHOOK_SECRET"]) == 64
    assert webhook.called
    import json

    sent = json.loads(webhook.calls.last.request.content)
    assert sent["url"] == "https://backup.example.com/webhook"
    assert sent["events"] == ["documents", "collections", "comments"]


@respx.mock
def test_init_warns_on_public_repo(tmp_path):
    respx.get("https://api.github.com/repos/example/backup-data").mock(
        return_value=httpx.Response(200, json={"private": False})
    )
    result = runner.invoke(app, init_args(tmp_path, extra=["--no-create-webhook"]))
    assert result.exit_code == 0
    assert "PUBLIC" in result.output


def test_init_rejects_coming_soon_destinations(tmp_path):
    args = [
        "init",
        "--outline-url", BASE,
        "--outline-token", "tok",
        "--dest-type", "gcs",
        "--dest-repo", "example/backup-data",
        "--github-token", "gh_tok",
        "--webhook-url", "https://backup.example.com",
        "--output", str(tmp_path / "config.toml"),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code != 0
    assert "coming soon" in result.output.lower()


def test_init_rejects_nested_dest_path(tmp_path):
    result = runner.invoke(app, init_args(tmp_path, extra=["--dest-path", "a/b"]))
    assert result.exit_code != 0
