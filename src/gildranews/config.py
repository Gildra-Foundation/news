from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_RSS_FEED_URL = "https://www.wowhead.com/news/rss/all"
ICY_VEINS_RSS_FEED_URL = "https://wp-prod.icy-veins.com/custom-rss/?category=wow"
DEFAULT_RSS_FEED_URLS = (DEFAULT_RSS_FEED_URL, ICY_VEINS_RSS_FEED_URL)
DEFAULT_REDDIT_SUBREDDITS = ("wow", "competitivewow", "wownoob")
DEFAULT_X_SEARCH_QUERY = (
    '"World of Warcraft" OR Warcraft lang:en min_faves:20 '
    "-filter:replies -filter:retweets"
)
_SUBREDDIT_RE = re.compile(r"[A-Za-z0-9_]{2,32}\Z")


def _required(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise RuntimeError(f"Не задан {key} в .env")
    return val


def _int(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    return int(raw) if raw else default


def _bounded_int(key: str, default: int, *, minimum: int, maximum: int) -> int:
    value = _int(key, default)
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{key} должен быть от {minimum} до {maximum}")
    return value


def _bounded_float(key: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.getenv(key, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{key} должен быть числом") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{key} должен быть от {minimum} до {maximum}")
    return value


def _bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{key} должен быть true или false")


def _csv(key: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    return tuple(value.strip() for value in raw.split(",") if value.strip())


def _hours(key: str, default: tuple[int, ...]) -> tuple[int, ...]:
    values = _csv(key, tuple(str(hour) for hour in default))
    try:
        hours = tuple(dict.fromkeys(int(value) for value in values))
    except ValueError as exc:
        raise RuntimeError(f"{key} должен содержать часы от 0 до 23") from exc
    if not hours or len(hours) > 4 or any(not 0 <= hour <= 23 for hour in hours):
        raise RuntimeError(f"{key} должен содержать от 1 до 4 часов от 0 до 23")
    return hours


def _date(key: str, default: date) -> date:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} должен быть датой YYYY-MM-DD") from exc


def _secret(key: str, file_key: str, default_file: str) -> str:
    direct = os.getenv(key, "").strip()
    if direct:
        return direct
    path = os.getenv(file_key, default_file).strip()
    if not path:
        return ""
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""
    if not value or len(value) > 4_096 or "\n" in value or "\r" in value:
        raise RuntimeError(f"Некорректный секрет в {file_key}")
    return value


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
    mtproto_publisher_enabled: bool = False
    rss_enabled: bool = True
    rss_feed_urls: tuple[str, ...] = DEFAULT_RSS_FEED_URLS
    ai_provider: str = "app_server"
    app_server_url: str = "http://agent-codex:4202/ag-ui"
    app_server_token: str = ""
    app_server_model: str = "gpt-5.6-luna"
    app_server_reasoning_effort: str = "xhigh"
    ai_timeout_seconds: int = 240
    news_selector_enabled: bool = False
    openrouter_api_key: str = ""
    news_selector_model: str = "typesafe/jev-1.13"
    news_selector_shadow_mode: bool = True
    news_selector_min_reject_confidence: float = 0.8
    news_selector_timeout_seconds: int = 20
    editor_url: str = "http://editor-gateway:8080/v2/edit"
    editor_token: str = ""
    dedup_context_hours: int = 48
    dedup_context_limit: int = 50
    reddit_enabled: bool = False
    reddit_api_key: str = ""
    reddit_subreddits: tuple[str, ...] = DEFAULT_REDDIT_SUBREDDITS
    social_discovery_hours_utc: tuple[int, ...] = (8, 18)
    reddit_candidates_per_subreddit: int = 15
    reddit_max_posts_per_run: int = 1
    reddit_max_posts_per_day: int = 2
    x_enabled: bool = False
    x_api_key: str = ""
    x_search_query: str = DEFAULT_X_SEARCH_QUERY
    x_max_posts_per_run: int = 1
    x_max_posts_per_day: int = 2
    emoji_autocreate_enabled: bool = False
    emoji_set_prefix: str = "gildra_warcraft"
    emoji_icon_dir: str = "data/emoji_icons"
    emoji_max_new_per_day: int = 10
    emoji_upload_timeout_seconds: int = 15
    scrape_do_enabled: bool = False
    subscribe_emoji_id: str = ""
    forever_guide_message_id: int = 0
    forever_guide_refresh_minutes: int = 30
    forever_release_date: date = date(2026, 11, 4)

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
    mtproto_publisher_enabled = _bool("MTPROTO_PUBLISHER_ENABLED")
    if (telegram_reader_enabled or mtproto_publisher_enabled) and not (
        tg_api_id and tg_api_hash
    ):
        raise RuntimeError(
            "Для Telegram MTProto задайте TG_API_ID и TG_API_HASH"
        )
    rss_enabled = _bool("RSS_ENABLED", True)
    rss_feed_urls = _csv("RSS_FEED_URLS", DEFAULT_RSS_FEED_URLS)
    if rss_enabled and not rss_feed_urls:
        raise RuntimeError("Для RSS_ENABLED=true задайте хотя бы один RSS_FEED_URLS")
    reddit_enabled = _bool("REDDITAPIS_ENABLED")
    reddit_api_key = os.getenv("REDDITAPIS_KEY", "").strip()
    reddit_subreddits = tuple(
        value.removeprefix("r/").strip()
        for value in _csv("REDDIT_SUBREDDITS", DEFAULT_REDDIT_SUBREDDITS)
        if value.removeprefix("r/").strip()
    )
    if reddit_enabled and not reddit_api_key:
        raise RuntimeError("Для REDDITAPIS_ENABLED=true задайте REDDITAPIS_KEY")
    if reddit_enabled and not reddit_subreddits:
        raise RuntimeError("Для REDDITAPIS_ENABLED=true задайте REDDIT_SUBREDDITS")
    if any(not _SUBREDDIT_RE.fullmatch(value) for value in reddit_subreddits):
        raise RuntimeError("REDDIT_SUBREDDITS содержит некорректное имя сообщества")
    x_enabled = _bool("GETXAPI_ENABLED")
    x_api_key = os.getenv("GETXAPI_KEY", "").strip()
    x_search_query = os.getenv("X_SEARCH_QUERY", DEFAULT_X_SEARCH_QUERY).strip()
    if x_enabled and not x_api_key:
        raise RuntimeError("Для GETXAPI_ENABLED=true задайте GETXAPI_KEY")
    if not x_search_query or len(x_search_query) > 400:
        raise RuntimeError("X_SEARCH_QUERY должен содержать от 1 до 400 символов")
    scrape_do_enabled = _bool("SCRAPE_DO_ENABLED")
    if scrape_do_enabled and not os.getenv("SCRAPE_DO_TOKEN", "").strip():
        raise RuntimeError("Для SCRAPE_DO_ENABLED=true задайте SCRAPE_DO_TOKEN")
    news_selector_enabled = _bool("NEWS_SELECTOR_ENABLED")
    openrouter_api_key = _secret(
        "OPENROUTER_API_KEY",
        "OPENROUTER_API_KEY_FILE",
        "/app/data/openrouter_api_key",
    )
    if news_selector_enabled and not openrouter_api_key:
        raise RuntimeError(
            "Для NEWS_SELECTOR_ENABLED=true необходимо задать OPENROUTER_API_KEY"
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
        mtproto_publisher_enabled=mtproto_publisher_enabled,
        rss_enabled=rss_enabled,
        rss_feed_urls=rss_feed_urls,
        ai_provider=ai_provider,
        app_server_url=app_server_url,
        app_server_token=_secret(
            "APP_SERVER_TOKEN",
            "APP_SERVER_TOKEN_FILE",
            "/app/data/app_server_token",
        ),
        app_server_model=os.getenv("APP_SERVER_MODEL", "gpt-5.6-luna").strip() or "gpt-5.6-luna",
        app_server_reasoning_effort=(
            os.getenv("APP_SERVER_REASONING_EFFORT", "xhigh").strip() or "xhigh"
        ),
        ai_timeout_seconds=_int("AI_TIMEOUT_SECONDS", 240),
        news_selector_enabled=news_selector_enabled,
        openrouter_api_key=openrouter_api_key,
        news_selector_model=(
            os.getenv("NEWS_SELECTOR_MODEL", "typesafe/jev-1.13").strip()
            or "typesafe/jev-1.13"
        ),
        news_selector_shadow_mode=_bool("NEWS_SELECTOR_SHADOW_MODE", True),
        news_selector_min_reject_confidence=_bounded_float(
            "NEWS_SELECTOR_MIN_REJECT_CONFIDENCE",
            0.8,
            minimum=0.5,
            maximum=1.0,
        ),
        news_selector_timeout_seconds=_bounded_int(
            "NEWS_SELECTOR_TIMEOUT_SECONDS", 20, minimum=2, maximum=120,
        ),
        editor_url=os.getenv("EDITOR_URL", "").strip(),
        editor_token=os.getenv("EDITOR_TOKEN", "").strip(),
        dedup_context_hours=_bounded_int(
            "DEDUP_CONTEXT_HOURS", 48, minimum=1, maximum=168,
        ),
        dedup_context_limit=_bounded_int(
            "DEDUP_CONTEXT_LIMIT", 50, minimum=1, maximum=100,
        ),
        reddit_enabled=reddit_enabled,
        reddit_api_key=reddit_api_key,
        reddit_subreddits=reddit_subreddits,
        social_discovery_hours_utc=_hours("SOCIAL_DISCOVERY_HOURS_UTC", (8, 18)),
        reddit_candidates_per_subreddit=_bounded_int(
            "REDDIT_CANDIDATES_PER_SUBREDDIT", 15, minimum=1, maximum=100,
        ),
        reddit_max_posts_per_run=_bounded_int(
            "REDDIT_MAX_POSTS_PER_RUN", 1, minimum=1, maximum=2,
        ),
        reddit_max_posts_per_day=_bounded_int(
            "REDDIT_MAX_POSTS_PER_DAY", 2, minimum=1, maximum=10,
        ),
        x_enabled=x_enabled,
        x_api_key=x_api_key,
        x_search_query=x_search_query,
        x_max_posts_per_run=_bounded_int(
            "X_MAX_POSTS_PER_RUN", 1, minimum=1, maximum=2,
        ),
        x_max_posts_per_day=_bounded_int(
            "X_MAX_POSTS_PER_DAY", 2, minimum=1, maximum=10,
        ),
        emoji_autocreate_enabled=_bool("EMOJI_AUTOCREATE_ENABLED"),
        emoji_set_prefix=(
            os.getenv("EMOJI_SET_PREFIX", "gildra_warcraft").strip()
            or "gildra_warcraft"
        ),
        emoji_icon_dir=(
            os.getenv("EMOJI_ICON_DIR", "data/emoji_icons").strip()
            or "data/emoji_icons"
        ),
        emoji_max_new_per_day=_bounded_int(
            "EMOJI_MAX_NEW_PER_DAY", 10, minimum=1, maximum=100,
        ),
        emoji_upload_timeout_seconds=_bounded_int(
            "EMOJI_UPLOAD_TIMEOUT_SECONDS", 15, minimum=1, maximum=60,
        ),
        scrape_do_enabled=scrape_do_enabled,
        subscribe_emoji_id=os.getenv("SUBSCRIBE_EMOJI_ID", "").strip(),
        forever_guide_message_id=_bounded_int(
            "FOREVER_GUIDE_MESSAGE_ID", 0, minimum=0, maximum=2_147_483_647,
        ),
        forever_guide_refresh_minutes=_bounded_int(
            "FOREVER_GUIDE_REFRESH_MINUTES", 30, minimum=5, maximum=1_440,
        ),
        forever_release_date=_date(
            "FOREVER_RELEASE_DATE", date(2026, 11, 4),
        ),
    )
