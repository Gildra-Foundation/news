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
    "raid": frozenset({"Zone", "Achievement"}),
    "dungeon": frozenset({"Zone", "Achievement"}),
    "boss": frozenset({"NPC"}),
    "creature": frozenset({"NPC"}),
    "faction": frozenset({"Achievement", "NPC"}),
    "profession": frozenset({"Spell"}),
    "event": frozenset({"Achievement", "Zone"}),
}
_TYPE_PRIORITY: dict[WarcraftEntityKind, tuple[str, ...]] = {
    "mount": ("Spell", "Item"),
    "pet": ("NPC", "Item", "Spell"),
    "cosmetic": ("Transmog Set", "Item"),
    "faction": ("NPC", "Achievement"),
    "event": ("Zone", "Achievement"),
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
_SPECIALIZATION_CATALOG = {
    "arms warrior": (71, 1, "warrior", "ability_warrior_savageblow"),
    "fury warrior": (72, 1, "warrior", "ability_warrior_innerrage"),
    "protection warrior": (73, 1, "warrior", "ability_warrior_defensivestance"),
    "holy paladin": (65, 2, "paladin", "spell_holy_holybolt"),
    "protection paladin": (66, 2, "paladin", "ability_paladin_shieldofthetemplar"),
    "augmentation": (1473, 13, "evoker", "classicon_evoker_augmentation"),
    "augmentation evoker": (1473, 13, "evoker", "classicon_evoker_augmentation"),
    "devastation": (1467, 13, "evoker", "classicon_evoker_devastation"),
    "devastation evoker": (1467, 13, "evoker", "classicon_evoker_devastation"),
    "preservation evoker": (1468, 13, "evoker", "classicon_evoker_preservation"),
    "retribution": (70, 2, "paladin", "spell_holy_auraoflight"),
    "retribution paladin": (70, 2, "paladin", "spell_holy_auraoflight"),
    "beast mastery hunter": (253, 3, "hunter", "ability_hunter_bestialdiscipline"),
    "marksmanship hunter": (254, 3, "hunter", "ability_hunter_focusedaim"),
    "survival hunter": (255, 3, "hunter", "ability_hunter_camouflage"),
    "assassination rogue": (259, 4, "rogue", "ability_rogue_deadliness"),
    "outlaw rogue": (260, 4, "rogue", "ability_rogue_waylay"),
    "subtlety rogue": (261, 4, "rogue", "ability_stealth"),
    "discipline priest": (256, 5, "priest", "spell_holy_powerwordshield"),
    "holy priest": (257, 5, "priest", "spell_holy_guardianspirit"),
    "shadow priest": (258, 5, "priest", "spell_shadow_shadowwordpain"),
    "blood death knight": (250, 6, "death-knight", "spell_deathknight_bloodpresence"),
    "frost death knight": (251, 6, "death-knight", "spell_deathknight_frostpresence"),
    "unholy death knight": (252, 6, "death-knight", "spell_deathknight_unholypresence"),
    "elemental shaman": (262, 7, "shaman", "spell_nature_lightning"),
    "enhancement shaman": (263, 7, "shaman", "spell_shaman_improvedreincarnation"),
    "restoration shaman": (264, 7, "shaman", "spell_nature_magicimmunity"),
    "arcane mage": (62, 8, "mage", "spell_holy_magicalsentry"),
    "fire mage": (63, 8, "mage", "spell_fire_firebolt02"),
    "frost mage": (64, 8, "mage", "spell_frost_frostbolt02"),
    "affliction warlock": (265, 9, "warlock", "spell_shadow_deathcoil"),
    "demonology warlock": (266, 9, "warlock", "spell_shadow_metamorphosis"),
    "destruction warlock": (267, 9, "warlock", "spell_shadow_rainoffire"),
    "brewmaster monk": (268, 10, "monk", "spell_monk_brewmaster_spec"),
    "windwalker monk": (269, 10, "monk", "spell_monk_windwalker_spec"),
    "mistweaver monk": (270, 10, "monk", "spell_monk_mistweaver_spec"),
    "balance druid": (102, 11, "druid", "spell_nature_starfall"),
    "feral druid": (103, 11, "druid", "ability_druid_catform"),
    "guardian druid": (104, 11, "druid", "ability_racial_bearform"),
    "restoration druid": (105, 11, "druid", "spell_nature_healingtouch"),
    "havoc demon hunter": (577, 12, "demon-hunter", "ability_demonhunter_specdps"),
    "vengeance demon hunter": (581, 12, "demon-hunter", "ability_demonhunter_spectank"),
    "devourer": (1213636, 12, "demon-hunter", "classicon_demonhunter_void"),
    "devourer demon hunter": (
        1213636,
        12,
        "demon-hunter",
        "classicon_demonhunter_void",
    ),
}
_SPELL_CATALOG = {
    "hungering slash": (1239519, "inv_12_dh_void_ability_reapersslice"),
    "nature's bounty": (1263879, "talentspec_druid_restoration"),
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
    if (
        reference.kind == "specialization"
        and reference.branch == "retail"
        and (specialization_info := _SPECIALIZATION_CATALOG.get(_name_key(query)))
    ):
        specialization_id, class_id, class_slug, icon = specialization_info
        return ResolvedWarcraftEntity(
            branch=reference.branch,
            kind=reference.kind,
            external_id=specialization_id,
            canonical_name=query,
            localized_name=reference.label.strip(),
            page_url=f"https://www.wowhead.com/class={class_id}/{class_slug}",
            icon_url=f"https://wow.zamimg.com/images/wow/icons/large/{icon}.jpg",
        )
    if (
        reference.kind in {"spell", "talent"}
        and reference.branch == "retail"
        and (spell_info := _SPELL_CATALOG.get(_name_key(query)))
    ):
        spell_id, icon = spell_info
        return ResolvedWarcraftEntity(
            branch=reference.branch,
            kind=reference.kind,
            external_id=spell_id,
            canonical_name=query,
            localized_name=reference.label.strip(),
            page_url=f"https://www.wowhead.com/spell={spell_id}",
            icon_url=f"https://wow.zamimg.com/images/wow/icons/large/{icon}.jpg",
        )
    if reference.branch == "forever":
        return None
    expected_types = _EXPECTED_TYPES.get(reference.kind)
    if expected_types is None:
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
        if reference.kind in {"raid", "dungeon"}:
            marker = "raid" if reference.kind == "raid" else "dungeon"
            all_zones = [
                candidate for candidate, type_name in exact if type_name == "Zone"
            ]
            zones = [
                candidate
                for candidate in all_zones
                if marker in " ".join(
                    str(part) for part in candidate.get("pinBreadcrumb", [])
                ).casefold()
            ]
            if not zones and len(all_zones) == 1:
                zones = all_zones
            if len(zones) != 1:
                return None
            page_candidate = zones[0]
            icon_candidates = [
                candidate
                for candidate, type_name in exact
                if type_name == "Achievement"
                and _ICON_RE.fullmatch(str(candidate.get("icon", "")).lower())
                and marker in " ".join(
                    str(part) for part in candidate.get("pinBreadcrumb", [])
                ).casefold()
            ]
            page_icon = str(page_candidate.get("icon") or "").lower()
            icon = page_icon if _ICON_RE.fullmatch(page_icon) else ""
            if not icon and icon_candidates:
                icon_candidate = min(
                    icon_candidates,
                    key=lambda candidate: int(candidate.get("popularity", 10_000)),
                )
                icon = str(icon_candidate.get("icon") or "").lower()
            entity_id = int(page_candidate["id"])
            page_prefix = f"https://www.wowhead.com/{branch_path}"
            return ResolvedWarcraftEntity(
                branch=reference.branch,
                kind=reference.kind,
                external_id=entity_id,
                canonical_name=str(page_candidate.get("name") or query).strip(),
                localized_name=reference.label.strip(),
                page_url=f"{page_prefix}zone={entity_id}",
                icon_url=(
                    f"https://wow.zamimg.com/images/wow/icons/large/{icon}.jpg"
                    if icon
                    else ""
                ),
            )
        priority = _TYPE_PRIORITY.get(reference.kind)
        if priority:
            best_rank = min(priority.index(type_name) for _candidate, type_name in exact)
            exact = [
                (candidate, type_name)
                for candidate, type_name in exact
                if priority.index(type_name) == best_rank
            ]
        identities = {
            (type_name, int(candidate["id"]))
            for candidate, type_name in exact
        }
        if len(identities) != 1:
            return None
        valid_icons = [
            (candidate, type_name)
            for candidate, type_name in exact
            if not candidate.get("icon")
            or _ICON_RE.fullmatch(str(candidate.get("icon")).lower())
        ]
        if not valid_icons:
            return None
        candidate, type_name = max(
            valid_icons,
            key=lambda value: bool(value[0].get("icon")),
        )
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
