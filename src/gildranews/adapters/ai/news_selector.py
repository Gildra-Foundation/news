from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, Field, ValidationError

from gildranews.application.ports import ContentAI
from gildranews.domain.models import FilterResult

log = logging.getLogger(__name__)

OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
_TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})


class NewsSelectorError(RuntimeError):
    """OpenRouter/TypeSafe did not return a usable classification."""


class _ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)


class _NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1)


class _Answers(BaseModel):
    publication_fit: _ChoiceAnswer
    wow_relevance: _NoulAnswer
    has_substance: _NoulAnswer
    branch: _ChoiceAnswer
    information_status: _ChoiceAnswer


class _Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0


class _DecisionResponse(BaseModel):
    model: str
    answers: _Answers
    usage: _Usage = Field(default_factory=_Usage)


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    choice: str
    confidence: float
    probabilities: dict[str, float]
    wow_relevance: float
    has_substance: float
    branch: str
    information_status: str
    model: str
    cost: float = 0

    def confidently_rejects(self, minimum_confidence: float) -> bool:
        return self.choice == "reject" and self.confidence >= minimum_confidence


def _questions(content_kind: str) -> dict[str, dict[str, object]]:
    community_topic = content_kind in {"reddit_topic", "x_topic"}
    accept_description = (
        "A concrete, useful, or genuinely interesting World of Warcraft community topic "
        "that can support a clear standalone post."
        if community_topic
        else "Concrete new World of Warcraft information, a meaningful update, or an event "
        "that affects players."
    )
    reject_description = (
        "Unrelated, promotional, context-free, or too insubstantial for a useful post."
    )
    return {
        "publication_fit": {
            "type": "choice",
            "instructions": "Should this item enter a curated World of Warcraft publication pipeline?",
            "criteria": {
                "accept": accept_description,
                "reject": reject_description,
            },
        },
        "wow_relevance": {
            "type": "noul",
            "instructions": "This item is specifically about World of Warcraft.",
        },
        "has_substance": {
            "type": "noul",
            "instructions": (
                "This item contains enough concrete information or discussion substance "
                "for a clear standalone post."
            ),
        },
        "branch": {
            "type": "choice",
            "instructions": "Which World of Warcraft branch is the item mainly about?",
            "criteria": {
                "retail": "The current main version of World of Warcraft.",
                "classic": "Any official World of Warcraft Classic branch.",
                "forever": "The WoW: Forever project or announced game branch.",
                "unknown": "The branch is not stated clearly enough.",
            },
        },
        "information_status": {
            "type": "choice",
            "instructions": "What is the strongest information status supported by the item?",
            "criteria": {
                "official": "An official announcement, patch note, hotfix, or developer statement.",
                "reported": "A factual report or discovery supported by direct evidence.",
                "speculation": "An unconfirmed interpretation, rumor, or prediction.",
                "opinion": "Primarily personal opinion or open-ended discussion.",
            },
        },
    }


class OpenRouterNewsSelector:
    """TypeSafe Jev classifier over OpenRouter's Decisions API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "typesafe/jev-1.13",
        timeout_seconds: int = 20,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def classify(self, text: str, content_kind: str) -> SelectionDecision:
        payload = {
            "model": self._model,
            "state": {
                "content_kind": content_kind,
                "text": text[:12_000],
            },
            "questions": _questions(content_kind),
        }
        response: httpx.Response | None = None
        for attempt in range(2):
            try:
                response = await self._client.post(
                    OPENROUTER_DECISIONS_URL,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://t.me/gildrawow",
                        "X-OpenRouter-Title": "GildraNews",
                    },
                    json=payload,
                )
            except httpx.HTTPError as exc:
                if attempt == 0:
                    await asyncio.sleep(0.25)
                    continue
                raise NewsSelectorError("OpenRouter request failed") from exc
            if response.status_code not in _TRANSIENT_STATUSES or attempt == 1:
                break
            await asyncio.sleep(0.25)

        if response is None:
            raise NewsSelectorError("OpenRouter returned no response")
        if response.is_error:
            raise NewsSelectorError(f"OpenRouter returned HTTP {response.status_code}")
        try:
            parsed = _DecisionResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise NewsSelectorError("OpenRouter returned an invalid decision response") from exc

        publication_fit = parsed.answers.publication_fit
        if publication_fit.choice not in {"accept", "reject"}:
            raise NewsSelectorError("TypeSafe returned an unknown publication decision")
        return SelectionDecision(
            choice=publication_fit.choice,
            confidence=publication_fit.confidence,
            probabilities=dict(publication_fit.probabilities),
            wow_relevance=parsed.answers.wow_relevance.noul,
            has_substance=parsed.answers.has_substance.noul,
            branch=parsed.answers.branch.choice,
            information_status=parsed.answers.information_status.choice,
            model=parsed.model,
            cost=parsed.usage.cost,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class ClassifiedContentAI:
    """Pre-filter content with Jev while retaining Luna as the authoritative fallback."""

    def __init__(
        self,
        delegate: ContentAI,
        selector: OpenRouterNewsSelector,
        *,
        min_reject_confidence: float,
        shadow_mode: bool,
    ) -> None:
        self._delegate = delegate
        self._selector = selector
        self._min_reject_confidence = min_reject_confidence
        self._shadow_mode = shadow_mode

    async def filter_and_rewrite(
        self,
        text,
        recent_posts,
        emoji_themes,
        content_kind="news",
    ):
        try:
            decision = await self._selector.classify(text, content_kind)
        except Exception:
            log.warning("news_selector_failed fallback=luna", exc_info=True)
        else:
            log.info(
                "news_selector_decision choice=%s confidence=%.3f relevance=%.3f "
                "substance=%.3f branch=%s status=%s model=%s cost=%.8f shadow=%s",
                decision.choice,
                decision.confidence,
                decision.wow_relevance,
                decision.has_substance,
                decision.branch,
                decision.information_status,
                decision.model,
                decision.cost,
                self._shadow_mode,
            )
            if not self._shadow_mode and decision.confidently_rejects(
                self._min_reject_confidence,
            ):
                return FilterResult(
                    is_news=False,
                    reason=(
                        "TypeSafe: материал уверенно не прошёл предварительный отбор "
                        f"({decision.confidence:.0%})"
                    ),
                )
        return await self._delegate.filter_and_rewrite(
            text,
            recent_posts,
            emoji_themes,
            content_kind,
        )

    async def translate(self, source_text, edit_instruction=None):
        return await self._delegate.translate(source_text, edit_instruction)

    async def summarize_github(self, source_text, edit_instruction=None):
        return await self._delegate.summarize_github(source_text, edit_instruction)

    async def rewrite(self, source_text):
        return await self._delegate.rewrite(source_text)

    async def make_weekly_digest(self, posts):
        return await self._delegate.make_weekly_digest(posts)

    async def aclose(self) -> None:
        try:
            await self._delegate.aclose()
        finally:
            await self._selector.aclose()
