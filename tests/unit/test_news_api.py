from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from gildranews.adapters.persistence import publication_guard, site_news, sqlite
from gildranews.application.site_articles import WebsiteArticle
from gildranews.domain.models import EventFingerprint
from gildranews.news_api import _WORKER_KEY, article_worker, create_app


@pytest_asyncio.fixture
async def database(monkeypatch, tmp_path):
    await sqlite.close()
    monkeypatch.setattr(sqlite, "DB_PATH", str(tmp_path / "news.db"))
    await sqlite.init()
    yield await sqlite._get_conn()
    await sqlite.close()


def article():
    return WebsiteArticle(
        title="Изменения рейда на PTR", summary="Урон снизили на 15%.",
        body="На PTR обновления 12.2.5 урон в рейде снизили на 15%. "
             "Изменения пока тестируются и на действующих серверах ещё не вступили в силу.",
        key_points=["Изменение на PTR"],
        evidence=[{"claim": "Урон снизили", "quote": "damage reduced"}],
    )


async def enqueue_post(message_id):
    await sqlite.record_published("rss", message_id, "Telegram title", "Short Telegram body", 42,
                                  website_source=site_news.source_payload("damage reduced", "https://example.com/news"))


@pytest.mark.asyncio
async def test_publication_and_outbox_are_atomic(database):
    fingerprint = EventFingerprint("retail", "12.2.5", "raid", "reduce damage", "testing", "", material_facts=("15%",))
    reservation = await publication_guard.reserve_publication("rss", 1, fingerprint)
    await publication_guard.complete_publication(
        reservation.reservation_id, channel="rss", message_id=1, title="Title", body="Body", target_message_id=42,
        website_source=site_news.source_payload("damage reduced", "https://example.com/news", fingerprint=fingerprint),
    )
    async with database.execute("SELECT count(*) FROM website_jobs JOIN published_posts ON published_post_id=published_posts.id") as cursor:
        assert (await cursor.fetchone())[0] == 1
    assert (await site_news.list_articles())["items"] == []


@pytest.mark.asyncio
async def test_atomic_publication_rolls_back_when_enqueue_fails(database, monkeypatch):
    fingerprint = EventFingerprint("retail", "", "raid", "reduce damage", "testing", "")
    reservation = await publication_guard.reserve_publication("rss", 1, fingerprint)

    async def unavailable(*args):
        raise RuntimeError("outbox write failed")

    monkeypatch.setattr(site_news, "enqueue", unavailable)
    with pytest.raises(RuntimeError, match="outbox"):
        await publication_guard.complete_publication(
            reservation.reservation_id, channel="rss", message_id=1, title="Title", body="Body", target_message_id=42,
            website_source={"text": "source"},
        )
    assert await sqlite.was_published("rss", 1) is None


@pytest.mark.asyncio
async def test_legacy_publication_rolls_back_on_outbox_error(database, monkeypatch):
    async def unavailable(*args):
        raise RuntimeError("outbox write failed")

    monkeypatch.setattr(site_news, "enqueue", unavailable)
    with pytest.raises(RuntimeError, match="outbox"):
        await sqlite.record_published("telegram", 1, "title", "body", 42, website_source={})
    # A later unrelated commit must not make the rolled-back publication visible.
    await database.commit()
    assert await sqlite.was_published("telegram", 1) is None


@pytest.mark.asyncio
async def test_release_cursor_preserves_delayed_jobs_and_excludes_private_evidence(database):
    await enqueue_post(1)
    await enqueue_post(2)
    first = await site_news.claim_job()
    second = await site_news.claim_job()
    await site_news.complete_job(second, article())
    response = await site_news.list_articles()
    await site_news.complete_job(first, article())
    following = await site_news.list_articles(response["next_cursor"])
    assert len(following["items"]) == 1
    assert following["items"][0]["id"] > response["next_cursor"]
    assert "evidence" not in json.dumps(response)
    assert "source_json" not in json.dumps(response)
    assert response["items"][0]["sources"][0]["url"] == "https://example.com/news"


@pytest.mark.asyncio
async def test_expired_lease_can_be_retried_but_old_worker_cannot_publish(database):
    await enqueue_post(1)
    old = await site_news.claim_job()
    assert await site_news.claim_job() is None
    await database.execute("UPDATE website_jobs SET lease_until=datetime('now', '-1 minute')")
    await database.commit()
    current = await site_news.claim_job()
    assert current["attempts"] == 2
    await site_news.complete_job(old, article())
    assert (await site_news.list_articles())["items"] == []
    await site_news.complete_job(current, article())
    assert len((await site_news.list_articles())["items"]) == 1


@pytest.mark.asyncio
async def test_failed_jobs_stop_after_three_attempts(database):
    await enqueue_post(1)
    for attempt in range(3):
        job = await site_news.claim_job()
        assert job["attempts"] == attempt + 1
        await site_news.fail_job(job, "ValueError")
        await database.execute("UPDATE website_jobs SET available_at=datetime('now', '-1 minute')")
        await database.commit()
    assert await site_news.claim_job() is None
    assert (await site_news.list_articles())["items"] == []


@pytest.mark.asyncio
async def test_api_auth_pagination_detail_and_read_only_contract(database):
    await enqueue_post(1)
    await site_news.complete_job(await site_news.claim_job(), article())
    async with TestClient(TestServer(create_app("private-token"))) as client:
        assert (await client.get("/health")).status == 200
        assert (await client.get("/v1/news")).status == 401
        headers = {"Authorization": "Bearer private-token"}
        for query in ["limit=101", "limit=0", "after_id=-1", "after_id=garbage"]:
            assert (await client.get("/v1/news?" + query, headers=headers)).status == 400
        response = await client.get("/v1/news?limit=1", headers=headers)
        data = await response.json()
        assert response.status == 200
        article_id = data["items"][0]["id"]
        assert (await client.get(f"/v1/news/{article_id}", headers=headers)).status == 200
        assert (await client.get("/v1/news/999", headers=headers)).status == 404
        assert (await client.get("/v1/news/no", headers=headers)).status == 400
        assert (await client.post("/v1/news", headers=headers)).status == 405


@pytest.mark.asyncio
async def test_worker_generates_from_original_source(database):
    await enqueue_post(1)

    class AI:
        async def make_website_article(self, source_text):
            assert source_text == "damage reduced"
            return article()

    task = asyncio.create_task(article_worker(AI()))
    try:
        for _ in range(100):
            if (await site_news.list_articles())["items"]:
                break
            await asyncio.sleep(0.01)
        assert len((await site_news.list_articles())["items"]) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_health_detects_stopped_worker(database):
    async def finished():
        return

    task = asyncio.create_task(finished())
    await task
    app = create_app("private-token")
    app[_WORKER_KEY] = task
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/health")).status == 503
