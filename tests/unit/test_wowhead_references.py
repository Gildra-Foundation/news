from __future__ import annotations

import httpx
import pytest

from gildranews.adapters.references.wowhead import resolve_reference


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
