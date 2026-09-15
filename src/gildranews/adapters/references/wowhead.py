from __future__ import annotations

import json
import re
import unicodedata
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urlencode, urlparse

import httpx

from gildranews.domain.models import ResolvedWarcraftEntity, WarcraftEntityKind, WarcraftEntityRef

ReferenceKind = Literal["raid", "creature"]
MAX_SEARCH_BYTES = 2 * 1024 * 1024
MAX_QUERY_CHARS = 100
_SPACE_RE = re.compile(r"\s+")
_ICON_RE = re.compile(r"[a-z0-9_]+\Z")

_TYPE_CODE_NAMES = {1: "NPC", 3: "Item", 6: "Spell", 7: "Zone", 10: "Achievement", 101: "Transmog Set"}
_EXPECTED_TYPES: dict[WarcraftEntityKind, frozenset[str]] = {
    "class": frozenset({"Spell"}),
    "specialization": frozenset({"Spell"}),
    "spell": frozenset({"Spell"}),
    "talent": frozenset({"Spell"}),
    "item": frozenset({"Item"}),
    "cosmetic": frozenset({"Item", "Transmog Set"}),
    "transmog_set": frozenset({"Transmog Set"}),
    "mount": frozenset({"Item", "Spell"}),
    "pet": frozenset({"Item", "NPC", "Spell"}),
    "achievement": frozenset({"Achievement"}),
    "raid": frozenset({"Zone"}),
    "dungeon": frozenset({"Zone"}),
    "boss": frozenset({"NPC"}),
    "creature": frozenset({"NPC"}),
    "faction": frozenset({"Achievement", "NPC"}),
    "profession": frozenset({"Spell"}),
    "event": frozenset({"Achievement", "Zone"}),
}
_PAGE_SLUGS = {
    "NPC": "npc",
    "Item": "item",
    "Spell": "spell",
    "Zone": "zone",
    "Achievement": "achievement",
    "Transmog Set": "transmog-set",
}
_CLASS_CATALOG = {
    "warrior": (1, "warrior", "classicon_warrior"),
    "paladin": (2, "paladin", "classicon_paladin"),
    "hunter": (3, "hunter", "classicon_hunter"),
    "rogue": (4, "rogue", "classicon_rogue"),
    "priest": (5, "priest", "classicon_priest"),
    "death knight": (6, "death-knight", "classicon_deathknight"),
    "shaman": (7, "shaman", "classicon_shaman"),
    "mage": (8, "mage", "classicon_mage"),
    "warlock": (9, "warlock", "classicon_warlock"),
    "monk": (10, "monk", "classicon_monk"),
    "druid": (11, "druid", "classicon_druid"),
    "demon hunter": (12, "demon-hunter", "classicon_demonhunter"),
    "evoker": (13, "evoker", "classicon_evoker"),
}


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


async def resolve_entity(
    reference: WarcraftEntityRef,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> ResolvedWarcraftEntity | None:
    """Resolve an exact, typed Retail/Classic entity without trusting AI-provided URLs."""
    query = _SPACE_RE.sub(" ", reference.query).strip()
    if (
        not 1 < len(query) <= MAX_QUERY_CHARS
        or any(ord(char) < 32 for char in query)
    ):
        return None
    if reference.kind == "class" and (class_info := _CLASS_CATALOG.get(_name_key(query))):
        class_id, slug, icon = class_info
        page_branch = "classic/" if reference.branch == "classic" else ""
        return ResolvedWarcraftEntity(
            branch=reference.branch,
            kind=reference.kind,
            external_id=class_id,
            canonical_name=query,
            localized_name=reference.label.strip(),
            page_url=f"https://www.wowhead.com/{page_branch}class={class_id}/{slug}",
            icon_url=f"https://wow.zamimg.com/images/wow/icons/large/{icon}.jpg",
        )
    if reference.branch == "forever":
        return None
    branch_path = "classic/" if reference.branch == "classic" else ""
    url = f"https://www.wowhead.com/{branch_path}search/suggestions-template"
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "GildraNews/0.1 Warcraft entity resolver"},
    )
    try:
        async with client.stream("GET", url, params={"q": query}) as response:
            response.raise_for_status()
            final = urlparse(str(response.url))
            if final.scheme != "https" or final.hostname != "www.wowhead.com":
                return None
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_SEARCH_BYTES:
                    return None
                chunks.append(chunk)
        payload = json.loads(b"".join(chunks))
        results = payload.get("results", []) if isinstance(payload, dict) else []
        expected_types = _EXPECTED_TYPES[reference.kind]
        exact: list[tuple[dict, str]] = []
        for candidate in results:
            if not isinstance(candidate, dict) or _name_key(str(candidate.get("name", ""))) != _name_key(query):
                continue
            type_name = str(candidate.get("typeName") or _TYPE_CODE_NAMES.get(candidate.get("type"), ""))
            entity_id = candidate.get("id")
            if type_name not in expected_types or not isinstance(entity_id, int) or entity_id <= 0:
                continue
            exact.append((candidate, type_name))
        if not exact:
            return None
        identities = {
            (type_name, str(candidate.get("icon") or ""))
            for candidate, type_name in exact
        }
        if len(identities) > 1:
            return None
        candidate, type_name = exact[0]
        icon = str(candidate.get("icon") or "").lower()
        if icon and not _ICON_RE.fullmatch(icon):
            return None
        entity_id = int(candidate["id"])
        page_prefix = f"https://www.wowhead.com/{branch_path}"
        icon_url = (
            f"https://wow.zamimg.com/images/wow/icons/large/{icon}.jpg"
            if icon
            else ""
        )
        return ResolvedWarcraftEntity(
            branch=reference.branch,
            kind=reference.kind,
            external_id=entity_id,
            canonical_name=str(candidate.get("name") or query).strip(),
            localized_name=reference.label.strip(),
            page_url=f"{page_prefix}{_PAGE_SLUGS[type_name]}={entity_id}",
            icon_url=icon_url,
        )
    except (httpx.HTTPError, json.JSONDecodeError, TypeError, ValueError):
        return None
    finally:
        if owns_client:
            await client.aclose()
