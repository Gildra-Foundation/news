from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from gildranews.adapters.ai.prompts import REDDIT_TOPIC_ANALYSIS_PROMPT, WOW_NEWS_ANALYSIS_PROMPT
from gildranews.domain.models import FilterResult, PublishedPostContext, Rewrite

log = logging.getLogger(__name__)

HASHTAG_INSTRUCTION = """
HASHTAG: для каждого поста ОБЯЗАТЕЛЬНО выбери ОДИН ключ из списка и верни его в поле hashtag:
— "новости" — хотфиксы, патчи, баланс, события и официальные анонсы;
— "руководство" — подробный гайд, маршрут или разбор механики;
— "советы" — короткий практический приём для игрока;
— "полезное" — аддон, инструмент, калькулятор или база данных;
— "обсуждения" — спорное изменение, PTR-эксперимент или позиция разработчиков.

Если не уверен — "полезное". Возвращай ИМЕННО ключ, без # и без имени канала — это бот добавит сам.
"""

# Кэш Gemini-клиентов по api_key. Клиент держит внутренний httpx-пул,
# создавать его на каждый вызов — потеря на TLS handshake/DNS.
_gemini_clients: dict[str, genai.Client] = {}


def _gemini(api_key: str):
    cli = _gemini_clients.get(api_key)
    if cli is None:
        cli = genai.Client(api_key=api_key)
        _gemini_clients[api_key] = cli
    return cli


@dataclass(frozen=True, slots=True)
class GeminiNewsFilter:
    """Configured Gemini adapter for the application-level news filter port."""

    api_key: str
    model: str

    async def filter_and_rewrite(
        self,
        text: str,
        recent_posts: Sequence[PublishedPostContext],
        emoji_themes: Sequence[dict[str, str]],
        content_kind: str = "news",
    ) -> FilterResult | None:
        return await filter_and_rewrite(
            api_key=self.api_key,
            model=self.model,
            text=text,
            recent_posts=list(recent_posts),
            emoji_themes=list(emoji_themes),
            content_kind=content_kind,
        )

REWRITE_PROMPT = """Ты — редактор Telegram-канала «RuNeuroNews» о новостях и находках в индустрии ИИ.

Перепиши присланный пост.

TITLE — 5–9 слов на русском, без точки на конце, без эмодзи, без кавычек. Передаёт суть. Допустим лёгкий характер. Без клише «прорыв», «революция», «потряс мир».

BODY — 2–4 абзаца, между абзацами одна пустая строка. Всего 350–800 символов. Первый абзац — суть с конкретикой.

СТИЛЬ — как у качественных русскоязычных AI-каналов (ai_newz, Сиолошная):
— открываешь сильным фактом или цифрой, не вводной фразой;
— разговорный синтаксис: короткие предложения, тире, авторская точка;
— одна-две точных формулировок или ироничный штрих — не больше;
— конкретика > общие слова. Цифры, версии, имена вместо «улучшено».

ПРИМЕР:
Исходник: «Anthropic выпустила Claude Opus 4.7 — флагман с контекстом 1М. SWE-bench 67.8%, +6 пунктов. Цены те же.»
ПЛОХО: «Anthropic обновила свою флагманскую модель. Главные фишки — увеличенное контекстное окно и улучшенная производительность.»
ХОРОШО: «Anthropic выкатила Claude Opus 4.7: миллион токенов в контексте, 67.8% на SWE-bench — +6 пунктов к 4.6.

Цены не двигались. API уже принимает запросы.»

ЖЁСТКИЕ ПРАВИЛА:
— РЕРАЙТ, НЕ ПЕРЕСКАЗ. Пересобери мысль, не переставляй слова. Если 70%+ слов body совпадают с исходником в том же порядке — переделай. Меняй структуру предложений, заменяй канцелярит на живые глаголы («выпустила» → «выкатила», «обновила» → «допилила»).
— Каждое предложение несёт факт/число/имя. Иначе режь.
— Не пиши «отличная находка», «полезный инструмент», «значительный прирост», «впечатляющий результат» — это филлер.
— Не приписывай авторам эпитеты («опытный», «известный»). Имя — достаточно.
— Не выдумывай расшифровок к названиям функций/скиллов если их нет в исходнике.
— Не пиши markdown (`*`, `_`, бэктики), хэштеги, html-теги.
— До 1 уместного смыслового эмодзи на пост.

ССЫЛКИ: если в исходнике есть URL — сохрани в body полным адресом. Не пиши «забрать», «по ссылке» — давай URL прямо.
""" + HASHTAG_INSTRUCTION

FILTER_PROMPT = WOW_NEWS_ANALYSIS_PROMPT + HASHTAG_INSTRUCTION
REDDIT_TOPIC_PROMPT = REDDIT_TOPIC_ANALYSIS_PROMPT + HASHTAG_INSTRUCTION


# Pydantic-схемы для structured output Gemini
class _RewriteOutput(BaseModel):
    title: str
    body: str
    hashtag: str = ""


class _FilterOutput(BaseModel):
    is_news: bool
    reason: str
    title: str = ""
    body: str = ""
    emoji_theme: str = ""
    hashtag: str = ""


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s)
    return s.strip()


def _parse_json(content: str) -> dict | None:
    if not content:
        return None
    content = _strip_code_fence(content)
    try:
        return json.loads(content, strict=False)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0), strict=False)
        except json.JSONDecodeError:
            return None


async def filter_and_rewrite(
    api_key: str,
    model: str,
    text: str,
    recent_posts: list[PublishedPostContext] | None = None,
    emoji_themes: list[dict] | None = None,
    content_kind: str = "news",
) -> FilterResult | None:
    """Single-post фильтр для real-time. None — если Gemini упал/вернул мусор.
    FilterResult с is_news=False — фильтр отказал (с reason).
    FilterResult с is_news=True — публикуем (с title/body/reason).
    recent_posts — недавно опубликованные посты с текстом, для дедупликации."""
    if not text.strip():
        return None
    payload = {
        "post": text[:12_000],
        "recent_published": recent_posts or [],
        "available_emoji_themes": emoji_themes or [],
    }
    client = _gemini(api_key)
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=(
                    REDDIT_TOPIC_PROMPT if content_kind == "reddit_topic" else FILTER_PROMPT
                ),
                temperature=0.4,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=_FilterOutput,
                # Отключаем thinking-режим Gemini 2.5 — он съедает токены и
                # упирается в лимит до выдачи JSON.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    except Exception:
        log.exception("Gemini filter error")
        return None

    parsed: _FilterOutput | None = getattr(resp, "parsed", None)
    if parsed is not None:
        title = parsed.title.strip()
        body = parsed.body.strip()
        if parsed.is_news and (not title or not body):
            log.warning("is_news=true но title/body пустые — считаем как отказ")
            return FilterResult(
                is_news=False,
                reason="Gemini пометил как новость, но не сформировал заголовок/тело",
            )
        return FilterResult(
            is_news=parsed.is_news,
            reason=parsed.reason.strip(),
            title=title,
            body=body,
            emoji_theme=parsed.emoji_theme.strip(),
            hashtag=parsed.hashtag.strip(),
        )

    data = _parse_json(resp.text or "")
    if data is None:
        log.error("Gemini filter ответ не парсится: %s", (resp.text or "")[:500])
        return None
    is_news = bool(data.get("is_news"))
    reason = str(data.get("reason", "")).strip()
    title = str(data.get("title", "")).strip()
    body = str(data.get("body", "")).strip()
    emoji_theme = str(data.get("emoji_theme", "")).strip()
    hashtag = str(data.get("hashtag", "")).strip()
    if is_news and (not title or not body):
        return FilterResult(is_news=False, reason=reason or "Пустой title/body")
    return FilterResult(
        is_news=is_news, reason=reason, title=title, body=body,
        emoji_theme=emoji_theme, hashtag=hashtag,
    )


TRANSLATE_PROMPT = """Ты — редактор Telegram-канала «RuNeuroNews» о новостях и находках в индустрии ИИ.

Получаешь пост из соцсети (X/Twitter или Reddit) в JSON: {"source_text": "...", "edit_instruction": "..."}.

🚨 КРИТИЧЕСКОЕ ПРАВИЛО — НИКАКИХ ВЫДУМОК ИЗ ОБУЧЕНИЯ.

Ты — языковая модель и помнишь много фактов из тренировочных данных. ЗАБУДЬ их полностью. Используй ТОЛЬКО то, что есть в source_text. Если автор написал «launched today» — ты НЕ знаешь что именно и за сколько. Если упомянуто имя без должности — ты НЕ знаешь его должность. Если речь о компании без цифр — НЕ подставляй цифры из памяти.

ПРИМЕР НАРУШЕНИЯ:
source_text: «just setting up my twttr»
ПЛОХО (галлюцинация): «Джек Дорси продал первый твит за $2,9 млн на NFT-аукционе, средства пошли на благотворительность.»
ХОРОШО (только то, что есть): «Просто настраиваю свой твиттер.» — даже если знаешь контекст про NFT, не добавляй.

Если в source_text мало фактов — body может быть коротким (1 предложение, 50 символов). Лучше скудный пост на чистых фактах, чем длинный с домыслами.

Если есть edit_instruction — учти при пересборке. Это правка от редактора.

TITLE — 5–9 слов на русском, без точки на конце, без эмодзи, без кавычек. Только то, что прямо следует из source_text.

BODY — до 4 абзацев, между абзацами одна пустая строка. До 800 символов. НИЖНЕЙ ГРАНИЦЫ НЕТ — если в исходнике мало содержания, body короткое. Перевод + минимальный рерайт под стиль канала.

СТИЛЬ — как у качественных русскоязычных AI-каналов (ai_newz, Сиолошная):
— открываешь сильным фактом или цифрой если они есть;
— разговорный синтаксис: короткие предложения, тире;
— одна-две точных формулировок или ироничный штрих если уместно;
— конкретика > общие слова.

ЖЁСТКИЕ ПРАВИЛА:

0. РЕРАЙТ, НЕ ПЕРЕСКАЗ. Пересобери мысль, а не переставляй слова. Меняй структуру предложений: исходник «Company X launched Y» → body «Y теперь работает: X докрутила»; «обновила/выпустила» → «выкатила/допилила/показала». Body не должен быть переводом-в-лоб.

1. ОТКРЫВАЛКА. Первая фраза НЕ начинается со страдательного шаблона: «представлена», «опубликована», «выпущена», «вышла», «обновлена». Начинай с подлежащего и активного глагола или с факта.

2. ЗАКРЫВАЛКА. Без обобщающих концовок: «это показательный кейс», «такой подход демонстрирует X», «прямой апгрейд», «всем стоит попробовать».

3. БЕЗ ФИЛЛЕРОВ: «отличная находка», «полезный инструмент», «значительный прирост», «впечатляющий результат», «интересный кейс».

4. БЕЗ MARKDOWN. Никаких `code`, *bold*, _italic_, хэштегов, html-тегов.

5. ЭМОДЗИ — до 1 смыслового на пост (🤖 🧠 ⚡️ 📦 🔧).

6. ССЫЛКИ. Если в source_text есть URL — сохрани в body полным адресом.
""" + HASHTAG_INSTRUCTION


class _TranslateOutput(BaseModel):
    title: str
    body: str
    hashtag: str = ""


async def translate_and_format(
    api_key: str,
    model: str,
    source_text: str,
    edit_instruction: str | None = None,
) -> Rewrite | None:
    """Переводит/форматирует пост из X или Reddit. edit_instruction — итеративная правка."""
    payload = {"source_text": source_text[:3000]}
    if edit_instruction:
        payload["edit_instruction"] = edit_instruction[:1000]

    client = _gemini(api_key)
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=TRANSLATE_PROMPT,
                temperature=0.4,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=_TranslateOutput,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    except Exception:
        log.exception("Gemini translate error")
        return None

    parsed: _TranslateOutput | None = getattr(resp, "parsed", None)
    if parsed is not None:
        return Rewrite(
            title=parsed.title.strip(),
            body=parsed.body.strip(),
            hashtag=(parsed.hashtag or "").strip(),
        )

    data = _parse_json(resp.text or "")
    if data is None:
        log.error("Gemini translate ответ не парсится: %s", (resp.text or "")[:500])
        return None
    try:
        return Rewrite(
            title=str(data["title"]).strip(),
            body=str(data["body"]).strip(),
            hashtag=str(data.get("hashtag", "")).strip(),
        )
    except (KeyError, TypeError):
        return None


GITHUB_PROMPT = """Ты — редактор Telegram-канала «RuNeuroNews» о новостях и находках в индустрии ИИ.

Получаешь структурированные данные о GitHub-репозитории: имя, описание, язык, звёзды, форки, темы, лицензия, фрагмент README. Сделай пост в стиле канала.

TITLE — 5–9 слов на русском, без точки на конце, без эмодзи, без кавычек.
Формат: «<Название>: <что это>». Пример:
— «Continue: опенсорс-альтернатива Copilot»
— «Marker: PDF в Markdown через нейросеть»
— «Codename Goose: локальный агентный фреймворк»

BODY — РОВНО 2 абзаца, между абзацами одна пустая строка. ЖЁСТКИЙ ЛИМИТ: ≤ 450 символов суммарно (caption Telegram ограничен; превышение → пост обрежется).

— Первый абзац — суть в 1–2 коротких предложениях: что репозиторий делает + язык + звёзды (округлённо: 18.2k) + лицензия. Активный залог. ~150 chars.
— Второй абзац — ГДЕ ПРИМЕНЯТЬ. 2–3 конкретных сценария, опираясь на description и README. Не выдумывай. ~250 chars.

НЕ ПИШИ третий абзац. НЕ ПИШИ про «отличительные особенности» отдельно — встрой в первый если нужно.

ЖЁСТКИЕ ПРАВИЛА:

0. РЕРАЙТ ОПИСАНИЯ, А НЕ КОПИРОВАНИЕ. README пиши своими словами под наш стиль. Не копируй english-фразы дословно — переформулируй. Не пиши «based on», «leveraging», «powered by» (это переводы из README). Живые русские глаголы.

1. БЕЗ ВЫДУМОК. Если в данных нет информации про конкретный use-case — не сочиняй. Опирайся только на description, topics, README. Не вставляй фичи которых нет.

2. ОТКРЫВАЛКА. Не начинай со страдательного шаблона «представлен», «опубликован», «вышла библиотека». Начинай с подлежащего: «<repo> позволяет…», «<repo> превращает X в Y», «<repo> — это…».

3. БЕЗ ФИЛЛЕРОВ. Запрещены: «отличный инструмент», «полезная находка», «впечатляющий проект», «интересное решение». Без эпитетов автору («опытный разработчик», «известная команда»).

4. БЕЗ MARKDOWN. Никаких `code`, *bold*, _italic_, хэштегов, html-тегов. Имена команд/пакетов пиши как обычный текст.

5. БЕЗ ЛИШНИХ ДАННЫХ. Не вставляй ссылку на github в body — она будет добавлена через кнопку «Оригинальный пост».

6. ЭМОДЗИ — до 1 уместного смыслового (🤖 🧠 ⚡️ 📦 🔧). Не каждый абзац.

7. ЦИФРЫ. Звёзды округляй: 1234 → 1.2k, 18247 → 18k, 124500 → 124k.

Стиль — как у качественных русскоязычных AI-каналов (ai_newz, Сиолошная): живо, конкретно, без воды.

Если на вход приходит JSON с полем edit_instruction — учти инструкцию редактора при пересборке поста.
""" + HASHTAG_INSTRUCTION


async def summarize_github(
    api_key: str,
    model: str,
    source_text: str,
    edit_instruction: str | None = None,
) -> Rewrite | None:
    """Делает пост из структурированных метаданных GitHub-репо.
    edit_instruction — инструкция от редактора при итеративной правке."""
    if not source_text.strip():
        return None
    payload = {"source_text": source_text[:6000]}
    if edit_instruction:
        payload["edit_instruction"] = edit_instruction[:1000]
    client = _gemini(api_key)
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=GITHUB_PROMPT,
                temperature=0.4,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=_RewriteOutput,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    except Exception:
        log.exception("Gemini github summarize error")
        return None

    parsed: _RewriteOutput | None = getattr(resp, "parsed", None)
    if parsed is not None:
        return Rewrite(
            title=parsed.title.strip(),
            body=parsed.body.strip(),
            hashtag=(parsed.hashtag or "").strip(),
        )

    data = _parse_json(resp.text or "")
    if data is None:
        log.error("Gemini github не парсится: %s", (resp.text or "")[:500])
        return None
    try:
        return Rewrite(
            title=str(data["title"]).strip(),
            body=str(data["body"]).strip(),
            hashtag=str(data.get("hashtag", "")).strip(),
        )
    except (KeyError, TypeError):
        return None


DIGEST_PROMPT = """Ты — редактор «RuNeuroNews». Готовишь короткий ЕЖЕНЕДЕЛЬНЫЙ ДАЙДЖЕСТ — обзор главных новостей канала за последние 7 дней.

На вход — JSON: {"posts": [{"id": <int>, "title": "...", "body_excerpt": "..."}, ...]}.

Задача:
1. Отбери до 6 самых важных постов недели. Приоритет: релизы крупных моделей > сделки/инвестиции > инструменты > исследования > гайды.
2. Сгруппируй в 2–4 рубрики по смыслу. Названия рубрик короткие, осмысленные. Примеры:
   — Релизы и обновления
   — Сделки и инвестиции
   — Инструменты и опенсорс
   — Исследования
3. Для каждого пункта дай ОЧЕНЬ короткую формулировку (5–12 слов) — одна мысль, конкретика. Без воды.

ВАЖНО — формат вывода:
JSON {"intro": "...", "sections": [{"name": "<emoji> <Рубрика>", "items": [{"id": <int>, "summary": "<5-12 слов>"}]}]}.

— intro — одна короткая строка (40–80 символов) приветствия к дайджесту с лёгким характером. Без клише «представляем», «спешим поделиться». Примеры: «Кто запустил, кто купил, кто открыл — главное за неделю», «Семь дней — десяток новостей, держите выжимку».
— name каждой секции начинается с подходящего эмодзи (🚀 релизы, 💼 сделки, 🔧 инструменты, 📊 исследования, 💡 советы и т.п.) и одного-двух слов кириллицей.
— summary — без точки в конце, без эмодзи, без markdown. Активный залог. Конкретика > общие слова.

id в ответе — id из входа. Если id указать неверный — пункт не попадёт в дайджест.

Запрещено: «впечатляющий», «отличный», «полезный», «важный кейс», «значительный», «прорыв», «революция»."""


class _DigestItem(BaseModel):
    id: int
    summary: str


class _DigestSection(BaseModel):
    name: str
    items: list[_DigestItem]


class _DigestOutput(BaseModel):
    intro: str
    sections: list[_DigestSection]


async def make_weekly_digest(
    api_key: str,
    model: str,
    posts: list[dict],
) -> _DigestOutput | None:
    """posts: [{"id": int, "title": str, "body_excerpt": str}].
    Возвращает структурированный дайджест или None."""
    if not posts:
        return None
    payload = {"posts": posts}
    client = _gemini(api_key)
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=DIGEST_PROMPT,
                temperature=0.4,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=_DigestOutput,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    except Exception:
        log.exception("Gemini digest error")
        return None

    parsed: _DigestOutput | None = getattr(resp, "parsed", None)
    if parsed is not None:
        return parsed
    data = _parse_json(resp.text or "")
    if not data:
        log.error("Gemini digest не парсится: %s", (resp.text or "")[:500])
        return None
    try:
        return _DigestOutput(**data)
    except ValidationError:
        log.error("Gemini digest schema mismatch: %r", data)
        return None


async def rewrite_only(api_key: str, model: str, text: str) -> Rewrite | None:
    """Рерайт без фильтра — для команды /test."""
    client = _gemini(api_key)
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=text[:3000],
            config=types.GenerateContentConfig(
                system_instruction=REWRITE_PROMPT,
                temperature=0.4,
                max_output_tokens=4096,
                response_mime_type="application/json",
                response_schema=_RewriteOutput,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )
    except Exception:
        log.exception("Gemini rewrite error")
        return None

    parsed: _RewriteOutput | None = getattr(resp, "parsed", None)
    if parsed is not None:
        return Rewrite(
            title=parsed.title.strip(),
            body=parsed.body.strip(),
            hashtag=(parsed.hashtag or "").strip(),
        )

    data = _parse_json(resp.text or "")
    if data is None:
        log.error("Gemini rewrite ответ не парсится: %s", (resp.text or "")[:500])
        return None
    try:
        return Rewrite(
            title=str(data["title"]).strip(),
            body=str(data["body"]).strip(),
            hashtag=str(data.get("hashtag", "")).strip(),
        )
    except (KeyError, TypeError):
        log.error("В rewrite-ответе нет title/body: %r", data)
        return None
