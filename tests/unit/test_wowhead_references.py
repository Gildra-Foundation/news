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
async def test_resolve_raid_uses_raid_zone_with_matching_achievement_icon() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {
                        "type": 7,
                        "typeName": "Zone",
                        "id": 5723,
                        "name": "Firelands",
                        "pinBreadcrumb": ["Raid"],
                    },
                    {
                        "type": 7,
                        "typeName": "Zone",
                        "id": 10022,
                        "name": "Firelands",
                        "pinBreadcrumb": ["Scenario"],
                    },
                    {
                        "type": 10,
                        "typeName": "Achievement",
                        "id": 5802,
                        "name": "Firelands",
                        "icon": "achievement_zone_firelands",
                        "pinBreadcrumb": ["Dungeons & Raids", "Cataclysm Raid"],
                    },
                ],
            },
        )

    reference = WarcraftEntityRef(
        "Огненные просторы", "Firelands", "raid", role="primary",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_entity(reference, http_client=client)

    assert result is not None
    assert result.external_id == 5723
    assert result.page_url == "https://www.wowhead.com/zone=5723"
    assert result.icon_url.endswith("/achievement_zone_firelands.jpg")


@pytest.mark.asyncio
async def test_resolve_entity_prefers_spell_for_mount_over_item_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {
                        "type": 3,
                        "typeName": "Item",
                        "id": 111,
                        "name": "Ashes of Al'ar",
                        "icon": "inv_misc_summerfest_brazierorange",
                    },
                    {
                        "type": 6,
                        "typeName": "Spell",
                        "id": 40192,
                        "name": "Ashes of Al'ar",
                        "icon": "ability_mount_fireravengodmount",
                    },
                ],
            },
        )

    reference = WarcraftEntityRef("Пепел Ал'ара", "Ashes of Al'ar", "mount")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_entity(reference, http_client=client)

    assert result is not None
    assert result.external_id == 40192
    assert result.page_url == "https://www.wowhead.com/spell=40192"
    assert result.icon_url.endswith("/ability_mount_fireravengodmount.jpg")


@pytest.mark.asyncio
async def test_resolve_entity_rejects_different_ids_even_with_same_icon() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "results": [
                    {"type": 6, "id": 133, "name": "Fireball", "icon": "fireball"},
                    {"type": 6, "id": 999, "name": "Fireball", "icon": "fireball"},
                ],
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
async def test_resolve_entity_leaves_expansion_logos_to_curated_catalog() -> None:
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500, request=request)

    reference = WarcraftEntityRef(
        "The Last Titan", "The Last Titan", "expansion", role="primary",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_entity(reference, http_client=client)

    assert result is None
    assert called is False


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
    ("query", "label", "specialization_id", "class_id", "class_slug", "icon"),
    [
        (
            "Augmentation",
            "Насыщатель",
            1473,
            13,
            "evoker",
            "classicon_evoker_augmentation",
        ),
        (
            "Devastation",
            "Опустошитель",
            1467,
            13,
            "evoker",
            "classicon_evoker_devastation",
        ),
        (
            "Retribution",
            "Воздаяние",
            70,
            2,
            "paladin",
            "spell_holy_auraoflight",
        ),
        (
            "Frost Mage",
            "Лёд",
            64,
            8,
            "mage",
            "spell_frost_frostbolt02",
        ),
        (
            "Devourer Demon Hunter",
            "Пожиратель",
            1213636,
            12,
            "demon-hunter",
            "classicon_demonhunter_void",
        ),
        (
            "Restoration Druid",
            "Восстановление",
            105,
            11,
            "druid",
            "spell_nature_healingtouch",
        ),
        (
            "Preservation Evoker",
            "Сохранение",
            1468,
            13,
            "evoker",
            "classicon_evoker_preservation",
        ),
        (
            "Arcane Mage",
            "Тайная магия",
            62,
            8,
            "mage",
            "spell_holy_magicalsentry",
        ),
        (
            "Discipline Priest",
            "Послушание",
            256,
            5,
            "priest",
            "spell_holy_powerwordshield",
        ),
        (
            "Holy Priest",
            "Свет",
            257,
            5,
            "priest",
            "spell_holy_guardianspirit",
        ),
        (
            "Outlaw Rogue",
            "Головорез",
            260,
            4,
            "rogue",
            "ability_rogue_waylay",
        ),
        (
            "Subtlety Rogue",
            "Скрытность",
            261,
            4,
            "rogue",
            "ability_stealth",
        ),
        (
            "Protection Warrior",
            "Защита",
            73,
            1,
            "warrior",
            "ability_warrior_defensivestance",
        ),
    ],
)
async def test_specialization_resolver_uses_verified_core_catalog_without_network(
    query,
    label,
    specialization_id,
    class_id,
    class_slug,
    icon,
) -> None:
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500, request=request)

    reference = WarcraftEntityRef(
        label=label,
        query=query,
        kind="specialization",
        role="primary",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_entity(reference, http_client=client)

    assert result is not None
    assert result.external_id == specialization_id
    assert result.page_url == f"https://www.wowhead.com/class={class_id}/{class_slug}"
    assert result.icon_url.endswith(f"/{icon}.jpg")
    assert called is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "label", "spell_id", "icon"),
    [
        (
            "Hungering Slash",
            "Голодное рассечение",
            1239519,
            "inv_12_dh_void_ability_reapersslice",
        ),
        (
            "Nature's Bounty",
            "Природное изобилие",
            1263879,
            "talentspec_druid_restoration",
        ),
        (
            "Merithra's Blessing",
            "Благословение Меритры",
            1256577,
            "inv12_apextalent_evoker_merithrasblessing",
        ),
    ],
)
async def test_spell_resolver_uses_verified_ability_catalog_without_network(
    query,
    label,
    spell_id,
    icon,
) -> None:
    called = False

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500, request=request)

    reference = WarcraftEntityRef(label, query, "spell", role="primary")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await resolve_entity(reference, http_client=client)

    assert result is not None
    assert result.external_id == spell_id
    assert result.page_url == f"https://www.wowhead.com/spell={spell_id}"
    assert result.icon_url.endswith(f"/{icon}.jpg")
    assert called is False


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
