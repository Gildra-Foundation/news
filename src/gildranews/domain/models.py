from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
