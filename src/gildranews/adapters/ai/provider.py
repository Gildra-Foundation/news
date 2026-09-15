from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from gildranews.adapters.ai import gemini
from gildranews.adapters.ai.app_server import AppServerClient, AppServerError
from gildranews.adapters.ai.prompts import WOW_CLASS_TERMINOLOGY
from gildranews.adapters.editor.manacost import EditorClient
from gildranews.application.translation_qa import (
    artificial_style_markers,
    check_translation,
    normalize_wow_class_terms,
    normalize_wow_expansion_names,
    presentation_issues,
    specialization_issues,
    untranslated_terms,
)
from gildranews.domain.models import (
    EntityReference,
    FilterResult,
    InfographicFact,
    InfographicSpec,
    PublishedPostContext,
    Rewrite,
    WarcraftBranch,
    WarcraftEntityKind,
    WarcraftEntityRole,
)

if TYPE_CHECKING:
    from gildranews.config import Config

log = logging.getLogger(__name__)

_STORY_CONTEXT_LIMIT = 50
_STORY_BODY_LIMIT = 420
_VOICE_CONTEXT_LIMIT = 5
_VOICE_BODY_LIMIT = 700
_LINKABLE_CONTEXT_RE = re.compile(r"\b(?:raid|dungeon|boss)\b", re.IGNORECASE)
_NAMED_GAME_OBJECT_RE = re.compile(
    r"\b[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+)+\b",
)
_RANKING_CONTEXT_RE = re.compile(r"\b(?:dps|rankings?|logs?)\b", re.IGNORECASE)
_SPECIALIZATION_REFERENCE_ALIASES = {
    "augmentation": frozenset({"augmentation", "augmentation evoker"}),
    "devastation": frozenset({"devastation", "devastation evoker"}),
    "retribution": frozenset({"retribution", "retribution paladin"}),
    "frost mage": frozenset({"frost mage"}),
}

_JSON_SUFFIX = """

Верни только JSON без Markdown и пояснений. Допустимая структура:
{"title":"...","body":"...","hashtag":"новости","infographic":{"kicker":"...","title":"...","facts":[{"value":"точное значение из входного текста","label":"краткая подпись"}],"source":""}}
Поле infographic необязательно. Добавляй его только при наличии 2–4 точных числовых фактов; value обязан дословно встречаться во входном source_text/post. Не добавляй фактов от себя. Поле source всегда оставляй пустым.
"""

_FILTER_JSON_SUFFIX = """

Верни только JSON без Markdown и пояснений:
{"is_news":true,"reason":"...","title":"...","body":"...","emoji_theme":"...","hashtag":"...","infographic":null,"references":[]}
infographic может быть объектом с полями kicker, title, facts (2–4 объектов value/label), source="". Каждое value должно дословно встречаться во входном post. Для отклонённой новости infographic=null. Не добавляй источник, URL или название издания в title/body.
references — не более трёх объектов {"label":"точный текст из title/body","query":"точное исходное английское имя из post","kind":"class|specialization|spell|talent|item|cosmetic|transmog_set|mount|pet|achievement|raid|dungeon|boss|creature|faction|profession|event|expansion","branch":"retail|classic|forever","role":"primary|secondary"}. Название дополнения сохраняй на английском и помечай kind=expansion. URL, ID и изображения не придумывай: их найдёт бот. Главную изменяемую сущность пометь primary, остальные secondary. Для остальных случаев references=[].
В рейтинге специализаций включи в references две самые важные специализации с kind=specialization для значков и названный рейд с kind=raid для ссылки. Для специализаций используй точные английские названия из post; классы отдельными references не добавляй.
Если материал относится к WoW: Forever, обязательно включи reference с label="WoW: Forever", query="WoW: Forever", kind="expansion", branch="forever", role="primary".
"""

_RUSSIAN_REPAIR_PROMPT = """Ты — выпускающий редактор русскоязычного канала о World of Warcraft.

Черновик уже основан на исходной статье. Исправь язык и подачу title и body:
— переведи по смыслу названия рейдов, способностей, эффектов и механик;
— имена существ и персонажей без точного перевода запиши кириллицей;
— официальные английские названия дополнений сохраняй без перевода и без склонения;
— если материал относится к WoW: Forever, обязательно сохрани это название в title или body и добавь для него reference типа expansion с веткой forever;
— не оставляй другую латиницу, кроме Blizzard, WoW, World of Warcraft и официального названия WoW: Forever;
— убери перечисленные artificial_style_markers и любые редакторские комментарии о самом материале;
— исправь перечисленные presentation_issues: не повторяй заголовок в начале, раздели плотный текст и сократи body до 650 символов;
— исправь перечисленные specialization_issues и при первом упоминании называй класс вместе со специализацией: Augmentation Evoker — «пробудитель Насыщатель», Devastation Evoker — «пробудитель Опустошитель», Retribution Paladin — «паладин Воздаяния», Enhancement Shaman — «шаман Совершенствование»;
— если переданы reference_issues, добавь ссылочную сущность названного рейда, подземелья или босса: label обязан дословно находиться в русском title/body, query — быть точным английским названием из source_text; не создавай ссылки на классы;
— для ranking_without_specialization_references добавь две главные специализации как references с kind=specialization: label — точное русское название из title/body, query — точное английское название из source_text; главную пометь primary;
— начни с события, действия или числа; пиши прямыми короткими фразами без канцелярита;
— одно предложение — одна мысль; длинную фразу раздели на две;
— не используй точку с запятой: она перегружает текст;
— recent_published используй как индекс уже опубликованных сюжетов: оставь в центре только новый факт;
— recent_voice_examples задают только длину и ритм канала; не копируй из них формулировки;
— не используй «важно отметить», «таким образом», «данный материал», «открывает новые возможности» и итоговый вывод ради вывода;
— сохрани без изменений все числа, версии, отрицания, статус события и причинно-следственные связи;
— ничего не добавляй из памяти и не указывай источник.

Верни только JSON без Markdown: {"title":"...","body":"...","hashtag":"...","references":[{"label":"точное название из title/body","query":"точное английское имя из source_text","kind":"spell","branch":"retail","role":"primary"}]}.
""" + WOW_CLASS_TERMINOLOGY


class _FactOutput(BaseModel):
    value: str
    label: str


class _InfographicOutput(BaseModel):
    kicker: str = "ГЛАВНОЕ В ЦИФРАХ"
    title: str
    facts: list[_FactOutput] = Field(min_length=2, max_length=4)
    source: str = ""


class _ReferenceOutput(BaseModel):
    label: str
    query: str
    kind: WarcraftEntityKind
    branch: WarcraftBranch = "retail"
    role: WarcraftEntityRole = "secondary"


class _RewriteOutput(BaseModel):
    title: str
    body: str
    hashtag: str = ""
    infographic: _InfographicOutput | None = None
    references: list[_ReferenceOutput] = Field(default_factory=list)


class _FilterOutput(_RewriteOutput):
    is_news: bool
    reason: str
    emoji_theme: str = ""


class _DigestItem(BaseModel):
    id: int
    summary: str


class _DigestSection(BaseModel):
    name: str
    items: list[_DigestItem]


class _DigestOutput(BaseModel):
    intro: str
    sections: list[_DigestSection]


def _context_excerpt(value: str, limit: int, *, keep_paragraphs: bool) -> str:
    cleaned = re.sub(r"[ \t]+", " ", str(value).strip())
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if not keep_paragraphs:
        cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    head = cleaned[: limit - 1].rstrip()
    boundaries = [head.rfind(marker) for marker in (". ", "! ", "? ", "\n")]
    boundary = max(boundaries)
    if boundary >= limit // 2:
        head = head[: boundary + 1]
    elif " " in head:
        head = head.rsplit(" ", 1)[0]
    return head.rstrip() + "…"


def _channel_context(
    recent_posts: Sequence[PublishedPostContext],
) -> tuple[list[PublishedPostContext], list[PublishedPostContext]]:
    bounded = list(recent_posts[:_STORY_CONTEXT_LIMIT])
    story_index = [
        {
            "title": _context_excerpt(post["title"], 180, keep_paragraphs=False),
            "body": _context_excerpt(
                post["body"], _STORY_BODY_LIMIT, keep_paragraphs=False,
            ),
            "posted_at": str(post["posted_at"]),
        }
        for post in bounded
    ]
    voice_examples = [
        {
            "title": _context_excerpt(post["title"], 180, keep_paragraphs=False),
            "body": _context_excerpt(
                post["body"], _VOICE_BODY_LIMIT, keep_paragraphs=True,
            ),
            "posted_at": str(post["posted_at"]),
        }
        for post in bounded[:_VOICE_CONTEXT_LIMIT]
    ]
    return story_index, voice_examples


def _json_object(raw: str) -> dict[str, Any]:
    parsed = gemini._parse_json(raw)
    if not isinstance(parsed, dict):
        raise TypeError("ответ не содержит JSON-объект")
    return parsed


def _infographic(value: _InfographicOutput | None, source: str) -> InfographicSpec | None:
    if value is None or any(fact.value.strip() not in source for fact in value.facts):
        return None
    return InfographicSpec(
        kicker=value.kicker.strip() or "ГЛАВНОЕ В ЦИФРАХ",
        title=value.title.strip(),
        facts=tuple(
            InfographicFact(value=fact.value.strip(), label=fact.label.strip())
            for fact in value.facts
        ),
        source="",
    )


def _references(
    values: list[_ReferenceOutput],
    source: str,
    title: str,
    body: str,
) -> tuple[EntityReference, ...]:
    source_key = source.casefold()
    published_text = f"{title}\n{body}"
    references: list[EntityReference] = []
    for value in values:
        label = normalize_wow_expansion_names(source, value.label.strip())
        query = value.query.strip()
        if not label or not query or label not in published_text or query.casefold() not in source_key:
            continue
        references.append(
            EntityReference(
                label=label,
                query=query,
                kind=value.kind,
                branch=value.branch,
                role=value.role,
            )
        )
        if len(references) == 3:
            break
    return tuple(references)


def _reference_issues(
    source: str,
    references: tuple[EntityReference, ...],
) -> tuple[str, ...]:
    """Require a resolvable entity for posts about a specifically named instance."""
    if any(reference.kind in {"raid", "dungeon", "boss"} for reference in references):
        return ()
    if _LINKABLE_CONTEXT_RE.search(source) and _NAMED_GAME_OBJECT_RE.search(source):
        return ("named_instance_without_reference",)
    return ()


def _specialization_reference_issues(
    source: str,
    references: tuple[EntityReference, ...],
) -> tuple[str, ...]:
    if not _RANKING_CONTEXT_RE.search(source):
        return ()
    source_key = source.casefold()
    mentioned = {
        key
        for key, aliases in _SPECIALIZATION_REFERENCE_ALIASES.items()
        if any(re.search(rf"\b{re.escape(alias)}\b", source_key) for alias in aliases)
    }
    required = min(2, len(mentioned))
    if required == 0:
        return ()
    referenced = {
        key
        for reference in references
        if reference.kind == "specialization"
        for key, aliases in _SPECIALIZATION_REFERENCE_ALIASES.items()
        if reference.query.casefold().strip() in aliases
    }
    if len(mentioned & referenced) < required:
        return ("ranking_without_specialization_references",)
    return ()


class AppServerContentAI:
    """Luna-backed content adapter over the server's AG-UI endpoint."""

    def __init__(self, app_server: AppServerClient, editor: EditorClient | None = None) -> None:
        self._app_server = app_server
        self._editor = editor

    async def _complete(self, prompt: str, payload: dict[str, Any], schema: type[BaseModel]) -> BaseModel | None:
        try:
            raw = await self._app_server.complete(prompt, json.dumps(payload, ensure_ascii=False))
            return schema.model_validate(_json_object(raw))
        except (AppServerError, TypeError, ValueError, ValidationError):
            log.warning("Luna returned an unavailable or invalid structured response", exc_info=True)
            return None

    async def _finish_rewrite(
        self,
        source: str,
        output: _RewriteOutput,
        *,
        verify_translation: bool = False,
    ) -> Rewrite | None:
        title = normalize_wow_expansion_names(
            source, normalize_wow_class_terms(source, output.title.strip()),
        )
        body = normalize_wow_expansion_names(
            source, normalize_wow_class_terms(source, output.body.strip()),
        )
        if not title or not body:
            return None
        before_editor = f"{title}\n\n{body}"
        qa = check_translation(source, before_editor)
        if verify_translation and not qa.ready_for_editor:
            log.warning(
                "Translation QA rejected Luna output: missing_numbers=%s missing_links=%s code=%s",
                qa.missing_numbers,
                qa.missing_links,
                qa.code_spans_match,
            )
            return None
        if self._editor is not None:
            edited_body = await self._editor.edit(body)
            edited_body = normalize_wow_expansion_names(
                source, normalize_wow_class_terms(source, edited_body),
            )
            if check_translation(before_editor, f"{title}\n\n{edited_body}").ready_for_editor:
                body = edited_body
        return Rewrite(
            title=title,
            body=body,
            hashtag=output.hashtag.strip(),
            infographic=_infographic(output.infographic, source),
            references=_references(output.references, source, title, body),
        )

    async def filter_and_rewrite(
        self,
        text: str,
        recent_posts: Sequence[PublishedPostContext],
        emoji_themes: Sequence[dict[str, str]],
        content_kind: str = "news",
    ) -> FilterResult | None:
        story_index, voice_examples = _channel_context(recent_posts)
        output = await self._complete(
            gemini._filter_prompt(content_kind) + _FILTER_JSON_SUFFIX,
            {
                "post": text[:12_000],
                "recent_published": story_index,
                "recent_voice_examples": voice_examples,
                "available_emoji_themes": list(emoji_themes),
            },
            _FilterOutput,
        )
        if not isinstance(output, _FilterOutput):
            return None
        if not output.is_news:
            return FilterResult(is_news=False, reason=output.reason.strip())
        rewrite = await self._finish_rewrite(text, output)
        if rewrite is None:
            return None
        public_text = f"{rewrite.title}\n{rewrite.body}"
        terms = untranslated_terms(public_text)
        style_markers = artificial_style_markers(public_text)
        layout_issues = presentation_issues(rewrite.title, rewrite.body)
        spec_issues = specialization_issues(text, public_text)
        reference_issues = (
            *_reference_issues(text, rewrite.references),
            *_specialization_reference_issues(text, rewrite.references),
        )
        if terms or style_markers or layout_issues or spec_issues or reference_issues:
            repaired_output = await self._complete(
                _RUSSIAN_REPAIR_PROMPT,
                {
                    "source_text": text[:12_000],
                    "draft_title": rewrite.title,
                    "draft_body": rewrite.body,
                    "untranslated_terms": list(terms),
                    "artificial_style_markers": list(style_markers),
                    "presentation_issues": list(layout_issues),
                    "specialization_issues": list(spec_issues),
                    "reference_issues": list(reference_issues),
                    "recent_published": story_index,
                    "recent_voice_examples": voice_examples,
                },
                _RewriteOutput,
            )
            if not isinstance(repaired_output, _RewriteOutput):
                return None
            repaired = await self._finish_rewrite(
                f"{rewrite.title}\n\n{rewrite.body}",
                repaired_output,
                verify_translation=True,
            )
            repaired_text = (
                f"{repaired.title}\n{repaired.body}" if repaired is not None else ""
            )
            repaired_references = (
                _references(
                    repaired_output.references,
                    text,
                    repaired.title,
                    repaired.body,
                )
                if repaired is not None
                else ()
            ) or rewrite.references
            if (
                repaired is None
                or untranslated_terms(repaired_text)
                or artificial_style_markers(repaired_text)
                or presentation_issues(repaired.title, repaired.body)
                or specialization_issues(text, repaired_text)
                or _reference_issues(text, repaired_references)
                or _specialization_reference_issues(text, repaired_references)
            ):
                log.warning("Editorial repair did not pass language and style gates")
                return None
            rewrite = Rewrite(
                title=repaired.title,
                body=repaired.body,
                hashtag=repaired.hashtag or rewrite.hashtag,
                infographic=rewrite.infographic,
                references=(
                    repaired_references
                ),
            )
        return FilterResult(
            is_news=True,
            reason=output.reason.strip(),
            title=rewrite.title,
            body=rewrite.body,
            emoji_theme=output.emoji_theme.strip(),
            hashtag=rewrite.hashtag,
            infographic=rewrite.infographic,
            references=rewrite.references,
        )

    async def _rewrite_with_prompt(
        self,
        prompt: str,
        source_text: str,
        edit_instruction: str | None = None,
        limit: int = 3000,
        verify_translation: bool = False,
    ) -> Rewrite | None:
        payload: dict[str, str] = {"source_text": source_text[:limit]}
        if edit_instruction:
            payload["edit_instruction"] = edit_instruction[:1000]
        output = await self._complete(prompt + _JSON_SUFFIX, payload, _RewriteOutput)
        return (
            await self._finish_rewrite(
                source_text, output, verify_translation=verify_translation,
            )
            if isinstance(output, _RewriteOutput)
            else None
        )

    async def translate(self, source_text: str, edit_instruction: str | None = None) -> Rewrite | None:
        return await self._rewrite_with_prompt(
            gemini.TRANSLATE_PROMPT,
            source_text,
            edit_instruction,
            verify_translation=True,
        )

    async def summarize_github(
        self, source_text: str, edit_instruction: str | None = None,
    ) -> Rewrite | None:
        return await self._rewrite_with_prompt(
            gemini.GITHUB_PROMPT, source_text, edit_instruction, limit=6000,
        )

    async def rewrite(self, source_text: str) -> Rewrite | None:
        return await self._rewrite_with_prompt(gemini.REWRITE_PROMPT, source_text)

    async def make_weekly_digest(self, posts: list[dict]) -> _DigestOutput | None:
        output = await self._complete(gemini.DIGEST_PROMPT, {"posts": posts}, _DigestOutput)
        return output if isinstance(output, _DigestOutput) else None

    async def aclose(self) -> None:
        await self._app_server.aclose()
        if self._editor is not None:
            await self._editor.aclose()


class GeminiContentAI:
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model
        self._filter = gemini.GeminiNewsFilter(api_key, model)

    async def filter_and_rewrite(
        self, text, recent_posts, emoji_themes, content_kind="news",
    ):
        return await self._filter.filter_and_rewrite(
            text, recent_posts, emoji_themes, content_kind,
        )

    async def translate(self, source_text, edit_instruction=None):
        return await gemini.translate_and_format(self._api_key, self._model, source_text, edit_instruction)

    async def summarize_github(self, source_text, edit_instruction=None):
        return await gemini.summarize_github(self._api_key, self._model, source_text, edit_instruction)

    async def rewrite(self, source_text):
        return await gemini.rewrite_only(self._api_key, self._model, source_text)

    async def make_weekly_digest(self, posts):
        return await gemini.make_weekly_digest(self._api_key, self._model, posts)

    async def aclose(self) -> None:
        return None


def build_content_ai(cfg: Config):
    if cfg.ai_provider == "gemini":
        return GeminiContentAI(cfg.gemini_api_key, cfg.gemini_model)
    app_server = AppServerClient(
        endpoint=cfg.app_server_url,
        token=cfg.app_server_token,
        model=cfg.app_server_model,
        reasoning_effort=cfg.app_server_reasoning_effort,
        timeout_seconds=cfg.ai_timeout_seconds,
    )
    editor = (
        EditorClient(
            cfg.editor_url,
            token=cfg.editor_token,
            timeout_seconds=cfg.ai_timeout_seconds,
        )
        if cfg.editor_url
        else None
    )
    return AppServerContentAI(app_server, editor)
