from __future__ import annotations

import json

import pytest

from gildranews.application.site_articles import generate_article

SOURCE = "Patch 12.2.5 PTR reduces raid damage by 15%. These changes are testing only and are not yet live."
BODY = ("На PTR обновления 12.2.5 урон в рейде снизили на 15%. "
        "Изменения пока тестируются и на действующих серверах ещё не вступили в силу.")


def draft():
    return {"title": "На PTR снизили урон в рейде", "summary": "Правка проходит тестирование.",
            "body": BODY, "key_points": ["Урон снизили на 15%."],
            "evidence": [{"claim": "Урон снизили на 15% на PTR.", "quote": "Patch 12.2.5 PTR reduces raid damage by 15%."}]}


class ArticleClient:
    def __init__(self, candidate=None, review=None):
        self.responses = iter([candidate or draft(), review or {
            "accepted": True, "unsupported_claims": [], "missing_essential_facts": [], "status_errors": [],
            "language_errors": [],
        }])
        self.calls = []

    async def complete(self, system, user):
        self.calls.append(json.loads(user))
        return json.dumps(next(self.responses), ensure_ascii=False)


@pytest.mark.asyncio
async def test_website_uses_source_and_review_checks_final_text():
    client = ArticleClient()
    result = await generate_article(client, None, SOURCE)
    assert result.body == BODY
    assert client.calls[0]["source_text"] == SOURCE
    assert client.calls[1]["article"]["body"] == BODY


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["quote", "number", "html"])
async def test_unsupported_content_is_blocked_before_review(mutation):
    candidate = draft()
    if mutation == "quote":
        candidate["evidence"][0]["quote"] = "Blizzard confirmed release tomorrow"
    elif mutation == "number":
        candidate["body"] = BODY.replace("15%", "50%")
    else:
        candidate["body"] += " <script>alert()</script>"
    client = ArticleClient(candidate)
    with pytest.raises(ValueError, match="unsupported|plain text"):
        await generate_article(client, None, SOURCE)
    assert len(client.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["unsupported_claims", "missing_essential_facts", "status_errors", "language_errors"])
async def test_failed_review_never_releases_article(field):
    review = {"accepted": True, "unsupported_claims": [], "missing_essential_facts": [], "status_errors": [],
              "language_errors": []}
    review[field] = ["Detected factual error"]
    client = ArticleClient()
    client.responses = iter([draft(), review, draft(), review])
    with pytest.raises(ValueError, match="factual review"):
        await generate_article(client, None, SOURCE)
    assert len(client.calls) == 4


@pytest.mark.asyncio
async def test_review_receives_editor_changes():
    class Editor:
        async def edit(self, body):
            return body.replace("пока тестируются", "тестируются")

    client = ArticleClient()
    result = await generate_article(client, Editor(), SOURCE)
    assert result.body == client.calls[1]["article"]["body"]
    assert "пока тестируются" not in result.body


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["title", "summary", "body", "key_points"])
async def test_english_in_any_public_field_is_repaired_before_final_review(field):
    candidate = draft()
    if field == "key_points":
        candidate[field] = ["Raid damage снижен на 15%."]
    else:
        candidate[field] += " Raid damage."
    client = ArticleClient()
    client.responses = iter([candidate, draft(), {
        "accepted": True, "unsupported_claims": [], "missing_essential_facts": [],
        "status_errors": [], "language_errors": [],
    }])
    result = await generate_article(client, None, SOURCE)
    assert result.body == BODY
    assert "Raid" in client.calls[1]["language_issues"][0]
    assert client.calls[1]["source_text"] == SOURCE
    assert client.calls[2]["article"] == result.model_dump()


@pytest.mark.asyncio
async def test_repeating_lead_is_repaired():
    candidate = draft()
    candidate["summary"] = BODY.split(". ", 1)[0] + "."
    client = ArticleClient()
    client.responses = iter([candidate, draft(), {
        "accepted": True, "unsupported_claims": [], "missing_essential_facts": [],
        "status_errors": [], "language_errors": [],
    }])
    await generate_article(client, None, SOURCE)
    assert "Начало body повторяет summary" in client.calls[1]["language_issues"]


@pytest.mark.asyncio
async def test_failed_language_repair_is_blocked_without_unbounded_retries():
    candidate = draft()
    candidate["body"] += " Attunement."
    client = ArticleClient()
    client.responses = iter([candidate, candidate])
    with pytest.raises(ValueError, match="language review after repair"):
        await generate_article(client, None, SOURCE)
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_language_repair_cannot_introduce_new_numbers():
    candidate = draft()
    candidate["body"] += " Raid."
    repaired = draft()
    repaired["body"] = BODY.replace("15%", "50%")
    client = ArticleClient()
    client.responses = iter([candidate, repaired])
    with pytest.raises(ValueError, match="unsupported numbers"):
        await generate_article(client, None, SOURCE)
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_editor_cannot_bypass_language_checks():
    class Editor:
        async def edit(self, body):
            return body + " В источнике сказано."

    client = ArticleClient()
    client.responses = iter([draft(), draft(), {
        "accepted": True, "unsupported_claims": [], "missing_essential_facts": [],
        "status_errors": [], "language_errors": [],
    }])
    result = await generate_article(client, Editor(), SOURCE)
    assert result.body == BODY
    assert "в источнике сказано" in client.calls[1]["language_issues"][0]


@pytest.mark.asyncio
async def test_review_feedback_is_repaired_and_final_article_is_reviewed_again():
    rejected = {"accepted": False, "unsupported_claims": [], "missing_essential_facts": [],
                "status_errors": [], "language_errors": ["Первый абзац пересказывает лид"]}
    repaired = draft()
    repaired["summary"] = "На основных серверах правка пока не действует."
    client = ArticleClient()
    client.responses = iter([draft(), rejected, repaired, {
        "accepted": True, "unsupported_claims": [], "missing_essential_facts": [],
        "status_errors": [], "language_errors": [],
    }])
    result = await generate_article(client, None, SOURCE)
    assert result.summary == repaired["summary"]
    assert client.calls[2]["review_errors"]["language_errors"] == rejected["language_errors"]
    assert client.calls[3]["article"] == result.model_dump()
