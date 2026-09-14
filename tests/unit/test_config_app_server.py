from __future__ import annotations

from gildranews import config


def test_load_reads_app_server_token_from_mounted_file(monkeypatch, tmp_path) -> None:
    token_file = tmp_path / "app_server_token"
    token_file.write_text("internal-bridge-token", encoding="utf-8")
    monkeypatch.setenv("BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TARGET_CHANNEL", "@channel")
    monkeypatch.delenv("APP_SERVER_TOKEN", raising=False)
    monkeypatch.setenv("APP_SERVER_TOKEN_FILE", str(token_file))

    cfg = config.load()

    assert cfg.app_server_token == "internal-bridge-token"
