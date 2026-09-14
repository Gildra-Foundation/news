"""Парсинг X/Reddit ссылок через явные платные и бесплатные маршруты.

GetXAPI и RedditAPIs используются только при включённых флагах. Затем идут
бесплатные FxTwitter/Reddit JSON и явно включённый Scrape.do fallback.
Ответы принимаются только после проверки контрактом ParsesUnix.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
from web_scraper import ResponseContract, validate_response
from web_scraper.fetchers import RawResponse
from web_scraper.providers.base import ProviderError, ProviderRequest
from web_scraper.providers.scrape_do import ScrapeDoProvider

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
        await asyncio.to_thread(Path(dest_path).write_bytes, r.content)
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


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _validated_json(
    response: httpx.Response,
    required_path_options: tuple[tuple[str, ...], ...],
    source: str,
) -> object | None:
    raw = RawResponse(
        requested_url=str(response.request.url),
        final_url=str(response.url),
        status=response.status_code,
        headers=dict(response.headers),
        body=response.content,
    )
    return _validated_raw_json(raw, required_path_options, source)


def _validated_raw_json(
    raw: RawResponse,
    required_path_options: tuple[tuple[str, ...], ...],
    source: str,
) -> object | None:
    for required_paths in required_path_options:
        result = validate_response(
            raw,
            ResponseContract.json(required_json_paths=required_paths),
        )
        if result.transport_validated:
            try:
                return json.loads(raw.body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                break
    log.warning("%s response rejected by ParsesUnix contract", source)
    return None


async def _fetch_scrape_do_json(
    url: str,
    required_path_options: tuple[tuple[str, ...], ...],
    source: str,
) -> object | None:
    if not os.getenv("SCRAPE_DO_TOKEN", "").strip() or not _enabled("SCRAPE_DO_ENABLED"):
        return None
    try:
        response = await asyncio.to_thread(
            ScrapeDoProvider().fetch,
            ProviderRequest(url=url, strategy_id="normal", timeout_seconds=30),
        )
    except ProviderError as exc:
        log.warning("Scrape.do fallback failed for %s: %s", source, exc.__class__.__name__)
        return None
    raw = RawResponse(
        requested_url=url,
        final_url=response.final_url or url,
        status=response.target_status,
        headers=response.headers,
        body=response.body,
        elapsed_ms=response.latency_ms,
        truncated=response.truncated,
    )
    return _validated_raw_json(raw, required_path_options, f"Scrape.do/{source}")


def _post_from_getxapi(tweet: dict) -> ExternalPost | None:
    text = str(tweet.get("text") or "").strip()
    if not text:
        return None

    media_url: str | None = None
    media_type: Literal["photo", "video", "none"] = "none"
    media = tweet.get("media") or []
    if isinstance(media, dict):
        media = [media]
    for item in media:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").lower()
        if kind in {"video", "animated_gif", "gif"}:
            media_url = item.get("videoUrl") or item.get("url") or item.get("media_url_https")
            if media_url:
                media_type = "video"
                break
        if media_type == "none" and kind in {"photo", "image"}:
            media_url = item.get("url") or item.get("media_url_https")
            if media_url:
                media_type = "photo"

    author_data = tweet.get("author") or {}
    screen_name = str(author_data.get("userName") or author_data.get("screen_name") or "")
    tweet_id = str(tweet.get("id") or "")
    return ExternalPost(
        source="twitter",
        text=text,
        media_url=str(media_url) if media_url else None,
        media_type=media_type,
        author=str(author_data.get("name") or screen_name),
        source_url=str(tweet.get("url") or f"https://x.com/i/status/{tweet_id}"),
        screen_name=screen_name,
        meta={
            "avatar_url": str(
                author_data.get("profilePicture") or author_data.get("avatar_url") or ""
            ),
            "verified": bool(author_data.get("isVerified") or author_data.get("isBlueVerified")),
        },
    )


async def _fetch_getxapi(tweet_id: str) -> ExternalPost | None:
    key = os.getenv("GETXAPI_KEY", "").strip()
    if not key or not _enabled("GETXAPI_ENABLED"):
        return None
    try:
        response = await _get_http().get(
            "https://api.getxapi.com/twitter/tweet/detail",
            params={"id": tweet_id},
            headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
        )
    except httpx.HTTPError as exc:
        log.warning("GetXAPI network error: %s", exc.__class__.__name__)
        return None
    payload = _validated_json(response, (("data.id", "data.text"),), "GetXAPI")
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return None
    return _post_from_getxapi(payload["data"])


async def _fetch_tweet(tweet_id: str) -> ExternalPost | None:
    if post := await _fetch_getxapi(tweet_id):
        return post
    url = f"https://api.fxtwitter.com/i/status/{tweet_id}"
    data: object | None = None
    try:
        r = await _get_http().get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        log.warning("FxTwitter network error: %s", e)
    else:
        if r.status_code == 200:
            data = _validated_json(r, (("tweet.text",),), "FxTwitter")
        else:
            log.warning("FxTwitter %s for tweet %s", r.status_code, tweet_id)
    if data is None:
        data = await _fetch_scrape_do_json(url, (("tweet.text",),), "FxTwitter")
    if not isinstance(data, dict):
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
    if post := await _fetch_redditapis(post_id):
        return post
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
    data: object | None = None
    if r is not None and r.status_code == 200:
        data = _validated_json(
            r,
            (("0.data.children.0.data.id",),),
            "Reddit",
        )
    if data is None:
        data = await _fetch_scrape_do_json(
            urls[0],
            (("0.data.children.0.data.id",),),
            "Reddit",
        )
    if not isinstance(data, list):
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


def _post_from_redditapis(post: dict) -> ExternalPost | None:
    title = str(post.get("title") or "").strip()
    selftext = str(post.get("text") or post.get("selftext") or "").strip()
    text = f"{title}\n\n{selftext}" if title and selftext else title or selftext
    if not text:
        return None

    media_url: str | None = None
    media_type: Literal["photo", "video", "none"] = "none"
    link_url = str(post.get("link_url") or "")
    path = link_url.lower().split("?", 1)[0]
    if path.endswith((".jpg", ".jpeg", ".png", ".webp")):
        media_url, media_type = link_url, "photo"
    elif path.endswith((".mp4", ".webm", ".mov")):
        media_url, media_type = link_url, "video"

    permalink = str(post.get("permalink") or "")
    source_url = str(post.get("url") or "")
    if permalink:
        source_url = f"https://reddit.com{permalink}"
    return ExternalPost(
        source="reddit",
        text=text,
        media_url=media_url,
        media_type=media_type,
        author=f"u/{post['author']}" if post.get("author") else "",
        source_url=source_url or f"https://reddit.com/comments/{post.get('id', '')}",
        subreddit=str(post.get("subreddit") or ""),
        title=title,
    )


async def _fetch_redditapis(post_id: str) -> ExternalPost | None:
    key = os.getenv("REDDITAPIS_KEY", "").strip()
    if not key or not _enabled("REDDITAPIS_ENABLED"):
        return None
    try:
        response = await _get_http().get(
            f"https://api.redditapis.com/api/reddit/post/{post_id}",
            headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
        )
    except httpx.HTTPError as exc:
        log.warning("RedditAPIs network error: %s", exc.__class__.__name__)
        return None
    payload = _validated_json(
        response,
        (("id", "title"), ("post.id", "post.title")),
        "RedditAPIs",
    )
    if not isinstance(payload, dict):
        return None
    candidate = payload.get("post", payload)
    if not isinstance(candidate, dict):
        return None
    return _post_from_redditapis(candidate)


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
