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
