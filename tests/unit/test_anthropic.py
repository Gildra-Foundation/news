from __future__ import annotations

import json

import httpx
import pytest

from gildranews import config
from gildranews.adapters.ai.anthropic import AnthropicClient, AnthropicError
from gildranews.adapters.ai.provider import AppServerContentAI, build_content_ai


@pytest.mark.asyncio
async def test_official_api_uses_sonnet_and_extracts_only_public_text() -> None:
    async def handler(request):
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == "private-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
        payload = json.loads(request.content)
        assert payload["model"] == "claude-sonnet-5-5"
        assert payload["system"] == "editorial rules"
        assert payload["messages"] == [{"role": "user", "content": "source"}]
        return httpx.Response(200, json={"stop_reason": "end_turn", "content": [
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "text", "text": "result"},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await AnthropicClient("private-key", http_client=http).complete("editorial rules", "source") == "result"


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_reason", ["max_tokens", "refusal", "pause_turn", None])
async def test_incomplete_or_refused_response_is_not_published(stop_reason) -> None:
    async def handler(_request):
        return httpx.Response(200, json={"stop_reason": stop_reason, "content": [{"type": "text", "text": "{}"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(AnthropicError, match="incomplete"):
            await AnthropicClient("key", http_client=http).complete("system", "source")


@pytest.mark.asyncio
async def test_authentication_error_does_not_retry_or_expose_key() -> None:
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(401, text="private-key private source")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(AnthropicError, match="^Anthropic HTTP 401$"):
            await AnthropicClient("private-key", http_client=http).complete("system", "source")
    assert calls == 1


@pytest.mark.asyncio
async def test_rate_limit_retries_are_bounded(monkeypatch) -> None:
    calls = 0

    async def no_sleep(_delay):
        pass

    monkeypatch.setattr("gildranews.adapters.ai.anthropic.asyncio.sleep", no_sleep)

    async def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"retry-after": "0"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(AnthropicError, match="429"):
            await AnthropicClient("key", http_client=http).complete("system", "source")
    assert calls == 3


@pytest.mark.asyncio
async def test_config_builds_claude_editorial_pipeline(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "bot-token")
    monkeypatch.setenv("TARGET_CHANNEL", "channel")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-key")
    monkeypatch.setenv("NEWS_SELECTOR_ENABLED", "false")
    monkeypatch.setenv("EDITOR_URL", "")
    cfg = config.load()
    content_ai = build_content_ai(cfg)
    try:
        assert cfg.anthropic_model == "claude-sonnet-5-5"
        assert isinstance(content_ai, AppServerContentAI)
        assert isinstance(content_ai._app_server, AnthropicClient)
    finally:
        await content_ai.aclose()


def test_claude_config_requires_credentials(monkeypatch) -> None:
    monkeypatch.setenv("TARGET_CHANNEL", "channel")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY_FILE", "/nonexistent/key")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        config.load()


def test_new_installations_default_to_sonnet(monkeypatch) -> None:
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "private-key")
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("TARGET_CHANNEL", "channel")
    assert config.load().ai_provider == "anthropic"
