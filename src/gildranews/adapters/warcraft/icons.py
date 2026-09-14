from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_ICON_HOSTS = frozenset({"wow.zamimg.com", "render.worldofwarcraft.com"})
ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
MAX_SOURCE_PIXELS = 16_000_000
MAX_SOURCE_SIDE = 4096
MAX_OUTPUT_BYTES = 256 * 1024


class IconError(ValueError):
    """The remote asset is not safe or cannot become a Telegram custom emoji."""


@dataclass(frozen=True, slots=True)
class NormalizedIcon:
    path: Path
    sha256: str
    source_url: str


def _allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ALLOWED_ICON_HOSTS


def _normalize(payload: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(payload)) as source:
            source.verify()
        with Image.open(io.BytesIO(payload)) as source:
            width, height = source.size
            if (
                width < 16
                or height < 16
                or width > MAX_SOURCE_SIDE
                or height > MAX_SOURCE_SIDE
                or width * height > MAX_SOURCE_PIXELS
            ):
                raise IconError("недопустимый размер изображения")
            image = ImageOps.fit(
                source.convert("RGBA"),
                (100, 100),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            output = io.BytesIO()
            image.save(output, format="WEBP", lossless=True, method=6)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise IconError("ответ не является безопасным изображением") from exc
    result = output.getvalue()
    if not result or len(result) > MAX_OUTPUT_BYTES:
        raise IconError("изображение не укладывается в лимит Telegram")
    return result


async def fetch_and_normalize_icon(
    url: str,
    destination_dir: Path,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> NormalizedIcon:
    if not _allowed_url(url):
        raise IconError("домен изображения не разрешён")
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "GildraNews/0.1 Warcraft icon fetcher"},
    )
    try:
        current_url = url
        chunks: list[bytes] = []
        for _redirect in range(4):
            async with client.stream("GET", current_url, follow_redirects=False) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    current_url = urljoin(current_url, location)
                    if not _allowed_url(current_url):
                        raise IconError("конечный домен изображения не разрешён")
                    continue
                response.raise_for_status()
                if not _allowed_url(str(response.url)):
                    raise IconError("конечный домен изображения не разрешён")
                mime = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if mime not in ALLOWED_MIME_TYPES:
                    raise IconError("тип изображения не разрешён")
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        raise IconError("изображение превышает лимит загрузки")
                    chunks.append(chunk)
                break
        else:
            raise IconError("слишком много перенаправлений")
        normalized = _normalize(b"".join(chunks))
        digest = hashlib.sha256(normalized).hexdigest()
        destination_dir.mkdir(parents=True, exist_ok=True)
        path = destination_dir / f"{digest}.webp"
        if not path.exists():
            path.write_bytes(normalized)
        return NormalizedIcon(path=path, sha256=digest, source_url=url)
    except httpx.HTTPError as exc:
        raise IconError("не удалось скачать изображение") from exc
    finally:
        if owns_client:
            await client.aclose()
