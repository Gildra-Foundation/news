from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from gildranews.domain.models import FilterResult, Rewrite

log = logging.getLogger(__name__)

HASHTAG_INSTRUCTION = """
HASHTAG: для каждого поста ОБЯЗАТЕЛЬНО выбери ОДИН ключ из списка и верни его в поле hashtag:
— "новости" — релизы моделей, продукты, сделки, инвестиции, регуляторика, конкретные ивенты;
— "руководство" — пошаговый гайд / туториал / how-to / прохождение настройки;
— "советы" — короткий приём, лайфхак, неочевидный трюк, шорт-форма «делайте так»;
— "полезное" — опенсорс-инструмент, репозиторий, библиотека, ссылка на ресурс/датасет;
— "обсуждения" — дискуссионная новость, мнение, спор, регуляторный/этический сюжет.

Если не уверен — "полезное". Возвращай ИМЕННО ключ, без # и без @runeuronews — это бот добавит сам.
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
        recent_titles: Sequence[str],
        emoji_themes: Sequence[dict[str, str]],
    ) -> FilterResult | None:
        return await filter_and_rewrite(
            api_key=self.api_key,
            model=self.model,
            text=text,
            recent_titles=list(recent_titles),
            emoji_themes=list(emoji_themes),
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

FILTER_PROMPT = """Ты — главный редактор Telegram-канала «RuNeuroNews» про индустрию ИИ. Канал — для людей, которые работают с ИИ, а не для энтузиастов. Выходит максимум 5–6 постов в день, поэтому проходит ТОЛЬКО лучшее.

На вход: JSON {"post": "<текст>", "recent_published": [...], "available_emoji_themes": [...]}.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ШАГ 1. ДУБЛИКАТ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Если post по сути рассказывает ТУ ЖЕ историю/находку/инструмент, что одна из записей recent_published (даже если другими словами, из другого канала, с другим углом) — is_news=false, reason="Дубликат: <ровно тот заголовок>".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ШАГ 2. РЕДАКТОРСКИЙ ТЕСТ ДВУХ ВОПРОСОВ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Задай себе ОБА вопроса:

А) «Сохранил бы я это в избранное / переслал бы в чат коллегам?»
Б) «Есть ли в посте хоть один УНИКАЛЬНЫЙ ФАКТ, который нельзя придумать самому?»
   (версия модели, цифра бенчмарка, имя компании-покупателя, сумма сделки, название репозитория, ссылка, конкретная команда из туториала)

Если оба ответа = ДА → проходи дальше.
Если хотя бы один = НЕТ → is_news=false, reason="Нет уникального факта / не интересно профи".

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ШАГ 3. ВАЖНОСТЬ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Оцени значимость для аудитории канала, мысленно — 3 уровня:

3 — СОБЫТИЕ ДНЯ. Остановит скролл у любого, кто следит за ИИ-рынком.
   Примеры: GPT-5 релиз, Anthropic поднял $5B, новый закон ЕС по ИИ, утечка весов крупной модели, 0-day в популярной модели.

2 — ВАЖНО. Большинство читателей сохранит/прочитает целиком.
   Примеры: новая фича в Claude/ChatGPT, опенсорс-репо 1k+ звёзд решающий боль, бенчмарк с цифрами, гайд с готовым приёмом, сделка $10M+, кейс с измеримой выгодой.

1 — РЯДОВОЕ. Нишевое, без сильной конкретики, или повтор уже известных идей.
   Примеры: патч-релиз без новых фич, общая статья «как использовать AI», микро-инструмент для очень узкой задачи, мнения без данных, пересказы чужих постов.

importance=1 → is_news=false, reason="Рядовое, не дотягивает до планки канала". Канал не должен забиваться шумом — лучше промолчать, чем опубликовать слабое.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ШАГ 4. КОНКРЕТНЫЕ КАТЕГОРИИ ОТСЕВА
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Даже если тесты проходят — выкини с is_news=false если это:

— мемы, шутки, опросы, развлекательное, тесты типа «угадай модель»;
— платная реклама курсов/буткемпов/сервисов/мерча/каналов (включая нативную);
— продвижение собственных платных продуктов автора без новой технической ценности;
— карьерные/мотивационные посты без технической конкретики («айти не для всех», «как я вкатился»);
— афиша: анонсы стримов/лекций/конференций без программы и спикеров;
— «общие» размышления без событий и фактов («я думаю, ИИ — это…»);
— списки без объяснений: «10 крутых сервисов» с одной строкой про каждый, без сути;
— «давайте обсудим» / опросы без контекста;
— RT/репост чужого поста без новой ценности;
— «вышло обновление X» без указания что именно поменялось;
— тесты в духе «я задал ChatGPT вопрос Y» без интересного вывода с цифрой;
— переоткрытие очевидного: «промпт-инжиниринг важен», «ИИ меняет рынок»;
— реклама API-обёрток без оригинальной фичи;
— фотки/арт без новости про инструмент.

ГЛАВНЫЙ ВОПРОС: «уйдёт ли читатель с этого поста умнее или с конкретной пользой?» Если только настроение / общие слова / самореклама — НЕТ.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ШАГ 5. ЗАПОЛНЕНИЕ ПОЛЕЙ
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

В любом случае reason: 1–2 предложения по-русски. Для is_news=true — что именно полезно (новый релиз / инструмент / приём). Для is_news=false — что не подходит.

Если is_news=true — также заполни title и body.

TITLE — 5–9 слов на русском, без точки на конце, без эмодзи, без кавычек. Передаёт суть: новость / релиз / инструмент / гайд / приём. Допустим лёгкий характер и неожиданная формулировка. Запрещены клише «прорыв», «революция», «потряс мир», «изменит всё», капс, кликбейтные многоточия.

BODY — 2–4 абзаца, между абзацами ОДНА пустая строка. Всего 350–800 символов. Первый абзац — суть с конкретикой.

СТИЛЬ body — как у качественных русскоязычных AI-каналов (ai_newz, Сиолошная, Denis Sexy IT):
— открываешь сильным фактом, цифрой или штрихом, а не вводной фразой типа «компания обновила...»;
— разговорный синтаксис: короткие предложения, тире, точка в неожиданном месте;
— одна-две точных формулировок или ироничный поворот за весь пост — без перегиба;
— конкретика всегда побеждает: цифры, версии, имена > обтекаемые «улучшено», «увеличено», «расширено».

ПРИМЕРЫ:

Исходник: «Anthropic выпустила Claude Opus 4.7 — флагман с контекстом 1М. SWE-bench 67.8%, +6 пунктов. Цены те же.»

ПЛОХО (сухо, шаблон):
«Anthropic обновила свою флагманскую модель Claude Opus до версии 4.7. Главные фишки — увеличенное контекстное окно и улучшенная производительность. Доступ к API уже открыт.»

ХОРОШО (вспышка + конкретика):
«Anthropic выкатила Claude Opus 4.7: миллион токенов в контексте, 67.8% на SWE-bench — это +6 пунктов к 4.6.

Цены не двигались — $15 за миллион входных, $75 за выходные. API уже принимает запросы.»

———

Исходник: «Мэтт Покок поделился набором скиллов для Claude Code — to-prd, to-issues, grill-me, design-an-interface. MIT, github.com/mattpocock/skills»

ПЛОХО (выдумка + филлер):
«Опытный разработчик Мэтт Покок поделился полезным набором скиллов: to-prd для подготовки кода и to-issues для управления задачами. Отличная находка для тех, кто использует Claude Code.»

ХОРОШО (только то, что есть):
«Мэтт Покок собрал набор скиллов для Claude Code: to-prd, to-issues, grill-me, design-an-interface. Названия в комментариях не нуждаются.

Установка в одну команду: npx skills@latest add mattpocock/skills/<name>. MIT.

https://github.com/mattpocock/skills»

———

ЖЁСТКИЕ ПРАВИЛА:

0. РЕРАЙТ, А НЕ ПЕРЕСКАЗ — главное.
Body должен ПЕРЕСОБРАТЬ мысль, а не переставить слова исходника. Это рерайт, не копипаст.
— Если 70%+ слов body совпадают с исходником в том же порядке — это плохо, переделай.
— Поменяй структуру предложений: исходник «компания X сделала Y» → body «Y теперь возможно: X выкатила».
— Замени канцелярит на живые глаголы: «выпустила» → «выкатила», «обновила» → «допилила»/«докрутила», «представила» → «показала», «реализовала» → «сделала», «запустила» → «открыла».
— Сменив угол: исходник про «что сделали» → расскажи «что это значит для пользователя» в одной фразе (только если факт есть).
— Опусти то, что подразумевается. Не объясняй очевидное.

1. ОТКРЫВАЛКА. Первая фраза НЕ начинается со страдательного шаблона: «представлена», «опубликована», «выпущена», «выложена», «анонсирована», «обновлена», «вышла». Начинай с подлежащего и активного глагола: «Anthropic выкатила...», «Cursor добавил...», «Мэтт Покок собрал...». Или сразу с факта-цифры: «Контекст 2M, цена не выросла».

2. ЗАКРЫВАЛКА. Пост заканчивается на конкретный факт, имя, цифру или URL. ЗАПРЕЩЕНЫ обобщающие концовки: «это показательный/яркий кейс», «такой подход демонстрирует X», «пример скорости/эффективности», «прямой апгрейд», «всем стоит попробовать». Если хочется так закончить — оборви на предыдущем факте.

3. ПЛОТНОСТЬ. Каждое предложение несёт факт, число, имя или конкретное наблюдение. Предложение можно убрать без потери смысла? — Убирай.

4. БЕЗ ФИЛЛЕРОВ: «отличная находка», «полезный инструмент», «хороший повод попробовать», «значительный прирост», «впечатляющий результат», «интересный кейс», «прямой апгрейд», «показательный кейс», «крутая фича», «достойно внимания», «нельзя пройти мимо».

5. БЕЗ ЭПИТЕТОВ к авторам: «опытный», «известный», «талантливый разработчик», «топовая компания». Имя/название — достаточно.

6. БЕЗ ВЫДУМОК. Не расшифровывай названия функций/скиллов/команд, если автор не дал расшифровку. Не подставляй того, чего нет в исходнике. Не упоминай аналоги, которых автор не упоминал.

7. БЕЗ MARKDOWN. Никаких `code`, *bold*, _italic_, хэштегов, html-тегов.

8. ЭМОДЗИ. До 1 смыслового эмодзи на пост (🤖 🧠 ⚡️ 📦 🔧). Не каждый абзац.

9. ИРОНИЯ — на один штрих максимум. Канал не паясничает. Не превращай каждый пост в шоу.

ССЫЛКИ: если в исходнике есть URL — обязательно сохрани в body полным адресом (https://github.com/foo/bar). Не оборачивай в «забрать», «по ссылке» — давай URL прямо. Без сокращений (bit.ly не использовать).

Если is_news=false — title и body оставь пустыми строками.

EMOJI_THEME: если в payload есть массив "available_emoji_themes" (формат [{"key": "...", "desc": "..."}, ...]) — для is_news=true выбери ОДНУ тему, ключ которой лучше всего описывает пост, и верни этот ключ в поле emoji_theme. Если ни одна тема не подходит — пустая строка. Для is_news=false — пустая строка.
""" + HASHTAG_INSTRUCTION


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
    recent_titles: list[str] | None = None,
    emoji_themes: list[dict] | None = None,
) -> FilterResult | None:
    """Single-post фильтр для real-time. None — если Gemini упал/вернул мусор.
    FilterResult с is_news=False — фильтр отказал (с reason).
    FilterResult с is_news=True — публикуем (с title/body/reason).
    recent_titles — недавно опубликованные, для дедупликации."""
    if not text.strip():
        return None
    payload = {
        "post": text[:3000],
        "recent_published": recent_titles or [],
        "available_emoji_themes": emoji_themes or [],
    }
    client = _gemini(api_key)
    try:
        resp = await client.aio.models.generate_content(
            model=model,
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                system_instruction=FILTER_PROMPT,
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
