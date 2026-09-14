from __future__ import annotations

from gildranews.application.digest import _post_link


def test_post_link_uses_configured_public_channel() -> None:
    assert _post_link("@gildrawow", 42) == "https://t.me/gildrawow/42"
