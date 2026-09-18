from __future__ import annotations

import json
from pathlib import Path

from gildranews.application.editorial_eval import (
    EditorialCase,
    EditorialObservation,
    evaluate_editorial_cases,
    load_jsonl,
    main,
)


def _case(
    case_id: str,
    *,
    should_publish: bool = True,
    duplicate_group: str = "",
) -> EditorialCase:
    return EditorialCase.model_validate(
        {
            "id": case_id,
            "content_kind": "news",
            "source_text": "Patch 12.2.5 reduces raid damage by 15% on the PTR.",
            "expected": {
                "should_publish": should_publish,
                "branch": "retail",
                "required_facts": ["12.2.5", "15%", "PTR"],
                "forbidden_terms": ["source"],
                "duplicate_group": duplicate_group,
                "media_terms": ["raid", "boss"],
            },
        }
    )


def _observation(
    case_id: str,
    *,
    selected: bool = True,
    published: bool = True,
    branch: str = "retail",
    body: str = "На PTR обновления 12.2.5 урон в рейде снизят на 15%.",
    media_label: str = "raid boss artwork",
) -> EditorialObservation:
    return EditorialObservation(
        case_id=case_id,
        selected=selected,
        published=published,
        branch=branch,
        title="Ослабление рейда",
        body=body,
        media_label=media_label,
    )


def test_load_jsonl_validates_editorial_cases(tmp_path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        json.dumps(_case("raid-tuning").model_dump(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    loaded = load_jsonl(path, EditorialCase)

    assert [case.id for case in loaded] == ["raid-tuning"]


def test_versioned_editorial_fixture_is_valid_and_covers_all_source_kinds() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "editorial" / "cases.jsonl"

    loaded = load_jsonl(fixture, EditorialCase)

    assert len(loaded) >= 6
    assert {case.content_kind for case in loaded} >= {"news", "reddit_topic"}
    assert {case.expected.branch for case in loaded} >= {
        "retail",
        "classic",
        "forever",
        "unknown",
    }


def test_perfect_editorial_run_passes_all_quality_gates() -> None:
    report = evaluate_editorial_cases(
        [_case("raid-tuning")],
        [_observation("raid-tuning")],
    )

    assert report.passed is True
    assert report.total == 1
    assert report.false_rejections == ()
    assert report.false_acceptances == ()
    assert report.fact_failures == ()
    assert report.media_failures == ()


def test_editorial_run_reports_selection_fact_branch_and_media_failures() -> None:
    cases = [
        _case("important"),
        _case("advertisement", should_publish=False),
        _case("bad-output"),
    ]
    observations = [
        _observation("important", selected=False, published=False),
        _observation("advertisement", selected=True, published=True),
        _observation(
            "bad-output",
            branch="classic",
            body="Source: урон в подземелье изменят.",
            media_label="random landscape",
        ),
    ]

    report = evaluate_editorial_cases(cases, observations)

    assert report.passed is False
    assert report.false_rejections == ("important",)
    assert report.false_acceptances == ("advertisement",)
    assert report.branch_failures == ("bad-output",)
    assert report.fact_failures == ("bad-output",)
    assert report.language_failures == ("bad-output",)
    assert report.media_failures == ("bad-output",)


def test_editorial_run_reports_duplicate_publications_and_missing_observations() -> None:
    cases = [
        _case("wowhead", duplicate_group="raid-hotfix"),
        _case("icy-veins", duplicate_group="raid-hotfix"),
        _case("missing"),
    ]
    observations = [
        _observation("wowhead"),
        _observation("icy-veins"),
    ]

    report = evaluate_editorial_cases(cases, observations)

    assert report.passed is False
    assert report.duplicate_groups == ("raid-hotfix",)
    assert report.missing_observations == ("missing",)


def test_eval_cli_writes_json_and_returns_nonzero_for_failed_gate(tmp_path, capsys) -> None:
    cases_path = tmp_path / "cases.jsonl"
    observations_path = tmp_path / "observations.jsonl"
    cases_path.write_text(
        json.dumps(_case("important").model_dump(), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    observations_path.write_text(
        json.dumps(
            _observation("important", selected=False, published=False).model_dump(),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = main([str(cases_path), str(observations_path)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["passed"] is False
    assert output["false_rejections"] == ["important"]
