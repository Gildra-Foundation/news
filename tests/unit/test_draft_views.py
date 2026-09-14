from __future__ import annotations

from gildranews.presentation.telegram.drafts import draft_tail_url


def test_draft_tail_url_is_only_added_for_github() -> None:
    assert draft_tail_url({"source_url": "https://github.com/org/repo"}) == (
        "https://github.com/org/repo"
    )
    assert draft_tail_url({"source_url": "https://x.com/user/status/1"}) is None
    assert draft_tail_url({}) is None
