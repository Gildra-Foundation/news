from __future__ import annotations

import json

import httpx
import pytest

from gildranews.adapters.editor.manacost import EditorClient


@pytest.mark.asyncio
async def test_editor_accepts_only_completed_approved_result() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"text": "Исправленный текст", "accepted": True, "checks_complete": True},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    editor = EditorClient("http://editor-gateway:8080/v2/edit", http_client=http)

    assert await editor.edit("Исходный текст") == "Исправленный текст"
    assert captured["payload"]["profile"] == "news"
    assert captured["payload"]["language"] == "ru-RU"
    await http.aclose()


@pytest.mark.asyncio
async def test_editor_keeps_source_when_candidate_is_rejected() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"text": "Плохая правка", "accepted": False, "checks_complete": True},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    editor = EditorClient("http://editor-gateway:8080/v2/edit", http_client=http)

    assert await editor.edit("Исходный текст") == "Исходный текст"
    await http.aclose()
