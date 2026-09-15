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


WarcraftBranch = Literal["retail", "classic", "forever"]
WarcraftEntityKind = Literal[
    "class",
    "specialization",
    "spell",
    "talent",
    "item",
    "cosmetic",
    "transmog_set",
    "mount",
    "pet",
    "achievement",
    "raid",
    "dungeon",
    "boss",
    "creature",
    "faction",
    "profession",
    "event",
]
WarcraftEntityRole = Literal["primary", "secondary"]


@dataclass(frozen=True, slots=True)
class WarcraftEntityRef:
    label: str
    query: str
    kind: WarcraftEntityKind
    branch: WarcraftBranch = "retail"
    role: WarcraftEntityRole = "secondary"


# Backwards-compatible import used by existing integrations.
EntityReference = WarcraftEntityRef


@dataclass(frozen=True, slots=True)
class ResolvedWarcraftEntity:
    branch: WarcraftBranch
    kind: WarcraftEntityKind
    external_id: int
    canonical_name: str
    localized_name: str
    page_url: str
    icon_url: str

    @property
    def key(self) -> str:
        return f"{self.branch}:{self.kind}:{self.external_id}"


@dataclass(frozen=True, slots=True)
class TelegramEmojiAsset:
    custom_emoji_id: str
    file_id: str
    sticker_set_name: str
    fallback: str
    placement_label: str = ""

    @property
    def html(self) -> str:
        return f'<tg-emoji emoji-id="{self.custom_emoji_id}">{self.fallback}</tg-emoji>'


@dataclass
class Rewrite:
    title: str
    body: str
    hashtag: str = ""
    infographic: InfographicSpec | None = None
    references: tuple[EntityReference, ...] = ()


@dataclass
class FilterResult:
    is_news: bool
    reason: str
    title: str = ""
    body: str = ""
    emoji_theme: str = ""
    hashtag: str = ""
    infographic: InfographicSpec | None = None
    references: tuple[EntityReference, ...] = ()


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
