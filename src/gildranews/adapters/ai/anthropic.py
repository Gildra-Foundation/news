from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


class AnthropicError(RuntimeError):
    """Safe provider failure; never includes credentials or source text."""


class AnthropicClient:
    """Direct official Messages API transport, independent of Codex sessions."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-5-5",
        timeout_seconds: float = 240,
        max_tokens: int = 8192,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("ANTHROPIC_API_KEY is required")
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._lock = asyncio.Lock()

    async def complete(self, system: str, user: str) -> str:
        async with self._lock:
            for attempt in range(3):
                try:
                    response = await self._http.post(
                        "https://api.anthropic.com/v1/messages",
                        headers={
                            "x-api-key": self._api_key,
                            "anthropic-version": "2023-06-01",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": self._model,
                            "max_tokens": self._max_tokens,
                            "system": system,
                            "messages": [{"role": "user", "content": user}],
                        },
                    )
                except httpx.TransportError:
                    if attempt == 2:
                        raise AnthropicError("Anthropic transport unavailable") from None
                    await asyncio.sleep(2 ** attempt)
                    continue
                if response.status_code in {429, 500, 502, 503, 504, 529} and attempt < 2:
                    try:
                        delay = min(30, max(1, float(response.headers.get("retry-after", 2 ** attempt))))
                    except ValueError:
                        delay = 2 ** attempt
                    log.warning("Anthropic retry status=%d attempt=%d", response.status_code, attempt + 1)
                    await asyncio.sleep(delay)
                    continue
                if response.is_error:
                    raise AnthropicError(f"Anthropic HTTP {response.status_code}")
                try:
                    data = response.json()
                    if data.get("stop_reason") != "end_turn":
                        raise AnthropicError("Anthropic response incomplete or refused")
                    result = "".join(
                        block["text"] for block in data["content"] if block.get("type") == "text"
                    ).strip()
                    if not result:
                        raise AnthropicError("Anthropic response contains no text")
                    return result
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise AnthropicError("Anthropic returned an invalid response") from None
        raise AnthropicError("Anthropic unavailable")

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
