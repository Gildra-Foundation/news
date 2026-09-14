from __future__ import annotations

import httpx
import pytest

from gildranews.adapters.sources.reddit import fetch_top_posts, parse_listing


def test_parse_listing_keeps_safe_non_promoted_topics() -> None:
    topics = parse_listing(
        {
            "posts": [
                {
                    "id": "1abc23",
                    "title": "A useful Mythic+ route",
                    "text": "The route skips two dangerous pulls.",
                    "author": "alice",
                    "subreddit": "worldofwarcraft",
                    "upvotes": 420,
                    "comments": 73,
                    "over_18": False,
                    "stickied": False,
                },
                {
                    "id": "1bad99",
                    "title": "Pinned announcement",
                    "subreddit": "worldofwarcraft",
                    "upvotes": 9_999,
                    "comments": 1,
                    "stickied": True,
                },
            ]
        },
        subreddit="worldofwarcraft",
    )

    assert len(topics) == 1
    assert topics[0].reddit_id == "1abc23"
    assert topics[0].source == "reddit:worldofwarcraft"
    assert topics[0].external_id == int("1abc23", 36)
    assert topics[0].article_url == (
        "https://www.reddit.com/r/worldofwarcraft/comments/1abc23/"
    )
    assert "420 голосов" in topics[0].ai_text
    assert "73 комментария" in topics[0].ai_text


@pytest.mark.asyncio
async def test_fetch_top_posts_uses_daily_listing_and_bearer_token() -> None:
    seen: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(
            authorization=request.headers["Authorization"],
            query=str(request.url.query),
        )
        return httpx.Response(
            200,
            json={
                "posts": [
                    {
                        "id": "1abc23",
                        "title": "Useful topic",
                        "subreddit": "wow",
                        "upvotes": 100,
                        "comments": 20,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        topics = await fetch_top_posts(
            "secret", "wow", limit=15, http_client=client,
        )

    assert [topic.reddit_id for topic in topics] == ["1abc23"]
    assert seen["authorization"] == "Bearer secret"
    assert "subreddit=wow" in seen["query"]
    assert "sort=top" in seen["query"]
    assert "t=day" in seen["query"]
    assert "limit=15" in seen["query"]
