from __future__ import annotations

import asyncio
from collections.abc import Set as AbstractSet
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image

PHOTO_LIMIT_BYTES = 10 * 1024 * 1024
VIDEO_LIMIT_BYTES = 48 * 1024 * 1024
_MEDIA_TYPES = {
    "photo": {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"},
    "video": {"video/mp4": ".mp4"},
}


def _validated_url(url: str, allowed_hosts: AbstractSet[str]) -> str:
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or hostname not in allowed_hosts:
        raise ValueError("Недопустимый домен медиа")
    return parsed.geturl()


def _webp_to_jpeg(source: Path) -> Path:
    destination = source.with_suffix(".jpg")
    with Image.open(source) as image:
        image.convert("RGB").save(destination, format="JPEG", quality=92, optimize=True)
    source.unlink()
    return destination


async def download(
    url: str,
    destination_dir: Path,
    *,
    kind: str,
    allowed_hosts: AbstractSet[str],
    http_client: httpx.AsyncClient | None = None,
) -> Path:
    accepted_types = _MEDIA_TYPES.get(kind)
    if accepted_types is None:
        raise ValueError("Неизвестный тип медиа")
    safe_url = _validated_url(url, allowed_hosts)
    limit = PHOTO_LIMIT_BYTES if kind == "photo" else VIDEO_LIMIT_BYTES
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "GildraNews/0.1 media fetcher"},
    )
    output_path: Path | None = None
    try:
        async with client.stream("GET", safe_url) as response:
            response.raise_for_status()
            _validated_url(str(response.url), allowed_hosts)
            media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            suffix = accepted_types.get(media_type)
            if suffix is None:
                raise ValueError("Неподдерживаемый формат медиа")
            content_length = response.headers.get("content-length")
            if content_length and (not content_length.isdigit() or int(content_length) > limit):
                raise ValueError("Медиафайл слишком большой")

            output_path = destination_dir / f"source-{kind}{suffix}"
            size = 0
            with output_path.open("wb") as output:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise ValueError("Медиафайл слишком большой")
                    output.write(chunk)
            if size == 0:
                raise ValueError("Пустой медиафайл")
        if kind == "photo" and output_path.suffix == ".webp":
            output_path = await asyncio.to_thread(_webp_to_jpeg, output_path)
        return output_path
    except Exception:
        if output_path is not None:
            output_path.unlink(missing_ok=True)
        raise
    finally:
        if owns_client:
            await client.aclose()
