from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from gildranews.adapters.ai import gemini
from gildranews.adapters.ai.app_server import AppServerClient, AppServerError
from gildranews.adapters.editor.manacost import EditorClient
from gildranews.application.translation_qa import check_translation
from gildranews.domain.models import (
    FilterResult,
    InfographicFact,
    InfographicSpec,
    PublishedPostContext,
    Rewrite,
)

if TYPE_CHECKING:
    from gildranews.config import Config

log = logging.getLogger(__name__)

_JSON_SUFFIX = """

Верни только JSON без Markdown и пояснений. Допустимая структура:
{"title":"...","body":"...","hashtag":"новости","infographic":{"kicker":"...","title":"...","facts":[{"value":"точное значение из входного текста","label":"краткая подпись"}],"source":""}}
Поле infographic необязательно. Добавляй его только при наличии 2–4 точных числовых фактов; value обязан дословно встречаться во входном source_text/post. Не добавляй фактов от себя. Поле source всегда оставляй пустым.
"""

_FILTER_JSON_SUFFIX = """

Верни только JSON без Markdown и пояснений:
{"is_news":true,"reason":"...","title":"...","body":"...","emoji_theme":"...","hashtag":"...","infographic":null}
infographic может быть объектом с полями kicker, title, facts (2–4 объектов value/label), source="". Каждое value должно дословно встречаться во входном post. Для отклонённой новости infographic=null. Не добавляй источник, URL или название издания в title/body.
"""


class _FactOutput(BaseModel):
    value: str
    label: str


class _InfographicOutput(BaseModel):
    kicker: str = "ГЛАВНОЕ В ЦИФРАХ"
    title: str
    facts: list[_FactOutput] = Field(min_length=2, max_length=4)
    source: str = ""


class _RewriteOutput(BaseModel):
    title: str
    body: str
    hashtag: str = ""
    infographic: _InfographicOutput | None = None


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
        title = output.title.strip()
        body = output.body.strip()
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
            if check_translation(before_editor, f"{title}\n\n{edited_body}").ready_for_editor:
                body = edited_body
        return Rewrite(
            title=title,
            body=body,
            hashtag=output.hashtag.strip(),
            infographic=_infographic(output.infographic, source),
        )

    async def filter_and_rewrite(
        self,
        text: str,
        recent_posts: Sequence[PublishedPostContext],
        emoji_themes: Sequence[dict[str, str]],
        content_kind: str = "news",
    ) -> FilterResult | None:
        output = await self._complete(
            gemini._filter_prompt(content_kind) + _FILTER_JSON_SUFFIX,
            {
                "post": text[:12_000],
                "recent_published": list(recent_posts),
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
        return FilterResult(
            is_news=True,
            reason=output.reason.strip(),
            title=rewrite.title,
            body=rewrite.body,
            emoji_theme=output.emoji_theme.strip(),
            hashtag=rewrite.hashtag,
            infographic=rewrite.infographic,
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
