"""Read-only GetXAPI adapter for World of Warcraft topic discovery."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from gildranews.adapters.sources.rss import RSSItem

API_URL = "https://api.getxapi.com/twitter/tweet/advanced_search"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TWEETS = 40
MAX_TEXT_CHARS = 8_000
MAX_QUERY_CHARS = 500
MAX_SQLITE_INTEGER = (1 << 63) - 1
_USERNAME_RE = re.compile(r"[A-Za-z0-9_]{1,15}\Z")


@dataclass(frozen=True, slots=True)
class XTopic:
    tweet_id: int
    text: str
    author: str
    likes: int
    reposts: int
    replies: int
    views: int

    @property
    def source(self) -> str:
        return "x"

    @property
    def external_id(self) -> int:
        return self.tweet_id

    @property
    def article_url(self) -> str:
        return f"https://x.com/{self.author}/status/{self.tweet_id}"

    @property
    def engagement(self) -> int:
        return self.likes + self.reposts * 3 + self.replies * 2

    @property
    def ai_text(self) -> str:
        return (
            f"Текст публикации:\n{self.text}\n\n"
            f"Активность: {self.likes} отметок, {self.reposts} репостов, "
            f"{self.replies} ответов, {self.views} просмотров."
        )

    def as_feed_item(self) -> RSSItem:
        title = self.text[:180].strip()
        continuation = self.text[len(title):].lstrip()
        activity = (
            f"Активность сообщества: {self.likes} отметок, {self.reposts} репостов, "
            f"{self.replies} ответов, {self.views} просмотров."
        )
        return RSSItem(
            source=self.source,
            external_id=self.external_id,
            title=title,
            content=f"{continuation}\n\n{activity}" if continuation else activity,
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


def parse_search(payload: object) -> list[XTopic]:
    """Validate and normalize the bounded subset returned by GetXAPI."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tweets"), list):
        raise TypeError("GetXAPI вернул ответ неизвестного формата")

    topics: list[XTopic] = []
    for raw in payload["tweets"][:MAX_TWEETS]:
        if (
            not isinstance(raw, dict)
            or raw.get("isReply")
            or raw.get("isRetweet")
            or raw.get("isPromoted")
        ):
            continue
        author_data = raw.get("author")
        if not isinstance(author_data, dict):
            continue
        author = str(author_data.get("userName") or "").strip()
        text = str(raw.get("text") or "").strip()[:MAX_TEXT_CHARS]
        tweet_id = _integer(raw.get("id"))
        if (
            not text
            or not 0 < tweet_id <= MAX_SQLITE_INTEGER
            or not _USERNAME_RE.fullmatch(author)
        ):
            continue
        topics.append(
            XTopic(
                tweet_id=tweet_id,
                text=text,
                author=author,
                likes=_integer(raw.get("likeCount")),
                reposts=_integer(raw.get("retweetCount")),
                replies=_integer(raw.get("replyCount")),
                views=_integer(raw.get("viewCount")),
            )
        )
    return topics


async def fetch_top_posts(
    api_key: str,
    query: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[XTopic]:
    """Fetch one bounded page of the most engaging matching X posts."""
    normalized_query = query.strip()
    if not api_key.strip():
        raise ValueError("Не задан GETXAPI_KEY")
    if not normalized_query or len(normalized_query) > MAX_QUERY_CHARS:
        raise ValueError("Некорректный X_SEARCH_QUERY")

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(20, connect=10),
        follow_redirects=False,
        headers={"User-Agent": "GildraNews/0.1 X topic reader"},
    )
    try:
        async with client.stream(
            "GET",
            API_URL,
            params={"q": normalized_query, "product": "Top"},
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        ) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ValueError("Ответ GetXAPI слишком большой")
                chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("GetXAPI вернул некорректный JSON") from exc
        return parse_search(payload)
    finally:
        if owns_client:
            await client.aclose()
