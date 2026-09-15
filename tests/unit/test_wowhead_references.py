from __future__ import annotations

import httpx
import pytest

from gildranews.adapters.references.wowhead import resolve_entity, resolve_reference
from gildranews.domain.models import WarcraftEntityRef


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "kind", "payload", "expected"),
    [
        (
            "Venomous Abyss",
            "raid",
            '[{"id":16915,"instance":7,"name":"The Venomous Abyss"}]',
            "https://www.wowhead.com/zone=16915",
        ),
        (
            "Sszorak",
            "creature",
            '[{"boss":1,"classification":1,"id":257347,"name":"Sszorak"}]',
            "https://www.wowhead.com/npc=257347",
        ),
    ],
)
async def test_resolve_reference_uses_exact_wowhead_entity(
    query: str,
    kind: str,
    payload: str,
    expected: str,
) -> None:
    document = (
        '<script type="application/json" id="data.wowhead-result">'
        f"{payload}</script>"
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.wowhead.com"
        assert request.url.path == "/search"
        return httpx.Response(200, request=request, text=document)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_reference(query, kind, http_client=client)

    assert result == expected


@pytest.mark.asyncio
async def test_resolve_reference_returns_none_without_exact_entity() -> None:
    document = (
        '<script type="application/json">'
        '[{"id":16915,"instance":7,"name":"Another Raid"}]'
        '</script>'
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, text=document)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_reference("Venomous Abyss", "raid", http_client=client)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_entity_uses_typed_classic_search_result_and_icon() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/classic/search/suggestions-template"
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {
                        "type": 6,
                        "typeName": "Spell",
                        "id": 133,
                        "name": "Fireball",
                        "icon": "spell_fire_flamebolt",
                    },
                    {"type": 3, "typeName": "Item", "id": 1, "name": "Fireball"},
                ]
            },
        )

    reference = WarcraftEntityRef(
        label="Огненный шар",
        query="Fireball",
        kind="spell",
        branch="classic",
        role="primary",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_entity(reference, http_client=client)

    assert result is not None
    assert result.external_id == 133
    assert result.branch == "classic"
    assert result.page_url == "https://www.wowhead.com/classic/spell=133"
    assert result.icon_url.endswith("/spell_fire_flamebolt.jpg")


@pytest.mark.asyncio
async def test_resolve_entity_rejects_ambiguous_exact_icons() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {"type": 6, "id": 133, "name": "Fireball", "icon": "fire_one"},
                    {"type": 6, "id": 999, "name": "Fireball", "icon": "fire_two"},
                ]
            },
        )

    reference = WarcraftEntityRef("Огненный шар", "Fireball", "spell")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_entity(reference, http_client=client)

    assert result is None


@pytest.mark.asyncio
async def test_resolve_entity_does_not_search_forever_in_retail_database() -> None:
    reference = WarcraftEntityRef(
        "Неизвестная способность",
        "Unknown Ability",
        "spell",
        branch="forever",
    )

    assert await resolve_entity(reference) is None


@pytest.mark.asyncio
async def test_class_resolver_uses_stable_core_catalog_without_network() -> None:
    reference = WarcraftEntityRef(
        label="мага",
        query="Mage",
        kind="class",
        branch="forever",
        role="primary",
    )

    result = await resolve_entity(reference)

    assert result is not None
    assert result.external_id == 8
    assert result.icon_url.endswith("/classicon_mage.jpg")
    assert result.page_url == "https://www.wowhead.com/class=8/mage"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "type_code", "type_name", "page_slug"),
    [
        ("specialization", 6, "Spell", "spell"),
        ("spell", 6, "Spell", "spell"),
        ("talent", 6, "Spell", "spell"),
        ("item", 3, "Item", "item"),
        ("cosmetic", 3, "Item", "item"),
        ("transmog_set", 101, "Transmog Set", "transmog-set"),
        ("mount", 6, "Spell", "spell"),
        ("pet", 1, "NPC", "npc"),
        ("achievement", 10, "Achievement", "achievement"),
        ("raid", 7, "Zone", "zone"),
        ("dungeon", 7, "Zone", "zone"),
        ("boss", 1, "NPC", "npc"),
        ("creature", 1, "NPC", "npc"),
        ("faction", 1, "NPC", "npc"),
        ("profession", 6, "Spell", "spell"),
        ("event", 7, "Zone", "zone"),
    ],
)
async def test_resolver_supports_every_dynamic_entity_kind(
    kind, type_code, type_name, page_slug,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {
                        "type": type_code,
                        "typeName": type_name,
                        "id": 777,
                        "name": "Exact Entity",
                        "icon": "inv_misc_questionmark",
                    }
                ]
            },
        )

    reference = WarcraftEntityRef("Сущность", "Exact Entity", kind)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_entity(reference, http_client=client)

    assert result is not None
    assert result.kind == kind
    assert result.page_url == f"https://www.wowhead.com/{page_slug}=777"
