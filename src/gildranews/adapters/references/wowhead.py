from __future__ import annotations

import json
import re
import unicodedata
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlencode, urlparse

import httpx

ReferenceKind = Literal["raid", "creature"]
MAX_SEARCH_BYTES = 2 * 1024 * 1024
MAX_QUERY_CHARS = 100
_SPACE_RE = re.compile(r"\s+")


class _JsonScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.documents: list[str] = []
        self._parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes = {key.lower(): value for key, value in attrs if value is not None}
        if tag == "script" and attributes.get("type", "").lower() == "application/json":
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._parts is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._parts is not None:
            self.documents.append("".join(self._parts))
            self._parts = None


def _name_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = _SPACE_RE.sub(" ", normalized)
    return normalized.removeprefix("the ")


def _entity_url(document: bytes, query: str, kind: ReferenceKind) -> str | None:
    parser = _JsonScriptParser()
    parser.feed(document.decode("utf-8", errors="replace"))
    parser.close()
    expected_name = _name_key(query)
    for raw_json in parser.documents:
        try:
            candidates = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if _name_key(str(candidate.get("name", ""))) != expected_name:
                continue
            entity_id = candidate.get("id")
            if not isinstance(entity_id, int) or entity_id <= 0:
                continue
            if kind == "raid" and "instance" in candidate:
                return f"https://www.wowhead.com/zone={entity_id}"
            if kind == "creature" and any(
                field in candidate for field in ("boss", "classification", "location")
            ):
                return f"https://www.wowhead.com/npc={entity_id}"
    return None


async def resolve_reference(
    query: str,
    kind: ReferenceKind,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    normalized_query = _SPACE_RE.sub(" ", query).strip()
    if (
        kind not in {"raid", "creature"}
        or not 1 < len(normalized_query) <= MAX_QUERY_CHARS
        or any(ord(char) < 32 for char in normalized_query)
    ):
        return None
    url = "https://www.wowhead.com/search?" + urlencode({"q": normalized_query})
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "GildraNews/0.1 Wowhead reference resolver"},
    )
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            final_url = urlparse(str(response.url))
            if final_url.scheme != "https" or final_url.hostname != "www.wowhead.com":
                return None
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_SEARCH_BYTES:
                    return None
                chunks.append(chunk)
        return _entity_url(b"".join(chunks), normalized_query, kind)
    except (httpx.HTTPError, ValueError):
        return None
    finally:
        if owns_client:
            await client.aclose()
