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


def load() -> Config:
    target = _required("TARGET_CHANNEL")
    if not target.startswith("@") and not target.startswith("-100"):
        target = "@" + target
    return Config(
        tg_api_id=int(_required("TG_API_ID")),
        tg_api_hash=_required("TG_API_HASH"),
        bot_token=_required("BOT_TOKEN"),
        target_channel=target,
        admin_user_id=_int("ADMIN_USER_ID", 0),
        gemini_api_key=_required("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
        lookback_minutes=_int("LOOKBACK_MINUTES", 45),
        interval_minutes=_int("INTERVAL_MINUTES", 30),
        max_posts_per_run=_int("MAX_POSTS_PER_RUN", 3),
    )
