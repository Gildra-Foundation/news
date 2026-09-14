from __future__ import annotations

from datetime import UTC, datetime

import pytest

from gildranews.adapters.sources.rss import (
    MAX_FEED_BYTES,
    extract_article_media,
    parse_feed,
    source_key,
)

WOWHEAD_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:media="http://search.yahoo.com/mrss/" version="2.0">
  <channel>
    <title>Wowhead News</title>
    <item>
      <title>Minimap Addon Tech Will Be Disabled</title>
      <link>https://www.wowhead.com/news=382863/minimap-addon-tech</link>
      <guid isPermaLink="false">https://www.wowhead.com/news=382863</guid>
      <pubDate>Mon, 14 Sep 2026 10:22:47 -0500</pubDate>
      <media:content url="https://wow.zamimg.com/image.jpg" medium="image" />
      <media:content url="https://wow.zamimg.com/clip.mp4" medium="video" type="video/mp4" />
      <content:encoded><![CDATA[
        Blizzard will disable <b>custom minimap assets</b> in a hotfix.
        <p>Addons will no longer call <code>SetSVG</code> on the compass.</p>
        <a href="https://example.invalid/source">Continue reading</a>
      ]]></content:encoded>
    </item>
  </channel>
</rss>
"""

ICY_VEINS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0">
  <channel>
    <title>Icy Veins - Feed</title>
    <item>
      <title>Blizzard Confirms a Midnight Change</title>
      <link>https://www.icy-veins.com/wow/news/blizzard-confirms-a-midnight-change/</link>
      <guid isPermaLink="false">https://www.icy-veins.com/wow/news/blizzard-confirms-a-midnight-change/</guid>
      <pubDate>Mon, 14 Sep 2026 16:59:58 +0000</pubDate>
      <description><![CDATA[A short summary.]]></description>
      <content:encoded><![CDATA[
        Blizzard confirmed a concrete change for Midnight Patch 12.2.
      ]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def test_parse_wowhead_feed_normalizes_article_for_ai() -> None:
    items = parse_feed(WOWHEAD_FEED, source="wowhead")

    assert len(items) == 1
    item = items[0]
    assert item.source == "wowhead"
    assert item.external_id == 382863
    assert item.title == "Minimap Addon Tech Will Be Disabled"
    assert item.published_at == datetime(2026, 9, 14, 15, 22, 47, tzinfo=UTC)
    assert item.image_url == "https://wow.zamimg.com/image.jpg"
    assert item.video_url == "https://wow.zamimg.com/clip.mp4"
    assert "custom minimap assets" in item.content
    assert "SetSVG" in item.content
    assert "https://" not in item.ai_text
    assert "Wowhead" not in item.ai_text


def test_parse_icy_veins_feed_uses_stable_source_and_hides_attribution() -> None:
    source = source_key("https://wp-prod.icy-veins.com/custom-rss/?category=wow")
    items = parse_feed(ICY_VEINS_FEED, source=source)

    assert source == "icy-veins"
    assert len(items) == 1
    assert items[0].source == "icy-veins"
    assert "Midnight Patch 12.2" in items[0].content
    assert "Icy Veins" not in items[0].ai_text


def test_parse_feed_rejects_oversized_document() -> None:
    with pytest.raises(ValueError, match="слишком большой"):
        parse_feed(b"x" * (MAX_FEED_BYTES + 1), source="wowhead")


def test_extract_article_media_finds_cover_and_direct_video() -> None:
    html = b"""
    <html><head>
      <meta property="og:image" content="https://wow.zamimg.com/cover.jpg">
      <meta property="og:video" content="https://wow.zamimg.com/clip.mp4">
    </head><body></body></html>
    """

    assert extract_article_media(html) == (
        "https://wow.zamimg.com/cover.jpg",
        "https://wow.zamimg.com/clip.mp4",
    )
