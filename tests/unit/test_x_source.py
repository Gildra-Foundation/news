from __future__ import annotations

import httpx
import pytest

from gildranews.adapters.sources.x_search import fetch_top_posts, parse_search


def test_parse_search_keeps_safe_original_posts() -> None:
    topics = parse_search(
        {
            "tweets": [
                {
                    "id": "2015410770834591992",
                    "text": "A useful World of Warcraft route with concrete details.",
                    "likeCount": 250,
                    "retweetCount": 30,
                    "replyCount": 18,
                    "viewCount": 8_000,
                    "isReply": False,
                    "author": {"userName": "WarcraftPlayer"},
                },
                {
                    "id": "2015410770834591993",
                    "text": "A reply",
                    "isReply": True,
                    "author": {"userName": "WarcraftPlayer"},
                },
            ]
        }
    )

    assert len(topics) == 1
    assert topics[0].tweet_id == 2015410770834591992
    assert topics[0].source == "x"
    assert topics[0].article_url == (
        "https://x.com/WarcraftPlayer/status/2015410770834591992"
    )
    assert "250 отметок" in topics[0].ai_text
    assert "30 репостов" in topics[0].ai_text


@pytest.mark.asyncio
async def test_fetch_top_posts_uses_getxapi_top_search_and_bearer_token() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            authorization=request.headers["Authorization"],
            query=str(request.url.query),
        )
        return httpx.Response(
            200,
            json={
                "tweets": [
                    {
                        "id": "2015410770834591992",
                        "text": "Useful Warcraft topic",
                        "author": {"userName": "Example"},
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        topics = await fetch_top_posts(
            "secret",
            '"World of Warcraft" lang:en since:2026-09-13',
            http_client=client,
        )

    assert [topic.tweet_id for topic in topics] == [2015410770834591992]
    assert seen["authorization"] == "Bearer secret"
    assert "q=%22World+of+Warcraft%22" in seen["query"]
    assert "product=Top" in seen["query"]
