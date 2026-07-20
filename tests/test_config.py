from pathlib import Path

from outline_backup.core.config import Settings, load_settings


def test_env_loading(monkeypatch):
    monkeypatch.setenv("OUTLINE_URL", "https://wiki.example.com")
    monkeypatch.setenv("OUTLINE_API_TOKEN", "ol_tok")
    monkeypatch.setenv("DEST_REPO", "example/backup-data")
    s = Settings()
    assert s.outline_url == "https://wiki.example.com"
    assert s.dest_branch == "main"
    assert s.dest_path == "data"
    assert s.debounce_seconds == 60.0


def test_toml_loading(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'OUTLINE_URL = "https://wiki.example.com"\n'
        'OUTLINE_API_TOKEN = "ol_tok"\n'
        'DEST_REPO = "example/backup-data"\n'
        "DEBOUNCE_SECONDS = 5\n"
    )
    s = load_settings(cfg)
    assert s.dest_repo == "example/backup-data"
    assert s.debounce_seconds == 5.0
