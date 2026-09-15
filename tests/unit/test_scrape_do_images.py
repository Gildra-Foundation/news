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
        assert request.url.params["resolveGoto"] == "true"
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


@pytest.mark.asyncio
async def test_search_ranks_exact_kind_specific_result_above_first_match() -> None:
    fetched: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "organic_results": [
                    {
                        "title": "Venomous Abyss overview",
                        "link": "https://www.wowhead.com/news/venomous-abyss-overview",
                        "snippet": "General World of Warcraft location information",
                    },
                    {
                        "title": "Venomous Abyss Raid Boss Changes",
                        "link": "https://www.icy-veins.com/wow/news/venomous-abyss-bosses/",
                        "snippet": "Raid tuning and boss changes",
                    },
                ],
            },
        )

    async def fetch_page(url: str) -> bytes:
        fetched.append(url)
        slug = "raid-bosses" if "bosses" in url else "generic"
        return (
            f'<meta property="og:image" content="https://static.icy-veins.com/{slug}.jpg">'
        ).encode()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await find_warcraft_image(
            "Venomous Abyss",
            entity_kind="raid",
            token="secret",
            http_client=client,
            page_fetcher=fetch_page,
        )

    assert result == "https://static.icy-veins.com/raid-bosses.jpg"
    assert fetched[0].endswith("/venomous-abyss-bosses/")


@pytest.mark.asyncio
async def test_search_selects_large_article_image_instead_of_site_logo() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "organic_results": [
                    {
                        "title": "Venomous Abyss Raid Changes",
                        "link": "https://www.icy-veins.com/wow/news/venomous-abyss/",
                        "snippet": "Venomous Abyss raid bosses",
                    },
                ],
            },
        )

    async def fetch_page(_url: str) -> bytes:
        return b"".join(
            (
                b'<meta property="og:image" content="https://static.icy-veins.com/site-logo.png">',
                b'<meta property="og:image:width" content="200">',
                b'<meta property="og:image:height" content="200">',
                b'<meta property="og:image" content="https://static.icy-veins.com/venomous-abyss.jpg">',
                b'<meta property="og:image:width" content="1200">',
                b'<meta property="og:image:height" content="675">',
                b'<meta property="og:image:alt" content="Venomous Abyss raid bosses">',
            )
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await find_warcraft_image(
            "Venomous Abyss",
            entity_kind="raid",
            token="secret",
            http_client=client,
            page_fetcher=fetch_page,
        )

    assert result == "https://static.icy-veins.com/venomous-abyss.jpg"
