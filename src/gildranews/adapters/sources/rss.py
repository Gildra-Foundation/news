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
MAX_ITEMS = 80
_CONTENT_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"
_MEDIA_TAG = "{http://search.yahoo.com/mrss/}content"
_ARTICLE_ID_RE = re.compile(r"(?:news=|/news/)(\d+)")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_PARAGRAPH_RE = re.compile(r"\n{3,}")


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


@dataclass(frozen=True, slots=True)
class RSSItem:
    source: str
    external_id: int
    title: str
    content: str
    published_at: datetime
    article_url: str = ""
    image_url: str = ""

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
    return f"rss:{host}" if host else "rss"


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
        media = element.find(_MEDIA_TAG)
        image_url = _http_url(media.get("url", "")) if media is not None else ""
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
