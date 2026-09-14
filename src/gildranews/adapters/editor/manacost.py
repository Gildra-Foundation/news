from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel, ValidationError


class _EditorResponse(BaseModel):
    text: str
    accepted: bool = False
    checks_complete: bool = False


log = logging.getLogger(__name__)


class EditorClient:
    """Client for ManacostTeam Editor gateway's stable `/v2/edit` contract."""

    def __init__(
        self,
        endpoint: str,
        token: str = "",
        timeout_seconds: float = 240,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._token = token
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def edit(self, text: str) -> str:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            response = await self._http.post(
                self._endpoint,
                headers=headers,
                json={
                    "text": text,
                    "mode": "edit",
                    "profile": "news",
                    "language": "ru-RU",
                    "retrieval": "off",
                },
            )
            response.raise_for_status()
            result = _EditorResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError):
            log.warning("EditorTeam unavailable or returned an invalid response", exc_info=True)
            return text
        if not result.accepted or not result.checks_complete or not result.text.strip():
            log.info("EditorTeam rejected the candidate; keeping the Luna version")
            return text
        return result.text.strip()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()
