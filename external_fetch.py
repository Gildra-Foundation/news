"""Парсинг постов из X (Twitter) и Reddit по публичной ссылке.

X — через FxTwitter (free, no auth): https://api.fxtwitter.com/_/status/<id>
Reddit — через нативный .json эндпойнт.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Literal

import httpx

log = logging.getLogger(__name__)

# Один HTTP-клиент на процесс — переиспользуем соединения и DNS-кэш.
_http_client: httpx.AsyncClient | None = None


async def close_http() -> None:
    """Закрыть глобальный httpx-клиент на shutdown. Идемпотентно."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        try:
            await _http_client.aclose()
        except Exception:
            log.warning("close_http: aclose() raised", exc_info=True)
    _http_client = None


def _get_http() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
            headers={
                # Reddit и FxTwitter блокируют generic-агенты. Имитируем настоящий браузер.
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        )
    return _http_client

TWITTER_RE = re.compile(
    r"https?://(?:mobile\.|www\.)?(?:twitter\.com|x\.com)/[^/\s]+/status/(\d+)",
    re.IGNORECASE,
)
REDDIT_RE = re.compile(
    r"https?://(?:www\.|old\.|new\.)?reddit\.com/r/(\w+)/comments/(\w+)",
    re.IGNORECASE,
)
GITHUB_RE = re.compile(
    r"https?://github\.com/([\w.-]+)/([\w.-]+)(?:\.git)?(?=/|$|\s|\?|#)",
    re.IGNORECASE,
)
EXTERNAL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?(?:twitter\.com|x\.com|reddit\.com|github\.com)/\S+",
    re.IGNORECASE,
)


@dataclass
class ExternalPost:
    source: Literal["twitter", "reddit", "github"]
    text: str
    media_url: str | None          # URL фото/видео из поста (None если только текст)
    media_type: Literal["photo", "video", "none"]
    author: str
    source_url: str
    # Дополнительные поля для рендера карточки-скриншота / отображения
    screen_name: str = ""    # @handle для X / owner для GitHub
    subreddit: str = ""      # сабреддит без префикса
    title: str = ""          # отдельный заголовок (Reddit) / полное имя репо (GitHub)
    meta: dict = field(default_factory=dict)  # доп. структурированные данные (для рендера)
    # Backward-compat alias
    @property
    def image_url(self) -> str | None:
        return self.media_url if self.media_type == "photo" else None


async def download_avatar(url: str, dest_path: str) -> bool:
    """Скачивает аватарку в файл. True если успех."""
    if not url:
        return False
    try:
        r = await _get_http().get(url)
    except httpx.HTTPError:
        return False
    if r.status_code != 200 or not r.content:
        return False
    try:
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return True
    except OSError:
        return False


async def fetch(url: str) -> ExternalPost | None:
    if (m := TWITTER_RE.search(url)):
        return await _fetch_tweet(m.group(1))
    if (m := REDDIT_RE.search(url)):
        return await _fetch_reddit(m.group(1), m.group(2))
    if (m := GITHUB_RE.search(url)):
        return await _fetch_github(m.group(1), m.group(2))
    return None


async def _fetch_tweet(tweet_id: str) -> ExternalPost | None:
    url = f"https://api.fxtwitter.com/i/status/{tweet_id}"
    try:
        r = await _get_http().get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        log.warning("FxTwitter network error: %s", e)
        return None
    if r.status_code != 200:
        log.warning("FxTwitter %s for tweet %s", r.status_code, tweet_id)
        return None
    try:
        data = r.json()
    except Exception:
        return None

    tweet = data.get("tweet") or {}
    text = (tweet.get("text") or "").strip()
    if not text:
        return None

    media_url: str | None = None
    media_type: Literal["photo", "video", "none"] = "none"
    media = tweet.get("media") or {}

    # Видео имеет приоритет — обычно несёт основной контент
    videos = media.get("videos") or []
    if videos:
        v = videos[0]
        # По умолчанию — самое высокое качество (v.url). Если в formats есть 720p mp4,
        # предпочтём его (баланс качество/размер под лимит Bot API ~50MB).
        chosen = v.get("url")
        for fmt in v.get("formats") or []:
            if fmt.get("container") == "mp4" and "720x" in (fmt.get("url") or ""):
                chosen = fmt["url"]
                break
        if chosen:
            media_url = chosen
            media_type = "video"

    if media_type == "none":
        photos = media.get("photos") or []
        if photos:
            media_url = photos[0].get("url")
            media_type = "photo"
        elif media.get("all"):
            for item in media["all"]:
                if item.get("type") == "photo" and item.get("url"):
                    media_url = item["url"]
                    media_type = "photo"
                    break

    author_data = tweet.get("author") or {}
    author = author_data.get("name") or author_data.get("screen_name") or ""
    screen_name = author_data.get("screen_name") or ""
    avatar_url = author_data.get("avatar_url") or ""
    verified = bool(author_data.get("verified"))
    return ExternalPost(
        source="twitter",
        text=text,
        media_url=media_url,
        media_type=media_type,
        author=author,
        source_url=tweet.get("url") or f"https://x.com/i/status/{tweet_id}",
        screen_name=screen_name,
        meta={
            "avatar_url": avatar_url,
            "verified": verified,
        },
    )


async def _fetch_reddit(subreddit: str, post_id: str) -> ExternalPost | None:
    # old.reddit.com и raw_json=1 — мягче к ботам и без HTML-эскейпов.
    # Если 403 — пробуем www-домен как fallback.
    urls = [
        f"https://old.reddit.com/r/{subreddit}/comments/{post_id}/.json?raw_json=1",
        f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json?raw_json=1",
    ]
    r = None
    for url in urls:
        try:
            r = await _get_http().get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            log.warning("Reddit network error: %s", e)
            continue
        if r.status_code == 200:
            break
        log.warning("Reddit %s for %s/%s via %s", r.status_code, subreddit, post_id, url.split("/")[2])
    if r is None or r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    try:
        post = data[0]["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None

    title = (post.get("title") or "").strip()
    selftext = (post.get("selftext") or "").strip()
    if title and selftext:
        text = f"{title}\n\n{selftext}"
    else:
        text = title or selftext
    if not text:
        return None

    media_url: str | None = None
    media_type: Literal["photo", "video", "none"] = "none"

    # 1) Reddit hosted video
    if post.get("is_video"):
        rv = (post.get("media") or {}).get("reddit_video") or {}
        fb = rv.get("fallback_url")
        if fb:
            # fallback_url содержит query параметры, очистим
            media_url = fb.split("?")[0]
            media_type = "video"

    # 2) Картинка
    if media_type == "none":
        post_hint = post.get("post_hint")
        direct_url = post.get("url_overridden_by_dest") or post.get("url") or ""
        if post_hint == "image" or any(direct_url.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            media_url = direct_url
            media_type = "photo"
        else:
            preview = post.get("preview") or {}
            images = preview.get("images") or []
            if images:
                src = (images[0].get("source") or {}).get("url")
                if src:
                    media_url = src.replace("&amp;", "&")
                    media_type = "photo"

    author = post.get("author") or ""
    permalink = post.get("permalink") or ""
    return ExternalPost(
        source="reddit",
        text=text,
        media_url=media_url,
        media_type=media_type,
        author=f"u/{author}" if author else "",
        source_url=f"https://reddit.com{permalink}",
        subreddit=subreddit,
        title=title,
    )


# Часть README, которую отдаём Gemini. README может быть мегабайты, нам
# хватит первых нескольких сотен слов — там обычно описание и use-cases.
README_MAX_CHARS = 3500

_GITHUB_PRIVATE_REPOS = (
    "marketplace", "topics", "trending", "collections", "sponsors", "settings",
    "orgs", "users", "notifications", "explore", "issues", "pulls", "new",
)


async def _fetch_github(owner: str, repo: str) -> ExternalPost | None:
    """Тянет метаданные репо и фрагмент README. GitHub API без авторизации —
    лимит 60 req/h на IP, нам хватает для редкого ручного использования."""
    # Отсекаем нерепо-URL вроде github.com/marketplace
    if owner.lower() in _GITHUB_PRIVATE_REPOS:
        return None
    api = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {"Accept": "application/vnd.github+json"}
    try:
        r = await _get_http().get(api, headers=headers)
    except httpx.HTTPError as e:
        log.warning("GitHub network error: %s", e)
        return None
    if r.status_code != 200:
        log.warning("GitHub %s for %s/%s", r.status_code, owner, repo)
        return None
    data = r.json()

    name = data.get("full_name") or f"{owner}/{repo}"
    description = (data.get("description") or "").strip()
    language = data.get("language") or ""
    stars = data.get("stargazers_count", 0)
    forks = data.get("forks_count", 0)
    topics = data.get("topics") or []
    license_info = ((data.get("license") or {}).get("spdx_id") or "").strip()
    homepage = (data.get("homepage") or "").strip()
    html_url = data.get("html_url") or f"https://github.com/{owner}/{repo}"

    # README — отдельный запрос, мягкий fallback при отказе
    readme = ""
    try:
        r2 = await _get_http().get(
            f"https://api.github.com/repos/{owner}/{repo}/readme",
            headers={"Accept": "application/vnd.github.raw+json"},
        )
        if r2.status_code == 200:
            readme = (r2.text or "").strip()[:README_MAX_CHARS]
    except httpx.HTTPError:
        pass

    parts = [f"Repository: {name}"]
    if description:
        parts.append(f"Description: {description}")
    if language:
        parts.append(f"Primary language: {language}")
    parts.append(f"Stars: {stars}; Forks: {forks}")
    if topics:
        parts.append(f"Topics: {', '.join(topics)}")
    if license_info:
        parts.append(f"License: {license_info}")
    if homepage:
        parts.append(f"Homepage: {homepage}")
    if readme:
        parts.append("\nREADME (excerpt):\n" + readme)
    text = "\n".join(parts)

    # Картинку отрисуем сами через render_post.render_github — стабильнее, чем
    # дёргать opengraph.githubassets.com (медленный, иногда отдаёт болванку).
    return ExternalPost(
        source="github",
        text=text,
        media_url=None,
        media_type="none",
        author=owner,
        source_url=html_url,
        screen_name=owner,
        title=name,
        meta={
            "description": description,
            "language": language,
            "stars": stars,
            "forks": forks,
            "topics": topics,
            "license": license_info,
        },
    )
