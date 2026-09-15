"""Safe Scrape.do fallback for Warcraft post images."""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
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
_IMAGE_NOISE = ("avatar", "favicon", "placeholder", "site-logo", "site_logo")
_KIND_TERMS = {
    "raid": ("raid", "boss", "instance"),
    "dungeon": ("dungeon", "boss", "instance"),
    "boss": ("boss", "encounter"),
    "creature": ("npc", "creature"),
    "spell": ("spell", "ability"),
    "talent": ("talent", "spell"),
    "item": ("item", "gear"),
    "cosmetic": ("cosmetic", "transmog", "armor"),
    "transmog_set": ("transmog", "armor", "set"),
    "mount": ("mount",),
    "pet": ("pet", "companion"),
    "expansion": ("expansion",),
}
PageFetcher = Callable[[str], Awaitable[bytes | None]]


@dataclass(slots=True)
class _ImageCandidate:
    url: str
    width: int | None = None
    height: int | None = None
    alt: str = ""


class _OpenGraphImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[_ImageCandidate] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "meta":
            return
        attributes = {key.lower(): value for key, value in attrs if value is not None}
        name = attributes.get("property", attributes.get("name", "")).lower()
        if name in {"og:image", "twitter:image"}:
            url = attributes.get("content", "").strip()
            if url:
                self.images.append(_ImageCandidate(url=url))
        elif self.images and name in {
            "og:image:width", "twitter:image:width",
            "og:image:height", "twitter:image:height",
        }:
            value = attributes.get("content", "").strip()
            if value.isdigit():
                field = "width" if name.endswith(":width") else "height"
                setattr(self.images[-1], field, int(value))
        elif self.images and name in {"og:image:alt", "twitter:image:alt"}:
            self.images[-1].alt = attributes.get("content", "").strip()


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


def _result_score(query: str, result: dict, entity_kind: str) -> int:
    title = str(result.get("title", "")).casefold()
    snippet = str(result.get("snippet", "")).casefold()
    haystack = f"{title} {snippet}"
    words = {
        word.casefold()
        for word in _WORD_RE.findall(query)
        if word.casefold() not in _SEARCH_NOISE
    }
    score = sum(2 for word in words if word in haystack)
    normalized_query = " ".join(query.casefold().split())
    if normalized_query and normalized_query in title:
        score += 20
    if words and all(word in title for word in words):
        score += 8
    score += 5 * sum(term in haystack for term in _KIND_TERMS.get(entity_kind, ()))
    return score


def _best_page_image(parser: _OpenGraphImageParser, query: str) -> str | None:
    words = {
        word.casefold()
        for word in _WORD_RE.findall(query)
        if word.casefold() not in _SEARCH_NOISE
    }
    ranked: list[tuple[int, int, str]] = []
    for index, image in enumerate(parser.images):
        if not _safe_image_url(image.url):
            continue
        if image.width is not None and image.height is not None:
            if image.width < 400 or image.height < 225:
                continue
            ratio = image.width / image.height
            if not 0.5 <= ratio <= 2.5:
                continue
        description = f"{image.url} {image.alt}".casefold()
        score = 3 * sum(word in description for word in words)
        if image.width is not None and image.height is not None:
            score += min((image.width * image.height) // 100_000, 20)
            if 1.2 <= image.width / image.height <= 2.2:
                score += 4
        if any(marker in description for marker in _IMAGE_NOISE):
            score -= 20
        ranked.append((score, -index, image.url))
    return max(ranked, default=(0, 0, None))[2]


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
    entity_kind: str = "",
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
                    f"{' '.join(_KIND_TERMS.get(entity_kind, ())[:2])} "
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
        ranked_results = sorted(
            (
                raw for raw in results[:8]
                if isinstance(raw, dict) and _relevant(normalized_query, raw)
            ),
            key=lambda raw: _result_score(normalized_query, raw, entity_kind),
            reverse=True,
        )
        for raw in ranked_results[:5]:
            page_url = str(raw.get("link") or "").strip()
            if not _safe_page_url(page_url):
                continue
            document = await fetch_page(page_url)
            if not document or len(document) > MAX_PAGE_BYTES:
                continue
            parser = _OpenGraphImageParser()
            parser.feed(document.decode("utf-8", errors="replace"))
            parser.close()
            if image_url := _best_page_image(parser, normalized_query):
                return image_url
        return None
    except (httpx.HTTPError, UnicodeDecodeError, ValueError):
        return None
    finally:
        if owns_client:
            await client.aclose()
