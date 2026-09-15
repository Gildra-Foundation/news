from __future__ import annotations

import json

import httpx
import pytest

from gildranews.adapters.ai.app_server import AppServerClient, AppServerError, parse_agui_sse
from gildranews.adapters.ai.provider import AppServerContentAI
from gildranews.application.translation_qa import presentation_issues


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
        response = dict(self.response)
        if response.get("is_news") is True and "fingerprint" not in response:
            response["fingerprint"] = {
                "game_branch": "retail",
                "version": "",
                "subject": response.get("title", "событие"),
                "action": "изменить",
                "status": "announced",
                "effective_date": "",
                "scope": [],
                "material_facts": [],
            }
        return json.dumps(response, ensure_ascii=False)

    async def aclose(self) -> None:
        return None


class _SequenceAppServer(_StubAppServer):
    def __init__(self, responses: list[dict]) -> None:
        super().__init__(responses[0])
        self.responses = iter(responses)

    async def complete(self, system: str, user: str) -> str:
        self.system = system
        self.user = user
        response = dict(next(self.responses))
        if response.get("is_news") is True and "fingerprint" not in response:
            response["fingerprint"] = {
                "game_branch": "retail",
                "version": "",
                "subject": response.get("title", "событие"),
                "action": "изменить",
                "status": "announced",
                "effective_date": "",
                "scope": [],
                "material_facts": [],
            }
        return json.dumps(response, ensure_ascii=False)


@pytest.mark.asyncio
async def test_luna_rejects_accepted_news_without_event_fingerprint() -> None:
    class _RawStub:
        async def complete(self, system: str, user: str) -> str:
            return json.dumps(
                {
                    "is_news": True,
                    "reason": "Новость",
                    "title": "Изменение рейда",
                    "body": "Босса ослабят.",
                },
                ensure_ascii=False,
            )

        async def aclose(self) -> None:
            return None

    result = await AppServerContentAI(_RawStub()).filter_and_rewrite(
        "The raid boss will be nerfed", [], [],
    )

    assert result is None


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
            "fingerprint": {
                "game_branch": "retail",
                "version": "12.2.5",
                "subject": "подсказки аддонов на миникарте",
                "action": "ограничить подсказки",
                "status": "announced",
                "effective_date": "",
                "scope": ["подземелья"],
                "material_facts": [],
            },
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
    assert result.fingerprint is not None
    assert result.fingerprint.version == "12.2.5"
    assert result.fingerprint.subject == "подсказки аддонов на миникарте"
    assert len(payload["post"]) > 3_000
    assert "final fact" in payload["post"]
    assert payload["recent_published"] == recent_posts
    assert "без новых существенных фактов" in app_server.system.lower()
    assert "tier set" in app_server.system.lower()
    assert "классовый комплект" in app_server.system.lower()
    assert "off-piece" in app_server.system.lower()
    assert "смена внешнего вида" in app_server.system.lower()
    assert "кратко, но полно" in app_server.system.lower()
    assert "180–500 символов" in app_server.system.lower()
    assert "не вырезай условие, дату, число или исключение" in app_server.system.lower()
    assert "простыми русскими конструкциями" in app_server.system.lower()
    assert "не пиши «level 20»" in app_server.system.lower()
    assert "число — в value" in app_server.system.lower()
    assert "World of Warcraft" in app_server.system
    assert "карта фактов" in app_server.system.lower()
    assert "не указывай источник" in app_server.system.lower()
    assert "mage → «маг»" in app_server.system.lower()
    assert "warlock → «чернокнижник»" in app_server.system.lower()
    assert "recent_voice_examples" in app_server.system
    assert "не пересказывай неизменившиеся факты" in app_server.system.lower()
    assert "не повторяй заголовок" in app_server.system.lower()
    assert "одно предложение — одна мысль" in app_server.system.lower()
    assert "точку с запятой" in app_server.system.lower()


@pytest.mark.asyncio
async def test_luna_builds_bounded_story_index_and_recent_voice_examples() -> None:
    app_server = _StubAppServer(
        {
            "is_news": True,
            "reason": "Новый факт",
            "title": "Blizzard изменила награду события",
            "body": "Игроки будут получать дополнительную монету раз в день.",
            "hashtag": "новости",
        },
    )
    processor = AppServerContentAI(app_server)
    recent_posts = [
        {
            "title": f"Публикация {index}",
            "body": (f"Факт {index}. " + "Подробность " * 100).strip(),
            "posted_at": f"2026-09-15 0{index}:00:00",
        }
        for index in range(8)
    ]

    result = await processor.filter_and_rewrite(
        "Blizzard changed the event reward to one extra coin per day.",
        recent_posts,
        [],
    )

    payload = json.loads(app_server.user)
    assert result is not None
    assert len(payload["recent_published"]) == 8
    assert all(len(post["body"]) <= 420 for post in payload["recent_published"])
    assert len(payload["recent_voice_examples"]) == 5
    assert all(len(post["body"]) <= 700 for post in payload["recent_voice_examples"])


@pytest.mark.asyncio
async def test_luna_repairs_dense_presentation_before_publication() -> None:
    long_body = "Blizzard изменила награду события. " + (
        "Игроки будут получать дополнительную монету без дополнительных условий "
        * 14
    )
    app_server = _SequenceAppServer(
        [
            {
                "is_news": True,
                "reason": "Новый факт",
                "title": "Blizzard изменила награду события",
                "body": long_body,
                "hashtag": "новости",
            },
            {
                "title": "Blizzard изменила награду события",
                "body": "Теперь игроки будут получать дополнительную монету раз в день.",
                "hashtag": "новости",
            },
        ],
    )
    processor = AppServerContentAI(app_server)

    result = await processor.filter_and_rewrite(
        "Blizzard changed the event reward to one extra coin per day.", [], [],
    )

    assert result is not None
    assert result.body == "Теперь игроки будут получать дополнительную монету раз в день."


@pytest.mark.asyncio
async def test_luna_splits_a_valid_repair_into_readable_paragraphs() -> None:
    repaired_body = (
        "Событие уже началось и предлагает несколько способов получить награды. "
        "Игроки могут пройти доступные подземелья и выполнить еженедельное задание. "
        "За первое прохождение персонаж получит дополнительную награду события. "
        "Повторные прохождения тоже принесут валюту для покупки полезных предметов. "
        "Все условия действуют до следующего еженедельного сброса игровых миров."
    )
    app_server = _SequenceAppServer(
        [
            {
                "is_news": True,
                "reason": "Событие началось",
                "title": "Началось еженедельное событие",
                "body": "Важно отметить, что событие уже началось.",
                "hashtag": "новости",
            },
            {
                "title": "Началось еженедельное событие",
                "body": repaired_body,
                "hashtag": "новости",
            },
        ],
    )

    result = await AppServerContentAI(app_server).filter_and_rewrite(
        "The weekly event is now live and offers several rewards.", [], [],
    )

    assert result is not None
    assert "\n\n" in result.body
    assert presentation_issues(result.title, result.body) == ()


@pytest.mark.asyncio
async def test_luna_normalizes_mage_mistranslation_before_publication() -> None:
    processor = AppServerContentAI(
        _StubAppServer(
            {
                "is_news": True,
                "reason": "Новая вступительная сцена",
                "title": "У паладина и колдуна общая сцена",
                "body": "Нежить-паладин и нежить-колдун получают одно вступление.",
                "hashtag": "новости",
            },
        ),
    )

    result = await processor.filter_and_rewrite(
        "The Undead Paladin and Undead Mage share the same intro scene.", [], [],
    )

    assert result is not None
    assert result.title == "У паладина и мага общая сцена"
    assert result.body == "Нежить-паладин и нежить-маг получают одно вступление."


@pytest.mark.asyncio
async def test_luna_repairs_untranslated_raid_terms_before_publication() -> None:
    app_server = _SequenceAppServer(
        [
            {
                "is_news": True,
                "reason": "Ослабление рейда",
                "title": "В Ядовитой Бездне ослабят механики",
                "body": "У Сзорака способность Caustic Claws больше не создаёт едкую лужу.",
                "hashtag": "новости",
            },
            {
                "title": "В Ядовитой Бездне ослабят механики",
                "body": "У Сзорака едкие когти больше не оставляют едкие лужи.",
                "hashtag": "новости",
                "references": [
                    {
                        "label": "Ядовитой Бездне",
                        "query": "Venomous Abyss",
                        "kind": "raid",
                    },
                    {
                        "label": "Сзорака",
                        "query": "Sszorak",
                        "kind": "creature",
                    },
                ],
            },
        ],
    )
    processor = AppServerContentAI(app_server)

    result = await processor.filter_and_rewrite(
        "In Venomous Abyss, Sszorak's Caustic Claws no longer creates Caustic Residue.",
        [],
        [],
    )

    assert result is not None
    assert result.title == "В Ядовитой Бездне ослабят механики"
    assert result.body == "У Сзорака едкие когти больше не оставляют едкие лужи."
    assert [(ref.label, ref.query, ref.kind) for ref in result.references] == [
        ("Ядовитой Бездне", "Venomous Abyss", "raid"),
        ("Сзорака", "Sszorak", "creature"),
    ]


@pytest.mark.asyncio
async def test_luna_preserves_official_expansion_name_and_reference() -> None:
    processor = AppServerContentAI(
        _StubAppServer(
            {
                "is_news": True,
                "reason": "Комментарий разработчиков",
                "title": "Последний Титан не завершит все истории WoW",
                "body": "Последний Титан завершит Сагу души мира, но не каждую историю.",
                "hashtag": "новости",
                "references": [
                    {
                        "label": "Последний Титан",
                        "query": "The Last Titan",
                        "kind": "expansion",
                        "role": "primary",
                    },
                ],
            },
        ),
    )

    result = await processor.filter_and_rewrite(
        "The Last Titan will conclude the Worldsoul Saga, but not every story in WoW.",
        [],
        [],
    )

    assert result is not None
    assert result.title == "The Last Titan не завершит все истории WoW"
    assert result.body == "The Last Titan завершит Сагу души мира, но не каждую историю."
    assert [(ref.label, ref.query, ref.kind) for ref in result.references] == [
        ("The Last Titan", "The Last Titan", "expansion"),
    ]


@pytest.mark.asyncio
async def test_luna_rejects_named_raid_when_reference_query_is_invented() -> None:
    processor = AppServerContentAI(
        _StubAppServer(
            {
                "is_news": True,
                "reason": "Ослабление рейда",
                "title": "В Ядовитой Бездне ослабят боссов",
                "body": "Изменения упростят прохождение.",
                "hashtag": "новости",
                "references": [
                    {
                        "label": "Ядовитой Бездне",
                        "query": "Invented Raid",
                        "kind": "raid",
                    }
                ],
            },
        ),
    )

    result = await processor.filter_and_rewrite("Venomous Abyss raid tuning", [], [])

    assert result is None


@pytest.mark.asyncio
async def test_luna_returns_typed_warcraft_entities_without_accepting_ids() -> None:
    processor = AppServerContentAI(
        _StubAppServer(
            {
                "is_news": True,
                "reason": "Изменение способности",
                "title": "Огненный шар усилят",
                "body": "Урон способности повысится.",
                "hashtag": "классы",
                "references": [
                    {
                        "label": "Огненный шар",
                        "query": "Fireball",
                        "kind": "spell",
                        "branch": "classic",
                        "role": "primary",
                        "external_id": 999999,
                        "icon_url": "https://evil.example/icon.png",
                    }
                ],
            },
        ),
    )

    result = await processor.filter_and_rewrite(
        "Classic Fireball damage will be increased.", [], [],
    )

    assert result is not None
    assert len(result.references) == 1
    reference = result.references[0]
    assert (reference.query, reference.kind, reference.branch, reference.role) == (
        "Fireball",
        "spell",
        "classic",
        "primary",
    )
    assert not hasattr(reference, "external_id")


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


@pytest.mark.asyncio
async def test_luna_repairs_ai_sounding_editorial_cliches_before_publication() -> None:
    app_server = _SequenceAppServer(
        [
            {
                "is_news": True,
                "reason": "Изменение баланса",
                "title": "Blizzard ослабила урон босса",
                "body": (
                    "Важно отметить, что урон способности снижен на 20%. "
                    "Это открывает новые возможности для игроков."
                ),
                "hashtag": "новости",
            },
            {
                "title": "Blizzard ослабила урон босса",
                "body": "Урон способности снизили на 20%, поэтому бой станет проще.",
                "hashtag": "новости",
            },
        ]
    )
    processor = AppServerContentAI(app_server)

    result = await processor.filter_and_rewrite(
        "Blizzard reduced the boss ability damage by 20%.", [], [],
    )

    assert result is not None
    assert result.body == "Урон способности снизили на 20%, поэтому бой станет проще."


@pytest.mark.asyncio
async def test_luna_repairs_ambiguous_specializations_and_adds_raid_reference() -> None:
    source = (
        "Heroic Venomous Abyss raid logs: Augmentation rises seven spots into "
        "the top 10. Retribution gains around 50,000 logs. Devastation rises "
        "four spots while Frost Mage drops eight spots."
    )
    app_server = _SequenceAppServer(
        [
            {
                "is_news": True,
                "reason": "Изменился рейтинг специализаций",
                "title": "Усиление вошло в десятку рейтинга урона",
                "body": (
                    "В героической Ядовитой Бездне Усиление поднялось на семь "
                    "мест. Воздаяние получило около 50,000 записей, а "
                    "Опустошение поднялось на четыре позиции."
                ),
                "hashtag": "полезное",
                "references": [],
            },
            {
                "title": "Насыщатель вошёл в десятку рейтинга урона",
                "body": (
                    "В героической Ядовитой Бездне пробудитель Насыщатель "
                    "поднялся на семь мест. Паладин Воздаяния получил около "
                    "50,000 записей, а пробудитель Опустошитель поднялся на "
                    "четыре позиции."
                ),
                "hashtag": "полезное",
                "references": [
                    {
                        "label": "Насыщатель",
                        "query": "Augmentation",
                        "kind": "specialization",
                        "branch": "retail",
                        "role": "primary",
                    },
                    {
                        "label": "Опустошитель",
                        "query": "Devastation",
                        "kind": "specialization",
                        "branch": "retail",
                        "role": "secondary",
                    },
                    {
                        "label": "Ядовитой Бездне",
                        "query": "Venomous Abyss",
                        "kind": "raid",
                        "branch": "retail",
                        "role": "secondary",
                    }
                ],
            },
        ]
    )
    processor = AppServerContentAI(app_server)

    result = await processor.filter_and_rewrite(source, [], [])

    assert result is not None
    assert result.title == "Насыщатель вошёл в десятку рейтинга урона"
    assert "пробудитель Насыщатель" in result.body
    assert "Паладин Воздаяния" in result.body
    assert "пробудитель Опустошитель" in result.body
    assert [(reference.query, reference.kind) for reference in result.references] == [
        ("Augmentation", "specialization"),
        ("Devastation", "specialization"),
        ("Venomous Abyss", "raid"),
    ]


@pytest.mark.asyncio
async def test_luna_repairs_named_raid_post_without_reference() -> None:
    app_server = _SequenceAppServer(
        [
            {
                "is_news": True,
                "reason": "Новая статистика рейда",
                "title": "Рейтинг урона в Ядовитой Бездне изменился",
                "body": "В героическом режиме сменился лидер.",
                "hashtag": "полезное",
                "references": [],
            },
            {
                "title": "Рейтинг урона в Ядовитой Бездне изменился",
                "body": "В героическом режиме сменился лидер.",
                "hashtag": "полезное",
                "references": [
                    {
                        "label": "Ядовитой Бездне",
                        "query": "Venomous Abyss",
                        "kind": "raid",
                        "branch": "retail",
                        "role": "primary",
                    }
                ],
            },
        ]
    )
    processor = AppServerContentAI(app_server)

    result = await processor.filter_and_rewrite(
        "Heroic Venomous Abyss raid rankings changed.", [], [],
    )

    assert result is not None
    assert [(reference.query, reference.kind) for reference in result.references] == [
        ("Venomous Abyss", "raid"),
    ]


@pytest.mark.asyncio
async def test_luna_repairs_ranking_post_without_specialization_references() -> None:
    source = (
        "Venomous Abyss raid rankings: Augmentation rises seven places and "
        "Devastation rises four places."
    )
    app_server = _SequenceAppServer(
        [
            {
                "is_news": True,
                "reason": "Изменился рейтинг специализаций",
                "title": "Насыщатель поднялся на семь мест",
                "body": (
                    "В Ядовитой Бездне пробудитель Насыщатель поднялся на семь "
                    "мест, а пробудитель Опустошитель — на четыре."
                ),
                "hashtag": "полезное",
                "references": [
                    {
                        "label": "Ядовитой Бездне",
                        "query": "Venomous Abyss",
                        "kind": "raid",
                    }
                ],
            },
            {
                "title": "Насыщатель поднялся на семь мест",
                "body": (
                    "В Ядовитой Бездне пробудитель Насыщатель поднялся на семь "
                    "мест, а пробудитель Опустошитель — на четыре."
                ),
                "hashtag": "полезное",
                "references": [
                    {
                        "label": "Насыщатель",
                        "query": "Augmentation",
                        "kind": "specialization",
                        "role": "primary",
                    },
                    {
                        "label": "Опустошитель",
                        "query": "Devastation",
                        "kind": "specialization",
                    },
                    {
                        "label": "Ядовитой Бездне",
                        "query": "Venomous Abyss",
                        "kind": "raid",
                    },
                ],
            },
        ]
    )
    processor = AppServerContentAI(app_server)

    result = await processor.filter_and_rewrite(source, [], [])

    assert result is not None
    assert [
        reference.query
        for reference in result.references
        if reference.kind == "specialization"
    ] == ["Augmentation", "Devastation"]
