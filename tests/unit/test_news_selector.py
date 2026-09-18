from __future__ import annotations

import json

import httpx
import pytest

from gildranews.adapters.ai.news_selector import (
    ClassifiedContentAI,
    OpenRouterNewsSelector,
    SelectionDecision,
)
from gildranews.domain.models import FilterResult


class _StubSelector:
    def __init__(self, decision: SelectionDecision | Exception) -> None:
        self.decision = decision
        self.closed = False

    async def classify(self, text: str, content_kind: str) -> SelectionDecision:
        assert text == "candidate"
        assert content_kind == "news"
        if isinstance(self.decision, Exception):
            raise self.decision
        return self.decision

    async def aclose(self) -> None:
        self.closed = True


class _StubContentAI:
    def __init__(self) -> None:
        self.filter_calls = 0
        self.closed = False

    async def filter_and_rewrite(
        self,
        text,
        recent_posts,
        emoji_themes,
        content_kind="news",
    ):
        self.filter_calls += 1
        return FilterResult(
            is_news=True,
            reason="Luna accepted",
            title="Title",
            body="Body",
        )

    async def translate(self, source_text, edit_instruction=None):
        return None

    async def summarize_github(self, source_text, edit_instruction=None):
        return None

    async def rewrite(self, source_text):
        return None

    async def make_weekly_digest(self, posts):
        return None

    async def aclose(self) -> None:
        self.closed = True


def _decision(*, choice: str, confidence: float) -> SelectionDecision:
    return SelectionDecision(
        choice=choice,
        confidence=confidence,
        probabilities={"accept": 0.01, "reject": 0.99},
        wow_relevance=0.95,
        has_substance=0.9,
        branch="retail",
        information_status="official",
        model="typesafe/jev-1.13-20260917",
    )


@pytest.mark.asyncio
async def test_openrouter_selector_uses_decisions_endpoint_and_parses_answers() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "typesafe/jev-1.13-20260917",
                "answers": {
                    "publication_fit": {
                        "type": "choice",
                        "choice": "accept",
                        "probabilities": {"accept": 0.98, "reject": 0.02},
                        "confidence": 0.96,
                    },
                    "wow_relevance": {"type": "noul", "noul": 0.99},
                    "has_substance": {"type": "noul", "noul": 0.91},
                    "branch": {
                        "type": "choice",
                        "choice": "retail",
                        "probabilities": {
                            "retail": 0.9,
                            "classic": 0.03,
                            "forever": 0.02,
                            "unknown": 0.05,
                        },
                        "confidence": 0.85,
                    },
                    "information_status": {
                        "type": "choice",
                        "choice": "official",
                        "probabilities": {
                            "official": 0.9,
                            "reported": 0.07,
                            "speculation": 0.02,
                            "opinion": 0.01,
                        },
                        "confidence": 0.82,
                    },
                },
                "usage": {"input_tokens": 350, "output_tokens": 60, "cost": 0.0000147},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    selector = OpenRouterNewsSelector(
        api_key="secret",
        model="typesafe/jev-1.13",
        client=client,
    )

    result = await selector.classify("Raid tuning announced", "news")

    assert captured["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert captured["authorization"] == "Bearer secret"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "typesafe/jev-1.13"
    assert payload["state"] == {
        "content_kind": "news",
        "text": "Raid tuning announced",
    }
    assert set(payload["questions"]) == {
        "publication_fit",
        "wow_relevance",
        "has_substance",
        "branch",
        "information_status",
    }
    assert result.choice == "accept"
    assert result.confidence == pytest.approx(0.96)
    assert result.wow_relevance == pytest.approx(0.99)
    assert result.branch == "retail"
    assert result.cost == pytest.approx(0.0000147)

    await client.aclose()


@pytest.mark.asyncio
async def test_confident_rejection_stops_before_luna() -> None:
    selector = _StubSelector(_decision(choice="reject", confidence=0.98))
    delegate = _StubContentAI()
    content_ai = ClassifiedContentAI(
        delegate,
        selector,
        min_reject_confidence=0.8,
        shadow_mode=False,
    )

    result = await content_ai.filter_and_rewrite("candidate", [], [])

    assert result is not None
    assert result.is_news is False
    assert "TypeSafe" in result.reason
    assert delegate.filter_calls == 0


@pytest.mark.asyncio
async def test_uncertain_rejection_falls_back_to_luna() -> None:
    selector = _StubSelector(_decision(choice="reject", confidence=0.55))
    delegate = _StubContentAI()
    content_ai = ClassifiedContentAI(
        delegate,
        selector,
        min_reject_confidence=0.8,
        shadow_mode=False,
    )

    result = await content_ai.filter_and_rewrite("candidate", [], [])

    assert result is not None
    assert result.is_news is True
    assert delegate.filter_calls == 1


@pytest.mark.asyncio
async def test_selector_failure_falls_back_to_luna() -> None:
    selector = _StubSelector(RuntimeError("temporary outage"))
    delegate = _StubContentAI()
    content_ai = ClassifiedContentAI(
        delegate,
        selector,
        min_reject_confidence=0.8,
        shadow_mode=False,
    )

    result = await content_ai.filter_and_rewrite("candidate", [], [])

    assert result is not None
    assert result.is_news is True
    assert delegate.filter_calls == 1


@pytest.mark.asyncio
async def test_shadow_mode_never_blocks_and_close_releases_both_clients() -> None:
    selector = _StubSelector(_decision(choice="reject", confidence=0.99))
    delegate = _StubContentAI()
    content_ai = ClassifiedContentAI(
        delegate,
        selector,
        min_reject_confidence=0.8,
        shadow_mode=True,
    )

    result = await content_ai.filter_and_rewrite("candidate", [], [])
    await content_ai.aclose()

    assert result is not None
    assert result.is_news is True
    assert delegate.filter_calls == 1
    assert selector.closed is True
    assert delegate.closed is True
