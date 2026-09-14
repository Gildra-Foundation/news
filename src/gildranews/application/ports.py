from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from gildranews.domain.models import FilterResult, PublishedPostContext, Rewrite


class NewsFilter(Protocol):
    async def filter_and_rewrite(
        self,
        text: str,
        recent_posts: Sequence[PublishedPostContext],
        emoji_themes: Sequence[dict[str, str]],
    ) -> FilterResult | None: ...


class Translator(Protocol):
    async def translate(self, source_text: str) -> Rewrite | None: ...


class TextEditor(Protocol):
    async def edit(self, source_text: str, instruction: str) -> Rewrite | None: ...


class ContentAI(NewsFilter, Protocol):
    async def translate(
        self, source_text: str, edit_instruction: str | None = None,
    ) -> Rewrite | None: ...

    async def summarize_github(
        self, source_text: str, edit_instruction: str | None = None,
    ) -> Rewrite | None: ...

    async def rewrite(self, source_text: str) -> Rewrite | None: ...

    async def make_weekly_digest(self, posts: list[dict]) -> Any | None: ...

    async def aclose(self) -> None: ...
