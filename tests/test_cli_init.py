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


@respx.mock
def test_init_writes_config_owner_only(tmp_path):
    respx.get("https://api.github.com/repos/example/backup-data").mock(
        return_value=httpx.Response(200, json={"private": True})
    )
    respx.post(f"{BASE}/api/webhookSubscriptions.create").mock(
        return_value=httpx.Response(200, json={"data": {"id": "wh1"}})
    )
    output = tmp_path / "config.toml"
    result = runner.invoke(app, init_args(tmp_path))
    assert result.exit_code == 0, result.output
    mode = output.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)
    assert "owner-readable only" in result.output


@respx.mock
def test_init_warns_when_repo_visibility_check_fails(tmp_path):
    respx.get("https://api.github.com/repos/example/backup-data").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    result = runner.invoke(app, init_args(tmp_path, extra=["--no-create-webhook"]))
    assert result.exit_code == 0, result.output
    assert "could not verify" in result.output
    assert "example/backup-data" in result.output
    assert "404" in result.output


@respx.mock
def test_init_escapes_special_characters_in_toml(tmp_path):
    respx.get("https://api.github.com/repos/example/backup-data").mock(
        return_value=httpx.Response(200, json={"private": True})
    )
    output = tmp_path / "config.toml"
    args = init_args(tmp_path, extra=["--no-create-webhook"])
    # Replace the plain token with one containing TOML-breaking characters.
    args[args.index("tok")] = 'to"k\\en'
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    cfg = tomllib.loads(output.read_text())
    assert cfg["OUTLINE_API_TOKEN"] == 'to"k\\en'


@respx.mock
def test_init_resecures_existing_config(tmp_path):
    respx.get("https://api.github.com/repos/example/backup-data").mock(
        return_value=httpx.Response(200, json={"private": True})
    )
    output = tmp_path / "config.toml"
    # Pre-create file with world-readable permissions.
    output.write_text("stale")
    output.chmod(0o644)
    # Run init with --no-create-webhook.
    result = runner.invoke(app, init_args(tmp_path, extra=["--no-create-webhook"]))
    assert result.exit_code == 0, result.output
    # Verify permissions were fixed to 0o600.
    mode = output.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)
    # Verify file content was replaced.
    cfg = tomllib.loads(output.read_text())
    assert cfg["DEST_REPO"] == "example/backup-data"


@respx.mock
def test_init_also_writes_env_template(tmp_path):
    respx.get("https://api.github.com/repos/example/backup-data").mock(
        return_value=httpx.Response(200, json={"private": True})
    )
    output = tmp_path / "config.toml"
    result = runner.invoke(app, init_args(tmp_path, extra=["--no-create-webhook"]))
    assert result.exit_code == 0, result.output

    env_path = output.parent / ".env"
    assert env_path.exists()
    mode = env_path.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)

    env_text = env_path.read_text()
    lines = dict(line.split("=", 1) for line in env_text.splitlines() if line)
    assert set(lines) == {
        "OUTLINE_URL",
        "OUTLINE_API_TOKEN",
        "OUTLINE_WEBHOOK_SECRET",
        "DEST_TYPE",
        "DEST_REPO",
        "DEST_BRANCH",
        "DEST_PATH",
        "GITHUB_TOKEN",
        "DEBOUNCE_SECONDS",
        "INCLUDE_ATTACHMENTS",
    }
    secret = lines["OUTLINE_WEBHOOK_SECRET"]
    assert len(secret) == 64
    assert str(env_path) in result.output
