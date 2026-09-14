from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class Rewrite:
    title: str
    body: str
    hashtag: str = ""


@dataclass
class FilterResult:
    is_news: bool
    reason: str
    title: str = ""
    body: str = ""
    emoji_theme: str = ""
    hashtag: str = ""


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
