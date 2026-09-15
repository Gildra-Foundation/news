from __future__ import annotations

import httpx
import pytest

from gildranews.adapters.media.scrape_do_images import find_warcraft_image


@pytest.mark.asyncio
async def test_search_finds_og_image_from_relevant_allowlisted_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.scrape.do"
        assert request.url.path == "/plugin/google/search"
        assert request.url.params["token"] == "secret"
        assert request.url.params["safe"] == "active"
        return httpx.Response(
            200,
            request=request,
            json={
                "organic_results": [
                    {
                        "title": "Venomous Abyss raid guide",
                        "link": "https://evil.example/venomous-abyss",
                        "snippet": "World of Warcraft raid",
                    },
                    {
                        "title": "Venomous Abyss Raid Changes",
                        "link": "https://www.icy-veins.com/wow/news/venomous-abyss/",
                        "snippet": "Boss changes in World of Warcraft",
                    },
                ]
            },
        )

    async def fetch_page(url: str) -> bytes:
        assert url == "https://www.icy-veins.com/wow/news/venomous-abyss/"
        return (
            b'<meta property="og:image" content="'
            b'https://static.icy-veins.com/wp/venomous-abyss-raid.webp">'
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await find_warcraft_image(
            "Venomous Abyss",
            token="secret",
            http_client=client,
            page_fetcher=fetch_page,
        )

    assert result == "https://static.icy-veins.com/wp/venomous-abyss-raid.webp"


@pytest.mark.asyncio
async def test_search_rejects_image_from_untrusted_cdn() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "organic_results": [
                    {
                        "title": "Venomous Abyss Raid Changes",
                        "link": "https://www.wowhead.com/news/venomous-abyss-raid",
                        "snippet": "Venomous Abyss",
                    }
                ]
            },
        )

    async def fetch_page(_url: str) -> bytes:
        return b'<meta property="og:image" content="https://evil.example/image.jpg">'

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await find_warcraft_image(
            "Venomous Abyss",
            token="secret",
            http_client=client,
            page_fetcher=fetch_page,
        )

    assert result is None
