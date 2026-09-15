"""Safe Scrape.do fallback for Warcraft post images."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from web_scraper.providers.base import ProviderError, ProviderRequest
from web_scraper.providers.scrape_do import ScrapeDoProvider

SEARCH_ENDPOINT = "https://api.scrape.do/plugin/google/search"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PAGE_BYTES = 3 * 1024 * 1024
MAX_QUERY_CHARS = 180
ALLOWED_IMAGE_HOSTS = frozenset({"wow.zamimg.com", "static.icy-veins.com"})
_WORD_RE = re.compile(r"[A-Za-z0-9]{3,}")
_SEARCH_NOISE = frozenset({"the", "and", "world", "warcraft", "wow", "raid", "news"})
PageFetcher = Callable[[str], Awaitable[bytes | None]]


class _OpenGraphImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.image_url = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "meta" or self.image_url:
            return
        attributes = {key.lower(): value for key, value in attrs if value is not None}
        name = attributes.get("property", attributes.get("name", "")).lower()
        if name in {"og:image", "twitter:image"}:
            self.image_url = attributes.get("content", "").strip()


def _safe_page_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return False
    host = (parsed.hostname or "").lower()
    return (
        host == "www.wowhead.com" and parsed.path.startswith("/news")
    ) or (
        host == "www.icy-veins.com" and parsed.path.startswith("/wow/news/")
    )


def _safe_image_url(value: str) -> bool:
    parsed = urlparse(value)
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in ALLOWED_IMAGE_HOSTS
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )


def _relevant(query: str, result: dict) -> bool:
    words = {
        word.casefold()
        for word in _WORD_RE.findall(query)
        if word.casefold() not in _SEARCH_NOISE
    }
    if not words:
        return False
    haystack = f"{result.get('title', '')} {result.get('snippet', '')}".casefold()
    matches = sum(word in haystack for word in words)
    return matches >= min(2, len(words))


async def _fetch_page_with_scrape_do(url: str) -> bytes | None:
    try:
        response = await asyncio.to_thread(
            ScrapeDoProvider(max_body_bytes=MAX_PAGE_BYTES).fetch,
            ProviderRequest(url=url, strategy_id="normal", timeout_seconds=30),
        )
    except ProviderError:
        return None
    if (
        response.target_status != 200
        or response.truncated
        or not _safe_page_url(response.final_url or url)
    ):
        return None
    return response.body


async def find_warcraft_image(
    query: str,
    *,
    token: str | None = None,
    http_client: httpx.AsyncClient | None = None,
    page_fetcher: PageFetcher | None = None,
) -> str | None:
    """Find a relevant OG image on an allowlisted Warcraft editorial page."""
    normalized_query = " ".join(query.split())[:MAX_QUERY_CHARS]
    scrape_token = (token if token is not None else os.getenv("SCRAPE_DO_TOKEN", "")).strip()
    if len(normalized_query) < 3 or not scrape_token:
        return None
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=10),
        follow_redirects=False,
        headers={"Accept": "application/json"},
    )
    try:
        response = await client.get(
            SEARCH_ENDPOINT,
            params={
                "token": scrape_token,
                "q": (
                    f'"World of Warcraft" {normalized_query} '
                    "(site:wowhead.com/news OR site:icy-veins.com/wow/news)"
                ),
                "safe": "active",
                "hl": "en",
                "gl": "us",
                "resolveGoto": "true",
            },
        )
        if (
            response.status_code != 200
            or len(response.content) > MAX_RESPONSE_BYTES
            or response.headers.get("content-type", "").split(";", 1)[0]
            not in {"application/json", "text/json", ""}
        ):
            return None
        payload = response.json()
        results = payload.get("organic_results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            return None
        fetch_page = page_fetcher or _fetch_page_with_scrape_do
        for raw in results[:5]:
            if not isinstance(raw, dict) or not _relevant(normalized_query, raw):
                continue
            page_url = str(raw.get("link") or "").strip()
            if not _safe_page_url(page_url):
                continue
            document = await fetch_page(page_url)
            if not document or len(document) > MAX_PAGE_BYTES:
                continue
            parser = _OpenGraphImageParser()
            parser.feed(document.decode("utf-8", errors="replace"))
            parser.close()
            if _safe_image_url(parser.image_url):
                return parser.image_url
        return None
    except (httpx.HTTPError, UnicodeDecodeError, ValueError):
        return None
    finally:
        if owns_client:
            await client.aclose()
