from __future__ import annotations

import json
import logging
import os
import re
from typing import TypedDict

log = logging.getLogger(__name__)

PATH = "data/emojis.json"


class EmojiInfo(TypedDict, total=False):
    id: str
    fallback: str
    desc: str
    match_pattern: str  # если есть — это «лого», подбирается regex-ом по тексту


def load() -> dict[str, EmojiInfo]:
    """Читает data/emojis.json. Возвращает только записи с непустым id."""
    if not os.path.exists(PATH):
        return {}
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        log.exception("Не удалось прочитать %s", PATH)
        return {}
    out: dict[str, EmojiInfo] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            continue
        eid = str(val.get("id") or "").strip()
        fb = str(val.get("fallback") or "").strip()
        if not eid or not fb:
            continue
        entry: EmojiInfo = {
            "id": eid,
            "fallback": fb,
            "desc": str(val.get("desc") or ""),
        }
        mp = str(val.get("match_pattern") or "").strip()
        if mp:
            entry["match_pattern"] = mp
        out[key] = entry
    return out


def themes_for_prompt(emoji_map: dict[str, EmojiInfo]) -> list[dict]:
    """Темы для подсказки Gemini — только «общие категории», без логотипов
    (логотипы подбирает regex-override в коде, не Gemini)."""
    return [
        {"key": k, "desc": v.get("desc", "")}
        for k, v in emoji_map.items()
        if not v.get("match_pattern")
    ]


def detect_override(emoji_map: dict[str, EmojiInfo], text: str) -> str | None:
    """Подбирает специфический лого/язык по тексту title+body.
    Возвращает ключ темы (например `logo_anthropic`) или None если не нашли."""
    if not text:
        return None
    for key, info in emoji_map.items():
        pattern = info.get("match_pattern")
        if not pattern:
            continue
        try:
            if re.search(pattern, text, flags=re.IGNORECASE):
                return key
        except re.error:
            log.warning("Bad regex in emoji map for %s: %s", key, pattern)
    return None
