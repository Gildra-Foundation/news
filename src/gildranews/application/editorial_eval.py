from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from gildranews.domain.models import WarcraftBranch

EvaluationBranch = WarcraftBranch | Literal["unknown"]


class EditorialExpectation(BaseModel):
    should_publish: bool
    branch: EvaluationBranch
    required_facts: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    duplicate_group: str = ""
    media_terms: list[str] = Field(default_factory=list)


class EditorialCase(BaseModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    content_kind: Literal["news", "reddit_topic", "x_topic", "manual"] = "news"
    source_text: str = Field(min_length=1, max_length=20_000)
    expected: EditorialExpectation


class EditorialObservation(BaseModel):
    case_id: str
    selected: bool
    published: bool
    branch: EvaluationBranch = "unknown"
    title: str = ""
    body: str = ""
    media_label: str = ""


class EditorialEvaluationReport(BaseModel):
    passed: bool
    total: int
    observed: int
    missing_observations: tuple[str, ...] = ()
    unexpected_observations: tuple[str, ...] = ()
    false_rejections: tuple[str, ...] = ()
    false_acceptances: tuple[str, ...] = ()
    branch_failures: tuple[str, ...] = ()
    fact_failures: tuple[str, ...] = ()
    language_failures: tuple[str, ...] = ()
    media_failures: tuple[str, ...] = ()
    duplicate_groups: tuple[str, ...] = ()


def load_jsonl[ModelT: BaseModel](path: Path, model: type[ModelT]) -> list[ModelT]:
    values: list[ModelT] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        value = model.model_validate_json(line)
        identity = str(getattr(value, "id", getattr(value, "case_id", "")))
        if identity and identity in seen_ids:
            raise ValueError(f"Повторяющийся id {identity!r} в {path}:{line_number}")
        if identity:
            seen_ids.add(identity)
        values.append(value)
    return values


def _contains(value: str, needle: str) -> bool:
    return needle.casefold() in value.casefold()


def evaluate_editorial_cases(
    cases: list[EditorialCase],
    observations: list[EditorialObservation],
) -> EditorialEvaluationReport:
    case_by_id = {case.id: case for case in cases}
    observation_by_id = {observation.case_id: observation for observation in observations}
    missing = tuple(sorted(set(case_by_id) - set(observation_by_id)))
    unexpected = tuple(sorted(set(observation_by_id) - set(case_by_id)))

    false_rejections: list[str] = []
    false_acceptances: list[str] = []
    branch_failures: list[str] = []
    fact_failures: list[str] = []
    language_failures: list[str] = []
    media_failures: list[str] = []
    published_groups: Counter[str] = Counter()

    for case_id in sorted(set(case_by_id) & set(observation_by_id)):
        case = case_by_id[case_id]
        observation = observation_by_id[case_id]
        expected = case.expected
        if expected.should_publish and not observation.selected:
            false_rejections.append(case_id)
        if not expected.should_publish and observation.published:
            false_acceptances.append(case_id)
        if not observation.published:
            continue

        public_text = f"{observation.title}\n{observation.body}"
        if observation.branch != expected.branch:
            branch_failures.append(case_id)
        if any(not _contains(public_text, fact) for fact in expected.required_facts):
            fact_failures.append(case_id)
        if any(_contains(public_text, term) for term in expected.forbidden_terms):
            language_failures.append(case_id)
        if expected.media_terms and not any(
            _contains(observation.media_label, term) for term in expected.media_terms
        ):
            media_failures.append(case_id)
        if expected.duplicate_group:
            published_groups[expected.duplicate_group] += 1

    duplicate_groups = tuple(
        sorted(group for group, count in published_groups.items() if count > 1)
    )
    failures = (
        missing,
        unexpected,
        tuple(false_rejections),
        tuple(false_acceptances),
        tuple(branch_failures),
        tuple(fact_failures),
        tuple(language_failures),
        tuple(media_failures),
        duplicate_groups,
    )
    return EditorialEvaluationReport(
        passed=not any(failures),
        total=len(cases),
        observed=len(observations),
        missing_observations=missing,
        unexpected_observations=unexpected,
        false_rejections=tuple(false_rejections),
        false_acceptances=tuple(false_acceptances),
        branch_failures=tuple(branch_failures),
        fact_failures=tuple(fact_failures),
        language_failures=tuple(language_failures),
        media_failures=tuple(media_failures),
        duplicate_groups=duplicate_groups,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Проверить результаты GildraNews на редакционной выборке.",
    )
    parser.add_argument("cases", type=Path)
    parser.add_argument("observations", type=Path)
    args = parser.parse_args(argv)
    cases = load_jsonl(args.cases, EditorialCase)
    observations = load_jsonl(args.observations, EditorialObservation)
    report = evaluate_editorial_cases(cases, observations)
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
