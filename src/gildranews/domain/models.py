from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, TypedDict

_SPACE_RE = re.compile(r"\s+")


def _canonical_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return _SPACE_RE.sub(" ", normalized).strip()


class PublishedPostContext(TypedDict):
    title: str
    body: str
    posted_at: str


@dataclass(frozen=True, slots=True)
class EventFingerprint:
    """Canonical facts used to compare the same story across different sources."""

    game_branch: str
    version: str
    subject: str
    action: str
    status: str
    effective_date: str
    scope: tuple[str, ...] = ()
    material_facts: tuple[str, ...] = ()

    def canonical(self) -> dict[str, object]:
        return {
            "game_branch": _canonical_text(self.game_branch),
            "version": _canonical_text(self.version),
            "subject": _canonical_text(self.subject),
            "action": _canonical_text(self.action),
            "status": _canonical_text(self.status),
            "effective_date": _canonical_text(self.effective_date),
            "scope": sorted({_canonical_text(value) for value in self.scope if value.strip()}),
            "material_facts": sorted(
                {_canonical_text(value) for value in self.material_facts if value.strip()}
            ),
        }

    @property
    def story_key(self) -> str:
        data = self.canonical()
        story = {
            key: data[key]
            for key in ("game_branch", "version", "subject", "action")
        }
        payload = json.dumps(story, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()

    @property
    def revision_key(self) -> str:
        data = self.canonical()
        revision = {
            "story_key": self.story_key,
            "status": data["status"],
            "effective_date": data["effective_date"],
            "material_facts": data["material_facts"],
        }
        payload = json.dumps(revision, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class InfographicFact:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class InfographicSpec:
    title: str
    facts: tuple[InfographicFact, ...]
    kicker: str = "ГЛАВНОЕ В ЦИФРАХ"
    source: str = ""


@dataclass
class Rewrite:
    title: str
    body: str
    hashtag: str = ""
    infographic: InfographicSpec | None = None


@dataclass
class FilterResult:
    is_news: bool
    reason: str
    title: str = ""
    body: str = ""
    emoji_theme: str = ""
    hashtag: str = ""
    infographic: InfographicSpec | None = None


ProcessStatus = Literal[
    "already_published",
    "duplicate",
    "filtered",
    "published",
    "publish_failed",
    "ai_error",
    "error",
]


@dataclass
class ProcessResult:
    status: ProcessStatus
    channel: str
    message_id: int
    reason: str = ""
    title: str = ""
    source_url: str = ""
