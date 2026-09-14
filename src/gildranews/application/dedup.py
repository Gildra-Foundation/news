from __future__ import annotations

from dataclasses import dataclass

from gildranews.domain.models import EventFingerprint


@dataclass(frozen=True, slots=True)
class DedupDecision:
    is_duplicate: bool
    is_material_update: bool
    reason: str


def compare_fingerprints(
    candidate: EventFingerprint,
    previous: EventFingerprint,
) -> DedupDecision:
    """Apply the deterministic part of the one-story publication policy."""
    if candidate.story_key != previous.story_key:
        return DedupDecision(False, False, "Другой сюжет")

    candidate_data = candidate.canonical()
    previous_data = previous.canonical()
    status_changed = bool(candidate_data["status"]) and (
        candidate_data["status"] != previous_data["status"]
    )
    date_changed = bool(candidate_data["effective_date"]) and (
        candidate_data["effective_date"] != previous_data["effective_date"]
    )
    new_facts = set(candidate_data["material_facts"]) - set(
        previous_data["material_facts"]
    )
    if status_changed or date_changed or new_facts:
        return DedupDecision(False, True, "Есть существенное развитие сюжета")
    return DedupDecision(True, False, "Тот же сюжет без новых существенных фактов")
