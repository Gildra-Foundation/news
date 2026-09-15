from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_ARTICLE_BYTES = 3 * 1024 * 1024
MAX_ITEMS = 80
SUPPORTED_ARTICLE_SOURCES = frozenset({"wowhead", "icy-veins"})
_CONTENT_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"
_MEDIA_TAG = "{http://search.yahoo.com/mrss/}content"
_ARTICLE_ID_RE = re.compile(r"(?:news=|/news/)(\d+)")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_PARAGRAPH_RE = re.compile(r"\n{3,}")
_ANALYTICS_TOPIC_RE = re.compile(
    r"\b(?:dps|rankings?|logs?|statistics?)\b",
    re.IGNORECASE,
)
_ANALYTICS_IMAGE_MARKERS = (
    "warcraft-logs",
    "damage-statistics",
    "dps-",
    "ranking",
)


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self.ignored_depth += 1
            return
        if tag in {"br", "p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.ignored_depth:
            self.ignored_depth -= 1
            return
        if tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


class _HTMLMedia(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.image_url = ""
        self.video_url = ""
        self.inline_images: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key.lower(): value for key, value in attrs if value is not None}
        if tag == "meta":
            property_name = attributes.get("property", attributes.get("name", "")).lower()
            content = _http_url(attributes.get("content"))
            if not self.image_url and property_name in {"og:image", "twitter:image"}:
                self.image_url = content
            elif not self.video_url and property_name in {
                "og:video",
                "og:video:url",
                "og:video:secure_url",
            }:
                self.video_url = content
        elif tag == "video" and not self.video_url:
            self.video_url = _http_url(attributes.get("src"))
        elif tag == "source" and not self.video_url:
            media_type = attributes.get("type", "").lower()
            if media_type == "video/mp4":
                self.video_url = _http_url(attributes.get("src"))
        elif tag == "img":
            image_url = _http_url(
                attributes.get("data-src") or attributes.get("src"),
            )
            if image_url:
                self.inline_images.append(image_url)


@dataclass(frozen=True, slots=True)
class RSSItem:
    source: str
    external_id: int
    title: str
    content: str
    published_at: datetime
    article_url: str = ""
    image_url: str = ""
    video_url: str = ""

    @property
    def ai_text(self) -> str:
        return f"Заголовок: {self.title}\n\nТекст новости:\n{self.content}"


def _plain_text(value: str | None) -> str:
    parser = _HTMLText()
    parser.feed(value or "")
    parser.close()
    lines = (_SPACE_RE.sub(" ", line).strip() for line in "".join(parser.parts).splitlines())
    text = "\n".join(line for line in lines if line)
    text = re.sub(r"\bContinue reading\s*»?\s*$", "", text, flags=re.IGNORECASE)
    return _PARAGRAPH_RE.sub("\n\n", text).strip()


def _external_id(guid: str, link: str) -> int:
    identity = guid.strip() or link.strip()
    match = _ARTICLE_ID_RE.search(identity) or _ARTICLE_ID_RE.search(link)
    if match:
        return int(match.group(1))
    digest = hashlib.blake2b(identity.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & ((1 << 63) - 1)


def _published_at(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _http_url(value: str | None) -> str:
    normalized = (value or "").strip()
    parsed = urlparse(normalized)
    return normalized if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def source_key(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if host == "wowhead.com" or host.endswith(".wowhead.com"):
        return "wowhead"
    if host == "icy-veins.com" or host.endswith(".icy-veins.com"):
        return "icy-veins"
    return f"rss:{host}" if host else "rss"


def extract_article_media(
    document: bytes,
    *,
    relevance_text: str = "",
) -> tuple[str, str]:
    if len(document) > MAX_ARTICLE_BYTES:
        raise ValueError("HTML-документ статьи слишком большой")
    parser = _HTMLMedia()
    try:
        parser.feed(document.decode("utf-8", errors="strict"))
        parser.close()
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Некорректный HTML статьи") from exc
    image_url = parser.image_url
    if _ANALYTICS_TOPIC_RE.search(relevance_text):
        relevant_chart = next(
            (
                candidate
                for candidate in parser.inline_images
                if any(
                    marker in candidate.casefold()
                    for marker in _ANALYTICS_IMAGE_MARKERS
                )
            ),
            "",
        )
        image_url = relevant_chart or image_url
    return image_url, parser.video_url


def parse_feed(document: bytes, *, source: str) -> list[RSSItem]:
    if len(document) > MAX_FEED_BYTES:
        raise ValueError("RSS-документ слишком большой")
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise ValueError("Некорректный RSS XML") from exc

    items: list[RSSItem] = []
    for element in root.findall("./channel/item")[:MAX_ITEMS]:
        title = _plain_text(element.findtext("title", default=""))
        link = _http_url(element.findtext("link", default=""))
        guid = (element.findtext("guid", default="") or "").strip()
        published_at = _published_at(element.findtext("pubDate", default=""))
        encoded = element.findtext(_CONTENT_TAG, default="")
        content = _plain_text(encoded or element.findtext("description", default=""))
        image_url = ""
        video_url = ""
        for media in element.findall(_MEDIA_TAG):
            media_url = _http_url(media.get("url", ""))
            medium = media.get("medium", "").lower()
            media_type = media.get("type", "").lower()
            if not media_url:
                continue
            if not image_url and (medium == "image" or media_type.startswith("image/")):
                image_url = media_url
            elif not video_url and (medium == "video" or media_type == "video/mp4"):
                video_url = media_url
        if not (title and content and published_at and (guid or link)):
            continue
        items.append(
            RSSItem(
                source=source.strip().lower(),
                external_id=_external_id(guid, link),
                title=title,
                content=content,
                published_at=published_at,
                article_url=link,
                image_url=image_url,
                video_url=video_url,
            )
        )
    return items


async def fetch_feed(
    url: str,
    *,
    source: str,
    http_client: httpx.AsyncClient | None = None,
) -> list[RSSItem]:
    if not _http_url(url):
        raise ValueError("RSS URL должен использовать http или https")
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "GildraNews/0.1 RSS reader"},
    )
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_FEED_BYTES:
                    raise ValueError("RSS-документ слишком большой")
                chunks.append(chunk)
        return parse_feed(b"".join(chunks), source=source)
    finally:
        if owns_client:
            await client.aclose()


async def fetch_article_media(
    url: str,
    *,
    relevance_text: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> tuple[str, str]:
    expected_source = source_key(url)
    if expected_source not in SUPPORTED_ARTICLE_SOURCES:
        raise ValueError("Неподдерживаемый источник статьи")
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "GildraNews/0.1 article reader"},
    )
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if source_key(str(response.url)) != expected_source:
                raise ValueError("Перенаправление статьи за пределы источника")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_ARTICLE_BYTES:
                    raise ValueError("HTML-документ статьи слишком большой")
                chunks.append(chunk)
        return extract_article_media(
            b"".join(chunks),
            relevance_text=relevance_text,
        )
    finally:
        if owns_client:
            await client.aclose()
