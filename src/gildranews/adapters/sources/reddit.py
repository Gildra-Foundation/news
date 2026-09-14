"""Read-only RedditAPIs adapter for daily WoW topic discovery."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from gildranews.adapters.sources.rss import RSSItem

API_URL = "https://api.redditapis.com/api/reddit/posts"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_POSTS = 100
MAX_TITLE_CHARS = 500
MAX_TEXT_CHARS = 10_000
_SUBREDDIT_RE = re.compile(r"[A-Za-z0-9_]{2,32}\Z")
_POST_ID_RE = re.compile(r"[a-z0-9]{3,12}\Z")


@dataclass(frozen=True, slots=True)
class RedditTopic:
    reddit_id: str
    title: str
    text: str
    subreddit: str
    upvotes: int
    comments: int

    @property
    def source(self) -> str:
        return f"reddit:{self.subreddit.lower()}"

    @property
    def external_id(self) -> int:
        return int(self.reddit_id, 36)

    @property
    def article_url(self) -> str:
        return (
            f"https://www.reddit.com/r/{self.subreddit}/comments/{self.reddit_id}/"
        )

    @property
    def engagement(self) -> int:
        return self.upvotes + self.comments * 3

    @property
    def ai_text(self) -> str:
        body = self.text or "В публикации нет дополнительного текста."
        return (
            f"Заголовок темы: {self.title}\n\n"
            f"Текст темы:\n{body}\n\n"
            f"Активность: {self.upvotes} голосов, {self.comments} комментария."
        )

    def as_feed_item(self) -> RSSItem:
        return RSSItem(
            source=self.source,
            external_id=self.external_id,
            title=self.title,
            content=(
                f"{self.text}\n\n"
                f"Активность сообщества: {self.upvotes} голосов, "
                f"{self.comments} комментария."
            ).strip(),
            published_at=datetime.now(UTC),
            article_url=self.article_url,
        )


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_listing(payload: object, *, subreddit: str) -> list[RedditTopic]:
    """Validate the small subset of the third-party response that we consume."""
    if not _SUBREDDIT_RE.fullmatch(subreddit):
        raise ValueError("Некорректное имя Reddit-сообщества")
    if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
        raise TypeError("RedditAPIs вернул ответ неизвестного формата")

    topics: list[RedditTopic] = []
    for raw in payload["posts"][:MAX_POSTS]:
        if (
            not isinstance(raw, dict)
            or raw.get("over_18")
            or raw.get("stickied")
            or raw.get("promoted")
        ):
            continue
        reddit_id = str(raw.get("id") or "").strip().lower()
        title = str(raw.get("title") or "").strip()[:MAX_TITLE_CHARS]
        response_subreddit = str(raw.get("subreddit") or subreddit).strip()
        if (
            not _POST_ID_RE.fullmatch(reddit_id)
            or not title
            or not _SUBREDDIT_RE.fullmatch(response_subreddit)
            or response_subreddit.lower() != subreddit.lower()
        ):
            continue
        topics.append(
            RedditTopic(
                reddit_id=reddit_id,
                title=title,
                text=str(raw.get("text") or "").strip()[:MAX_TEXT_CHARS],
                subreddit=response_subreddit,
                upvotes=_integer(raw.get("upvotes")),
                comments=_integer(raw.get("comments")),
            )
        )
    return topics


async def fetch_top_posts(
    api_key: str,
    subreddit: str,
    *,
    limit: int = 15,
    http_client: httpx.AsyncClient | None = None,
) -> list[RedditTopic]:
    """Fetch one bounded page of the top posts from the previous day."""
    if not api_key.strip():
        raise ValueError("Не задан REDDITAPIS_KEY")
    if not _SUBREDDIT_RE.fullmatch(subreddit):
        raise ValueError("Некорректное имя Reddit-сообщества")
    if not 1 <= limit <= MAX_POSTS:
        raise ValueError("limit должен быть от 1 до 100")

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=10),
        follow_redirects=False,
        headers={"User-Agent": "GildraNews/0.1 Reddit topic reader"},
    )
    try:
        async with client.stream(
            "GET",
            API_URL,
            params={"subreddit": subreddit, "sort": "top", "t": "day", "limit": limit},
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        ) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ValueError("Ответ RedditAPIs слишком большой")
                chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("RedditAPIs вернул некорректный JSON") from exc
        return parse_listing(payload, subreddit=subreddit)
    finally:
        if owns_client:
            await client.aclose()
