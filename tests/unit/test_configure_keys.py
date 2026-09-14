from __future__ import annotations

import stat

from dotenv import dotenv_values

from gildranews.configure_keys import main, update_env_file


def test_update_env_file_preserves_other_settings_and_hides_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BOT_TOKEN='existing'\nGETXAPI_KEY='old'\n", encoding="utf-8")

    update_env_file(
        env_file,
        {
            "GETXAPI_KEY": "new-secret",
            "REDDITAPIS_KEY": "reddit-secret",
            "SCRAPE_DO_TOKEN": "scrape-secret",
        },
    )

    values = dotenv_values(env_file)
    assert values["BOT_TOKEN"] == "existing"
    assert values["GETXAPI_KEY"] == "new-secret"
    assert values["REDDITAPIS_KEY"] == "reddit-secret"
    assert values["SCRAPE_DO_TOKEN"] == "scrape-secret"
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_update_env_file_rejects_multiline_secrets(tmp_path) -> None:
    env_file = tmp_path / ".env"

    try:
        update_env_file(env_file, {"GETXAPI_KEY": "first\nsecond"})
    except ValueError as exc:
        assert "one line" in str(exc)
    else:
        raise AssertionError("multiline secret was accepted")


def test_command_enables_paid_routes_without_echoing_keys(monkeypatch, tmp_path, capsys) -> None:
    env_file = tmp_path / ".env"
    answers = iter(("scrape-secret", "getx-secret", "reddit-secret"))
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))

    result = main(["--env-file", str(env_file), "--enable-paid"])

    assert result == 0
    values = dotenv_values(env_file)
    assert values["SCRAPE_DO_ENABLED"] == "true"
    assert values["GETXAPI_ENABLED"] == "true"
    assert values["REDDITAPIS_ENABLED"] == "true"
    assert "scrape-secret" not in capsys.readouterr().out
