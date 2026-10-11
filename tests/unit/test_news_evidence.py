from __future__ import annotations

import json

import pytest

from gildranews.adapters.ai.provider import AppServerContentAI, InvalidAIResponseError


class AnalysisClient:
    def __init__(self, evidence, news_value="practical"):
        self.calls = 0
        self.evidence = evidence
        self.news_value = news_value

    async def complete(self, system, user):
        self.calls += 1
        assert "ЭТАП 1" in system
        return json.dumps({
            "is_news": True, "reason": "Изменение рейда", "news_value": self.news_value,
            "evidence": self.evidence,
            "fingerprint": {"game_branch": "retail", "version": "12.2.5", "subject": "raid", "action": "tune"},
        })


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", [[], [{"quote": "invented fact", "importance": "essential"}],
    [{"quote": "Patch 12.2.5", "importance": "context"}],
    [{"quote": "Patch 12.2.5", "importance": "essential"}, {"quote": "invented fact", "importance": "essential"}],
    [{"quote": "   ", "importance": "essential"}],
])
async def test_missing_or_invented_main_evidence_blocks_drafting(evidence) -> None:
    client = AnalysisClient(evidence)
    with pytest.raises(InvalidAIResponseError, match="цитат"):
        await AppServerContentAI(client).filter_and_rewrite("Patch 12.2.5", [], [])
    assert client.calls == 1


@pytest.mark.asyncio
async def test_news_without_value_does_not_reach_drafting() -> None:
    client = AnalysisClient([], news_value="none")
    result = await AppServerContentAI(client).filter_and_rewrite("Routine roundup", [], [])
    assert not result.is_news
    assert client.calls == 1


@pytest.mark.asyncio
async def test_missing_news_value_cannot_silently_pass_selection() -> None:
    class Client:
        def __init__(self):
            self.calls = 0

        async def complete(self, system, user):
            self.calls += 1
            return json.dumps({
                "is_news": True, "reason": "Интересно игрокам",
                "evidence": [{"quote": "Patch 12.2.5", "importance": "essential"}],
                "fingerprint": {"subject": "raid", "action": "tune"},
            })

    client = Client()
    with pytest.raises(InvalidAIResponseError, match="JSON"):
        await AppServerContentAI(client).filter_and_rewrite("Patch 12.2.5", [], [])
    assert client.calls == 2


@pytest.mark.asyncio
async def test_forever_repair_receives_branch_and_source_backed_facts() -> None:
    source = "WoW: Forever beta rewards Paladins with a new cloak."

    class Client:
        def __init__(self):
            self.calls = 0

        async def complete(self, system, user):
            self.calls += 1
            payload = json.loads(user)
            if self.calls == 1:
                return json.dumps({
                    "is_news": True, "reason": "Новая награда", "news_value": "practical",
                    "evidence": [{"quote": source, "importance": "essential"}],
                    "fingerprint": {"game_branch": "forever", "version": "", "subject": "cloak", "action": "reward", "status": "testing"},
                })
            if self.calls == 2:
                return json.dumps({"title": "Паладинам дадут новый плащ", "body": "Награду добавили в бета-тестирование."})
            assert "forever_branch_not_explained" in system
            assert payload["fingerprint"]["game_branch"] == "forever"
            assert payload["verified_evidence"][0]["quote"] == source
            return json.dumps({"title": "В WoW: Forever паладинам дадут новый плащ", "body": "Награду добавили в бета-тестирование."})

    client = Client()
    result = await AppServerContentAI(client).filter_and_rewrite(source, [], [])
    assert result.is_news
    assert "WoW: Forever" in result.title
    assert client.calls == 3
