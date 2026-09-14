from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _required(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise RuntimeError(f"Не задан {key} в .env")
    return val


def _int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    return int(raw) if raw else default


def _bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{key} должен быть true или false")


@dataclass(frozen=True)
class Config:
    tg_api_id: int
    tg_api_hash: str
    bot_token: str
    target_channel: str
    admin_user_id: int
    gemini_api_key: str
    gemini_model: str
    lookback_minutes: int
    interval_minutes: int
    max_posts_per_run: int
    telegram_reader_enabled: bool = False
    ai_provider: str = "app_server"
    app_server_url: str = "http://agent-codex:4202/ag-ui"
    app_server_token: str = ""
    app_server_model: str = "gpt-5.6-luna"
    app_server_reasoning_effort: str = "xhigh"
    ai_timeout_seconds: int = 240
    editor_url: str = "http://editor-gateway:8080/v2/edit"
    editor_token: str = ""

def load() -> Config:
    target = _required("TARGET_CHANNEL")
    if not target.startswith("@") and not target.startswith("-100"):
        target = "@" + target
    ai_provider = os.getenv("AI_PROVIDER", "app_server").strip().lower() or "app_server"
    if ai_provider not in {"app_server", "gemini"}:
        raise RuntimeError("AI_PROVIDER должен быть app_server или gemini")
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if ai_provider == "gemini" and not gemini_api_key:
        raise RuntimeError("Для AI_PROVIDER=gemini необходимо задать GEMINI_API_KEY")
    app_server_url = os.getenv(
        "APP_SERVER_URL", "http://agent-codex:4202/ag-ui",
    ).strip()
    if ai_provider == "app_server" and not app_server_url:
        raise RuntimeError("Для AI_PROVIDER=app_server необходимо задать APP_SERVER_URL")
    tg_api_id = _int("TG_API_ID", 0)
    tg_api_hash = os.getenv("TG_API_HASH", "").strip()
    telegram_reader_enabled = _bool("TELEGRAM_READER_ENABLED")
    if telegram_reader_enabled and not (tg_api_id and tg_api_hash):
        raise RuntimeError(
            "Для TELEGRAM_READER_ENABLED=true задайте TG_API_ID и TG_API_HASH"
        )
    return Config(
        tg_api_id=tg_api_id,
        tg_api_hash=tg_api_hash,
        bot_token=_required("BOT_TOKEN"),
        target_channel=target,
        admin_user_id=_int("ADMIN_USER_ID", 0),
        gemini_api_key=gemini_api_key,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
        lookback_minutes=_int("LOOKBACK_MINUTES", 45),
        interval_minutes=_int("INTERVAL_MINUTES", 30),
        max_posts_per_run=_int("MAX_POSTS_PER_RUN", 3),
        telegram_reader_enabled=telegram_reader_enabled,
        ai_provider=ai_provider,
        app_server_url=app_server_url,
        app_server_token=os.getenv("APP_SERVER_TOKEN", "").strip(),
        app_server_model=os.getenv("APP_SERVER_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna",
        app_server_reasoning_effort=(
            os.getenv("APP_SERVER_REASONING_EFFORT", "xhigh").strip() or "xhigh"
        ),
        ai_timeout_seconds=_int("AI_TIMEOUT_SECONDS", 240),
        editor_url=os.getenv(
            "EDITOR_URL", "http://editor-gateway:8080/v2/edit",
        ).strip(),
        editor_token=os.getenv("EDITOR_TOKEN", "").strip(),
    )
