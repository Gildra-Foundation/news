from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from gildranews.domain.models import FilterResult, Rewrite


class NewsFilter(Protocol):
    async def filter_and_rewrite(
        self,
        text: str,
        recent_titles: Sequence[str],
        emoji_themes: Sequence[dict[str, str]],
    ) -> FilterResult | None: ...


class Translator(Protocol):
    async def translate(self, source_text: str) -> Rewrite | None: ...


class TextEditor(Protocol):
    async def edit(self, source_text: str, instruction: str) -> Rewrite | None: ...
