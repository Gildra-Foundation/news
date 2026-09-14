from __future__ import annotations

from gildranews.jobs.scheduler import find_orphan_screenshots


def test_find_orphan_screenshots_ignores_active_and_unrelated_files(tmp_path) -> None:
    active = tmp_path / "draft_1.png"
    orphan = tmp_path / "draft_2.png"
    unrelated = tmp_path / "cover.png"
    active.touch()
    orphan.touch()
    unrelated.touch()

    assert find_orphan_screenshots(tmp_path, {1}) == [orphan]
