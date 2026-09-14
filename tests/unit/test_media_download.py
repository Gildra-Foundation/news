from __future__ import annotations

import httpx
import pytest

from gildranews.adapters.media import download


@pytest.mark.asyncio
async def test_download_saves_bounded_wowhead_photo(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"jpeg")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        path = await download(
            "https://wow.zamimg.com/image.jpg",
            tmp_path,
            kind="photo",
            allowed_hosts={"wow.zamimg.com"},
            http_client=client,
        )
    finally:
        await client.aclose()

    assert path.suffix == ".jpg"
    assert path.read_bytes() == b"jpeg"


@pytest.mark.asyncio
async def test_download_rejects_media_from_untrusted_host(tmp_path) -> None:
    with pytest.raises(ValueError, match="домен"):
        await download(
            "http://127.0.0.1/private.jpg",
            tmp_path,
            kind="photo",
            allowed_hosts={"wow.zamimg.com"},
        )


@pytest.mark.asyncio
async def test_download_rejects_empty_media(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "video/mp4"}, content=b"")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValueError, match="Пустой"):
            await download(
                "https://wow.zamimg.com/clip.mp4",
                tmp_path,
                kind="video",
                allowed_hosts={"wow.zamimg.com"},
                http_client=client,
            )
    finally:
        await client.aclose()

    assert not list(tmp_path.iterdir())
