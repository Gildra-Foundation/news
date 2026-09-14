from __future__ import annotations

import httpx
import pytest
from web_scraper.providers.base import ProviderResponse

from gildranews.adapters.sources.external import (
    _fetch_reddit,
    _fetch_scrape_do_json,
    _fetch_tweet,
    _post_from_getxapi,
    _post_from_redditapis,
)


def test_getxapi_payload_is_normalized_for_telegram_preview() -> None:
    post = _post_from_getxapi(
        {
            "id": "123",
            "url": "https://x.com/example/status/123",
            "text": "Release notes",
            "author": {
                "name": "Example",
                "userName": "example",
                "profilePicture": "https://cdn.example/avatar.jpg",
                "isBlueVerified": True,
            },
            "media": [{"type": "photo", "url": "https://cdn.example/photo.jpg"}],
        }
    )

    assert post is not None
    assert post.source == "twitter"
    assert post.text == "Release notes"
    assert post.media_type == "photo"
    assert post.media_url == "https://cdn.example/photo.jpg"
    assert post.screen_name == "example"
    assert post.meta == {
        "avatar_url": "https://cdn.example/avatar.jpg",
        "verified": True,
    }


def test_redditapis_payload_is_normalized_for_telegram_preview() -> None:
    post = _post_from_redditapis(
        {
            "id": "abc123",
            "title": "A useful project",
            "text": "Details",
            "author": "alice",
            "subreddit": "programming",
            "permalink": "/r/programming/comments/abc123/a_useful_project/",
            "link_url": "https://images.example/project.webp",
        }
    )

    assert post is not None
    assert post.source == "reddit"
    assert post.text == "A useful project\n\nDetails"
    assert post.media_type == "photo"
    assert post.media_url == "https://images.example/project.webp"
    assert post.author == "u/alice"
    assert post.source_url == ("https://reddit.com/r/programming/comments/abc123/a_useful_project/")


@pytest.mark.asyncio
async def test_scrape_do_fallback_requires_explicit_enable(monkeypatch) -> None:
    calls = []

    class FakeProvider:
        def fetch(self, request):
            calls.append(request)
            return ProviderResponse(
                provider="scrape.do",
                strategy_id="normal",
                target_status=200,
                provider_status=200,
                body=b'{"tweet":{"text":"hello"}}',
                headers={"content-type": "application/json"},
                final_url=request.url,
            )

    monkeypatch.setattr("gildranews.adapters.sources.external.ScrapeDoProvider", FakeProvider)
    monkeypatch.setenv("SCRAPE_DO_TOKEN", "secret")
    monkeypatch.delenv("SCRAPE_DO_ENABLED", raising=False)

    disabled = await _fetch_scrape_do_json("https://api.example/item", (("tweet.text",),), "test")
    assert disabled is None
    assert calls == []

    monkeypatch.setenv("SCRAPE_DO_ENABLED", "true")
    enabled = await _fetch_scrape_do_json("https://api.example/item", (("tweet.text",),), "test")
    assert enabled == {"tweet": {"text": "hello"}}
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_tweet_uses_scrape_do_only_after_free_route_fails(monkeypatch) -> None:
    class FailedClient:
        async def get(self, url, **kwargs):
            return httpx.Response(502, request=httpx.Request("GET", url))

    async def scrape_fallback(url, required_paths, source):
        assert source == "FxTwitter"
        return {
            "tweet": {
                "id": "123",
                "text": "fallback",
                "url": "https://x.com/example/status/123",
                "author": {"name": "Example", "screen_name": "example"},
            }
        }

    monkeypatch.delenv("GETXAPI_KEY", raising=False)
    monkeypatch.setattr("gildranews.adapters.sources.external._get_http", FailedClient)
    monkeypatch.setattr(
        "gildranews.adapters.sources.external._fetch_scrape_do_json", scrape_fallback
    )

    post = await _fetch_tweet("123")

    assert post is not None
    assert post.text == "fallback"


@pytest.mark.asyncio
async def test_reddit_uses_scrape_do_only_after_free_routes_fail(monkeypatch) -> None:
    class FailedClient:
        async def get(self, url, **kwargs):
            return httpx.Response(403, request=httpx.Request("GET", url))

    async def scrape_fallback(url, required_paths, source):
        assert source == "Reddit"
        return [
            {
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "abc123",
                                "title": "fallback",
                                "selftext": "body",
                                "author": "alice",
                                "permalink": "/r/python/comments/abc123/fallback/",
                            }
                        }
                    ]
                }
            }
        ]

    monkeypatch.delenv("REDDITAPIS_KEY", raising=False)
    monkeypatch.setattr("gildranews.adapters.sources.external._get_http", FailedClient)
    monkeypatch.setattr(
        "gildranews.adapters.sources.external._fetch_scrape_do_json", scrape_fallback
    )

    post = await _fetch_reddit("python", "abc123")

    assert post is not None
    assert post.text == "fallback\n\nbody"
