import httpx
import respx
from typer.testing import CliRunner

from outline_backup.cli.main import app

runner = CliRunner()
BASE = "https://wiki.example.com"


def write_config(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'OUTLINE_URL = "{BASE}"\n'
        'OUTLINE_API_TOKEN = "tok"\n'
        'DEST_TYPE = "github"\n'
        'DEST_REPO = "example/backup-data"\n'
    )
    return cfg


def mock_workspace():
    col = {"id": "col1", "name": "Guides", "urlId": "Ab12"}
    doc = {"id": "doc1", "title": "Intro", "urlId": "Xy9", "collectionId": "col1",
           "parentDocumentId": None, "updatedAt": "2026-01-01T00:00:00Z"}
    respx.post(f"{BASE}/api/collections.list").mock(return_value=httpx.Response(200, json={"data": [col]}))
    respx.post(f"{BASE}/api/documents.list").mock(return_value=httpx.Response(200, json={"data": [doc]}))
    respx.post(f"{BASE}/api/documents.info").mock(return_value=httpx.Response(200, json={"data": doc}))
    respx.post(f"{BASE}/api/documents.export").mock(return_value=httpx.Response(200, json={"data": "# Intro\n"}))
    respx.post(f"{BASE}/api/collections.info").mock(return_value=httpx.Response(200, json={"data": col}))
    respx.post(f"{BASE}/api/comments.list").mock(return_value=httpx.Response(200, json={"data": []}))


@respx.mock
def test_export_writes_local_tree(tmp_path):
    cfg = write_config(tmp_path)
    mock_workspace()
    out = tmp_path / "out"
    result = runner.invoke(app, ["export", str(out), "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert (out / "collections/guides-Ab12/intro-Xy9.md").read_text() == "# Intro\n"
    assert (out / "_manifest.json").exists()


def test_export_requires_outline_url(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('OUTLINE_URL = ""\nOUTLINE_API_TOKEN = ""\n')
    result = runner.invoke(app, ["export", str(tmp_path / "out"), "--config", str(cfg)])
    assert result.exit_code != 0


class StubEngine:
    def __init__(self, record: dict):
        self.record = record

    def sync_all(self, message="backup: full sync", prune=False):
        self.record["prune"] = prune
        return 3


def test_backfill_prune_flag(tmp_path, monkeypatch):
    record: dict = {}

    def fake_engine(settings, dest=None, pace_seconds=0.0):
        record["pace"] = pace_seconds
        return StubEngine(record)

    monkeypatch.setattr("outline_backup.cli.main._engine", fake_engine)
    result = runner.invoke(app, ["backfill", "--prune", "--config", str(write_config(tmp_path))])
    assert result.exit_code == 0, result.output
    assert record["prune"] is True


def test_backfill_pace_comes_from_settings(tmp_path, monkeypatch):
    record: dict = {}

    def fake_engine(settings, dest=None, pace_seconds=0.0):
        record["pace"] = pace_seconds
        return StubEngine(record)

    monkeypatch.setattr("outline_backup.cli.main._engine", fake_engine)
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'OUTLINE_URL = "{BASE}"\n'
        'OUTLINE_API_TOKEN = "tok"\n'
        'DEST_TYPE = "github"\n'
        'DEST_REPO = "example/backup-data"\n'
        "BACKFILL_PACE_SECONDS = 0.1\n"
    )
    result = runner.invoke(app, ["backfill", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert record["pace"] == 0.1


def test_cli_restore_collections_flag(tmp_path, monkeypatch):
    from outline_backup.core.restore import RestoreReport

    recorded: dict = {}

    def fake_restore(client, source, *, dry_run=False, upload=None, collections=None):
        recorded["collections"] = collections
        return RestoreReport()

    monkeypatch.setattr("outline_backup.core.restore.restore", fake_restore)
    result = runner.invoke(app, [
        "restore", str(tmp_path), "--target-url", BASE, "--target-token", "t",
        "--collections", "Guides", "--collections", "Docs",
    ])
    assert result.exit_code == 0, result.output
    assert recorded["collections"] == ["Guides", "Docs"]
