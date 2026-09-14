from __future__ import annotations

import json

import httpx
import pytest

from gildranews.adapters.ai.app_server import AppServerClient, AppServerError, parse_agui_sse
from gildranews.adapters.ai.provider import AppServerContentAI


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def test_parse_agui_sse_joins_last_assistant_message() -> None:
    body = "".join(
        [
            _event({"type": "TEXT_MESSAGE_START", "messageId": "a", "role": "assistant"}),
            _event({"type": "TEXT_MESSAGE_CONTENT", "messageId": "a", "delta": "old"}),
            _event({"type": "TEXT_MESSAGE_END", "messageId": "a"}),
            _event({"type": "TEXT_MESSAGE_START", "messageId": "b", "role": "assistant"}),
            _event({"type": "TEXT_MESSAGE_CONTENT", "messageId": "b", "delta": "{\"ok\":"}),
            _event({"type": "TEXT_MESSAGE_CONTENT", "messageId": "b", "delta": "true}"}),
        ]
    )

    assert parse_agui_sse(body) == '{"ok":true}'


def test_parse_agui_sse_rejects_failed_run() -> None:
    with pytest.raises(AppServerError, match="RUN_ERROR"):
        parse_agui_sse(_event({"type": "RUN_ERROR", "message": "secret details"}))


@pytest.mark.asyncio
async def test_client_sends_luna_model_and_internal_token_header() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_event({"type": "TEXT_MESSAGE_CONTENT", "delta": "done"}),
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AppServerClient(
        endpoint="http://agent-codex:4202/ag-ui",
        token="internal-token",
        model="gpt-5.6-luna",
        reasoning_effort="xhigh",
        http_client=http,
    )

    assert await client.complete("system", "user") == "done"
    assert captured["headers"]["x-openbot-agent-token"] == "internal-token"
    assert captured["payload"]["forwardedProps"] == {
        "openbotAgentModel": "gpt-5.6-luna",
        "openbotAgentReasoningEffort": "xhigh",
    }
    assert [message["role"] for message in captured["payload"]["messages"]] == [
        "system",
        "user",
    ]

    await http.aclose()


class _StubAppServer:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.system = ""
        self.user = ""

    async def complete(self, system: str, user: str) -> str:
        self.system = system
        self.user = user
        return json.dumps(self.response, ensure_ascii=False)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_luna_translation_is_rejected_when_a_number_is_lost() -> None:
    processor = AppServerContentAI(
        _StubAppServer(
            {"title": "Новая модель", "body": "Модель стала быстрее", "hashtag": "новости"},
        ),
    )

    result = await processor.translate("Model 5.6 became faster")

    assert result is None


@pytest.mark.asyncio
async def test_luna_filter_keeps_only_source_backed_infographic() -> None:
    processor = AppServerContentAI(
        _StubAppServer(
            {
                "is_news": True,
                "reason": "Есть измеримый результат",
                "title": "Модель ускорила генерацию",
                "body": "Скорость выросла на 40%.",
                "hashtag": "новости",
                "infographic": {
                    "title": "Рост скорости",
                    "facts": [
                        {"value": "40%", "label": "рост скорости"},
                        {"value": "5.6", "label": "версия модели"},
                    ],
                },
            },
        ),
    )

    result = await processor.filter_and_rewrite(
        "Model 5.6 is 40% faster", [], [],
    )

    assert result is not None
    assert result.infographic is not None
    assert [fact.value for fact in result.infographic.facts] == ["40%", "5.6"]
    assert result.infographic.source == ""


@pytest.mark.asyncio
async def test_luna_news_analysis_uses_full_wow_context_and_hides_source() -> None:
    app_server = _StubAppServer(
        {
            "is_news": True,
            "reason": "Важный хотфикс",
            "title": "Blizzard меняет механику миникарты",
            "body": "Хотфикс ограничит подсказки аддонов внутри подземелий.",
            "hashtag": "новости",
        },
    )
    processor = AppServerContentAI(app_server)
    source_text = "World of Warcraft " + ("x" * 5_000) + " final fact"

    recent_posts = [
        {
            "title": "Blizzard меняет механику миникарты",
            "body": "Ранее компания ограничила подсказки аддонов в подземельях.",
            "posted_at": "2026-09-13 12:00:00",
        },
    ]
    result = await processor.filter_and_rewrite(source_text, recent_posts, [])

    payload = json.loads(app_server.user)
    assert result is not None
    assert len(payload["post"]) > 3_000
    assert "final fact" in payload["post"]
    assert payload["recent_published"] == recent_posts
    assert "без новых существенных фактов" in app_server.system.lower()
    assert "tier set" in app_server.system.lower()
    assert "классовый комплект" in app_server.system.lower()
    assert "off-piece" in app_server.system.lower()
    assert "смена внешнего вида" in app_server.system.lower()
    assert "кратко, но полно" in app_server.system.lower()
    assert "250–600 символов" in app_server.system.lower()
    assert "не вырезай условие, дату, число или исключение" in app_server.system.lower()
    assert "простыми русскими конструкциями" in app_server.system.lower()
    assert "не пиши «level 20»" in app_server.system.lower()
    assert "число — в value" in app_server.system.lower()
    assert "World of Warcraft" in app_server.system
    assert "карта фактов" in app_server.system.lower()
    assert "не указывай источник" in app_server.system.lower()


@pytest.mark.asyncio
async def test_luna_uses_reddit_editorial_policy_for_community_topics() -> None:
    app_server = _StubAppServer(
        {
            "is_news": True,
            "reason": "Практический совет для игроков",
            "title": "Игроки нашли короткий маршрут",
            "body": "Маршрут позволяет пропустить две опасные группы противников.",
            "hashtag": "советы",
        },
    )
    processor = AppServerContentAI(app_server)

    result = await processor.filter_and_rewrite(
        "A route skips two dangerous pulls.", [], [], content_kind="reddit_topic",
    )

    assert result is not None
    assert "не выдавай мнение" in app_server.system.lower()
    assert "смысловым дублем" in app_server.system.lower()
    assert "не указывай reddit" in app_server.system.lower()


@pytest.mark.asyncio
async def test_luna_uses_x_editorial_policy_for_social_topics() -> None:
    app_server = _StubAppServer(
        {
            "is_news": True,
            "reason": "Полезное наблюдение игроков",
            "title": "Игроки уточнили работу механики",
            "body": "Наблюдение стоит учитывать перед прохождением подземелья.",
            "hashtag": "обсуждения",
        },
    )
    processor = AppServerContentAI(app_server)

    result = await processor.filter_and_rewrite(
        "One player's observation.", [], [], content_kind="x_topic",
    )

    assert result is not None
    assert "не выдавай один пост" in app_server.system.lower()
    assert "не указывай x" in app_server.system.lower()
