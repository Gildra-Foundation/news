from __future__ import annotations

import io

import httpx
import pytest
from PIL import Image

from gildranews.adapters.warcraft.icons import IconError, fetch_and_normalize_icon


def _png(width: int = 180, height: int = 120) -> bytes:
    image = Image.new("RGBA", (width, height), (180, 30, 20, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_fetch_icon_normalizes_allowlisted_image_to_telegram_size(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_png())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = await fetch_and_normalize_icon(
        "https://wow.zamimg.com/images/icon.png",
        tmp_path,
        http_client=client,
    )

    with Image.open(result.path) as image:
        assert image.size == (100, 100)
        assert image.format == "WEBP"
    assert len(result.sha256) == 64
    assert result.source_url == "https://wow.zamimg.com/images/icon.png"
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_icon_rejects_redirect_to_untrusted_host(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://evil.example/icon.png"},
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    with pytest.raises(IconError, match="домен"):
        await fetch_and_normalize_icon(
            "https://wow.zamimg.com/redirect",
            tmp_path,
            http_client=client,
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_fetch_icon_rejects_invalid_image_content(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"not an image",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(IconError, match="изображение"):
        await fetch_and_normalize_icon(
            "https://render.worldofwarcraft.com/icon.png",
            tmp_path,
            http_client=client,
        )
    await client.aclose()
