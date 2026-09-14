"""Рендер карточек-«скриншотов» для твитов, Reddit-постов, GitHub-репо.

Дизайн-система:
— ширина фиксированная 1080 (универсально для Telegram)
— высота адаптивная в диапазоне 16:9 — 4:5, длинный текст обрезается с многоточием
— палитра и типографика — общая константа, чтобы карточки выглядели одной семьёй
— скруглённые углы, чистый минимализм
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

log = logging.getLogger(__name__)

# ---------- Канвас ----------
WIDTH = 1080
MIN_HEIGHT = 608   # 16:9
MAX_HEIGHT = 1350  # 4:5
PADDING = 64
CORNER_RADIUS = 32  # скруглённые углы у карточки
INNER_LINE_GAP = 14

# ---------- Палитра ----------
# Светлая (Twitter) — мягкий белый, тёмный текст
LIGHT_BG = (255, 255, 255)
LIGHT_FG = (15, 20, 25)
LIGHT_MUTED = (83, 100, 113)
LIGHT_BORDER = (239, 243, 244)
X_BLUE = (29, 155, 240)  # верифицированный значок

# Тёмная (Reddit/GitHub) — фирменно-тёмный, светлый текст
DARK_BG = (22, 26, 30)
DARK_FG = (231, 233, 234)
DARK_MUTED = (139, 152, 165)
DARK_BORDER = (47, 51, 54)
DARK_CARD = (15, 18, 20)  # ещё чуть глубже для контраста

REDDIT_ORANGE = (255, 69, 0)
GITHUB_FG = (240, 246, 252)
GITHUB_ACCENT = (88, 166, 255)
GITHUB_GREEN = (63, 185, 80)

# ---------- Шрифты ----------
FONT_CANDIDATES_REGULAR = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]
FONT_CANDIDATES_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]

_FONT_CACHE: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}


def _font_path(bold: bool) -> str:
    for p in (FONT_CANDIDATES_BOLD if bold else FONT_CANDIDATES_REGULAR):
        if os.path.exists(p):
            return p
    raise FileNotFoundError("Не найден ни один шрифт DejaVu/Arial/Helvetica")


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    key = (bold, size)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = ImageFont.truetype(_font_path(bold), size)
        _FONT_CACHE[key] = f
    return f


# ---------- Утилиты ----------
def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            out.append("")
            continue
        words = paragraph.split(" ")
        line = ""
        for word in words:
            test = (line + " " + word).strip() if line else word
            if draw.textlength(test, font=font) <= max_width:
                line = test
            else:
                if line:
                    out.append(line)
                line = word
        if line:
            out.append(line)
    return out


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    # Используем ascent+descent, более стабильно чем bbox конкретного текста
    ascent, descent = font.getmetrics()
    return ascent + descent + INNER_LINE_GAP


def _truncate_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font,
    max_lines: int,
    max_width: int,
) -> list[str]:
    if max_lines <= 0:
        return []
    if len(lines) <= max_lines:
        return lines
    kept = list(lines[:max_lines])
    last = (kept[-1] or "").rstrip()
    while last and draw.textlength(last + "…", font=font) > max_width:
        last = last[:-1].rstrip()
    kept[-1] = (last + "…") if last else "…"
    return kept


def _format_count(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def _circular_avatar(path: str, size: int) -> Image.Image | None:
    """Круглая аватарка с прозрачным фоном."""
    try:
        src = Image.open(path).convert("RGBA")
    except (OSError, ValueError, UnidentifiedImageError):
        return None
    # Crop centred square, resize, mask
    w, h = src.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    src = src.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.Resampling.LANCZOS
    )
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src, (0, 0), mask=mask)
    return out


def _initial_avatar(letter: str, size: int, *, bg=(100, 100, 130), fg=(255, 255, 255)) -> Image.Image:
    """Фолбэк-аватарка: круг с буквой."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size, size), fill=bg)
    font = _font(True, int(size * 0.55))
    text = (letter or "?").upper()[:1]
    tw = d.textlength(text, font=font)
    ascent, descent = font.getmetrics()
    th = ascent + descent
    d.text(
        ((size - tw) / 2, (size - th) / 2 - 2),
        text, fill=fg, font=font,
    )
    return img


def _round_corners(img: Image.Image, radius: int) -> Image.Image:
    """Скругляет углы прямоугольной картинки. Возвращает RGBA."""
    rgba = img.convert("RGBA")
    mask = Image.new("L", rgba.size, 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, 0, rgba.size[0], rgba.size[1]), radius=radius, fill=255)
    rgba.putalpha(mask)
    return rgba


def _verified_badge(size: int, *, color=X_BLUE) -> Image.Image:
    """Простой синий значок верификации — круг с галочкой."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size, size), fill=color)
    # Галочка: две линии
    s = size
    d.line(
        [(s * 0.30, s * 0.52), (s * 0.46, s * 0.68), (s * 0.74, s * 0.36)],
        fill=(255, 255, 255), width=max(2, int(s * 0.10)),
    )
    return img


# Цвета языков GitHub (топ-30 для AI/web/data)
LANG_COLORS = {
    "Python": (53, 114, 165),
    "JavaScript": (241, 224, 90),
    "TypeScript": (49, 120, 198),
    "Rust": (222, 165, 132),
    "Go": (0, 173, 216),
    "Java": (176, 114, 25),
    "C++": (243, 75, 125),
    "C": (85, 85, 85),
    "C#": (23, 134, 0),
    "Ruby": (112, 21, 22),
    "Swift": (255, 172, 69),
    "Kotlin": (169, 123, 255),
    "Shell": (137, 224, 81),
    "HTML": (227, 76, 38),
    "CSS": (86, 61, 124),
    "Vue": (65, 184, 131),
    "Svelte": (255, 62, 0),
    "Jupyter Notebook": (218, 91, 12),
    "PHP": (79, 93, 149),
    "Lua": (0, 0, 128),
    "Dart": (0, 180, 171),
    "Elixir": (110, 74, 126),
    "Scala": (194, 45, 64),
    "Haskell": (94, 80, 134),
    "R": (25, 140, 232),
    "Zig": (236, 145, 92),
    "Solidity": (170, 102, 70),
    "Cuda": (58, 79, 152),
    "Dockerfile": (56, 76, 108),
    "Markdown": (8, 60, 152),
}


# ============================================================
#                       TWITTER / X
# ============================================================
def render_tweet(
    *,
    text: str,
    author_name: str,
    screen_name: str,
    dest_path: str,
    avatar_path: str | None = None,
    verified: bool = False,
) -> str:
    name_font = _font(True, 38)
    handle_font = _font(False, 30)
    text_font = _font(False, 40)
    brand_font = _font(True, 56)

    text_max_w = WIDTH - 2 * PADDING

    tmp = Image.new("RGB", (10, 10))
    td = ImageDraw.Draw(tmp)
    lines = _wrap_text(td, text, text_font, text_max_w)
    line_h = _line_height(text_font)

    avatar_size = 96
    header_h = avatar_size + 16
    gap_after_header = 40
    gap_before_footer = 24
    footer_h = 56
    fixed_h = PADDING + header_h + gap_after_header + gap_before_footer + footer_h + PADDING

    desired_total = fixed_h + len(lines) * line_h
    if desired_total <= MIN_HEIGHT:
        height = MIN_HEIGHT
    elif desired_total <= MAX_HEIGHT:
        height = desired_total
    else:
        max_text_h = MAX_HEIGHT - fixed_h
        max_lines = max(1, max_text_h // line_h)
        lines = _truncate_lines(td, lines, text_font, max_lines, text_max_w)
        height = MAX_HEIGHT

    img = Image.new("RGBA", (WIDTH, height), LIGHT_BG)
    draw = ImageDraw.Draw(img)

    # Шапка: аватарка + имя/handle
    avatar_img: Image.Image | None = None
    if avatar_path and os.path.exists(avatar_path):
        avatar_img = _circular_avatar(avatar_path, avatar_size)
    if avatar_img is None:
        # фолбэк — первая буква
        initial = (author_name or screen_name or "?")[0]
        avatar_img = _initial_avatar(initial, avatar_size, bg=(180, 200, 220), fg=(20, 30, 50))
    img.paste(avatar_img, (PADDING, PADDING), avatar_img)

    name_x = PADDING + avatar_size + 22
    name_y = PADDING + 6
    draw.text((name_x, name_y), author_name, fill=LIGHT_FG, font=name_font)

    # Если verified — рисуем синий значок справа от имени
    if verified:
        nw = draw.textlength(author_name, font=name_font)
        badge_size = 36
        badge = _verified_badge(badge_size)
        ascent, _ = name_font.getmetrics()
        badge_y = name_y + (ascent - badge_size) // 2 + 2
        img.paste(badge, (int(name_x + nw + 10), badge_y), badge)

    handle_y = name_y + name_font.size + 12
    draw.text((name_x, handle_y), f"@{screen_name}", fill=LIGHT_MUTED, font=handle_font)

    # Тело
    y = PADDING + header_h + gap_after_header
    for line in lines:
        draw.text((PADDING, y), line, fill=LIGHT_FG, font=text_font)
        y += line_h

    # Низ: разделитель + лого X
    sep_y = height - PADDING - footer_h - 10
    draw.line([(PADDING, sep_y), (WIDTH - PADDING, sep_y)], fill=LIGHT_BORDER, width=2)
    brand_text = "𝕏"
    try:
        bw = draw.textlength(brand_text, font=brand_font)
    except Exception:  # noqa: BLE001 - font backends expose inconsistent glyph errors
        brand_text = "X"
        bw = draw.textlength(brand_text, font=brand_font)
    if bw < 10:
        brand_text = "X"
        bw = draw.textlength(brand_text, font=brand_font)
    draw.text(
        (WIDTH - PADDING - bw, height - PADDING - footer_h + 4),
        brand_text, fill=LIGHT_FG, font=brand_font,
    )

    _flatten_and_save(img, dest_path, bg=LIGHT_BG)
    return dest_path


# ============================================================
#                          REDDIT
# ============================================================
def render_reddit(
    *,
    title: str,
    body: str,
    subreddit: str,
    author: str,
    dest_path: str,
) -> str:
    sub_font = _font(True, 30)
    meta_font = _font(False, 26)
    title_font = _font(True, 46)
    body_font = _font(False, 32)
    brand_font = _font(True, 36)

    text_max_w = WIDTH - 2 * PADDING

    tmp = Image.new("RGB", (10, 10))
    td = ImageDraw.Draw(tmp)
    title_lines = _wrap_text(td, title, title_font, text_max_w)
    body_lines = _wrap_text(td, body, body_font, text_max_w) if body else []

    title_lh = _line_height(title_font)
    body_lh = _line_height(body_font)

    title_lines = _truncate_lines(td, title_lines, title_font, 3, text_max_w)
    title_h = len(title_lines) * title_lh

    meta_h = 50
    gap_after_meta = 28
    gap_after_title = 24 if body_lines else 0
    gap_before_footer = 24
    footer_h = 50

    fixed_h = (PADDING + meta_h + gap_after_meta + title_h + gap_after_title
               + gap_before_footer + footer_h + PADDING)

    desired_total = fixed_h + len(body_lines) * body_lh
    if desired_total <= MIN_HEIGHT:
        height = MIN_HEIGHT
    elif desired_total <= MAX_HEIGHT:
        height = desired_total
    else:
        max_body_h = MAX_HEIGHT - fixed_h
        max_body_lines = max(0, max_body_h // body_lh)
        body_lines = _truncate_lines(td, body_lines, body_font, max_body_lines, text_max_w)
        height = MAX_HEIGHT

    img = Image.new("RGBA", (WIDTH, height), DARK_BG)
    draw = ImageDraw.Draw(img)

    # Шапка: r/sub оранжевым, автор серым
    sub_text = f"r/{subreddit}"
    draw.text((PADDING, PADDING), sub_text, fill=REDDIT_ORANGE, font=sub_font)
    if author:
        sw = draw.textlength(sub_text, font=sub_font)
        # точка-разделитель
        dot_x = PADDING + sw + 18
        ascent, _ = sub_font.getmetrics()
        draw.ellipse(
            (dot_x, PADDING + ascent // 2 - 3, dot_x + 6, PADDING + ascent // 2 + 3),
            fill=DARK_MUTED,
        )
        draw.text((dot_x + 18, PADDING + 2), author, fill=DARK_MUTED, font=meta_font)

    # Заголовок
    y = PADDING + meta_h + gap_after_meta
    for line in title_lines:
        draw.text((PADDING, y), line, fill=DARK_FG, font=title_font)
        y += title_lh

    # Тело
    if body_lines:
        y += gap_after_title - 10
        for line in body_lines:
            draw.text((PADDING, y), line, fill=DARK_MUTED, font=body_font)
            y += body_lh

    # Низ: разделитель + бренд
    sep_y = height - PADDING - footer_h - 10
    draw.line([(PADDING, sep_y), (WIDTH - PADDING, sep_y)], fill=DARK_BORDER, width=2)
    draw.text(
        (PADDING, height - PADDING - footer_h + 8),
        "reddit", fill=REDDIT_ORANGE, font=brand_font,
    )

    _flatten_and_save(img, dest_path, bg=DARK_BG)
    return dest_path


# ============================================================
#                          GITHUB
# ============================================================
def render_github(
    *,
    full_name: str,
    description: str,
    language: str,
    stars: int,
    forks: int,
    dest_path: str,
    topics: list[str] | None = None,
) -> str:
    name_font = _font(True, 50)
    desc_font = _font(False, 32)
    topic_font = _font(True, 24)
    meta_font = _font(True, 30)
    brand_font = _font(True, 36)

    text_max_w = WIDTH - 2 * PADDING

    tmp = Image.new("RGB", (10, 10))
    td = ImageDraw.Draw(tmp)

    name_lines = _truncate_lines(
        td, _wrap_text(td, full_name, name_font, text_max_w),
        name_font, 2, text_max_w,
    )
    desc_lines = _truncate_lines(
        td, _wrap_text(td, description or "", desc_font, text_max_w),
        desc_font, 4, text_max_w,
    ) if description else []

    name_lh = _line_height(name_font)
    desc_lh = _line_height(desc_font)

    # Topics — до 4 «чипов»
    topic_chips: list[tuple[str, int]] = []
    if topics:
        cur_x = 0
        chip_pad = 22
        for t in topics[:6]:
            chip = f"#{t}"
            cw = int(td.textlength(chip, font=topic_font)) + chip_pad * 2
            if cur_x + cw > text_max_w and topic_chips:
                break
            topic_chips.append((chip, cw))
            cur_x += cw + 14

    name_h = len(name_lines) * name_lh
    desc_h = len(desc_lines) * desc_lh
    topics_h = 50 if topic_chips else 0
    meta_h = 56
    footer_h = 56

    gap_after_name = 26
    gap_after_desc = 22 if desc_lines else 0
    gap_after_topics = 30 if topic_chips else 0
    gap_before_footer = 22

    fixed_h = (PADDING + name_h + gap_after_name + desc_h + gap_after_desc
               + topics_h + gap_after_topics
               + meta_h + gap_before_footer + footer_h + PADDING)

    height = max(MIN_HEIGHT, min(MAX_HEIGHT, fixed_h))

    img = Image.new("RGBA", (WIDTH, height), DARK_BG)
    draw = ImageDraw.Draw(img)

    # Имя
    y = PADDING
    for line in name_lines:
        draw.text((PADDING, y), line, fill=GITHUB_FG, font=name_font)
        y += name_lh

    # Описание
    if desc_lines:
        y += gap_after_name
        for line in desc_lines:
            draw.text((PADDING, y), line, fill=DARK_MUTED, font=desc_font)
            y += desc_lh

    # Topic chips
    if topic_chips:
        y += gap_after_desc - 4
        chip_x = PADDING
        chip_y = y
        chip_h = 44
        for text, cw in topic_chips:
            draw.rounded_rectangle(
                (chip_x, chip_y, chip_x + cw, chip_y + chip_h),
                radius=chip_h // 2, fill=(30, 36, 42), outline=DARK_BORDER, width=1,
            )
            tw_chip = td.textlength(text, font=topic_font)
            ascent, _ = topic_font.getmetrics()
            draw.text(
                (chip_x + (cw - tw_chip) / 2, chip_y + (chip_h - ascent) / 2 + 2),
                text, fill=GITHUB_ACCENT, font=topic_font,
            )
            chip_x += cw + 14
        y += chip_h
    else:
        y += gap_after_desc

    # Полоска языка + цвет
    meta_y = height - PADDING - footer_h - gap_before_footer - meta_h
    cursor_x = PADDING
    if language:
        lang_color = LANG_COLORS.get(language, (140, 140, 140))
        dot_size = 16
        ascent, _ = meta_font.getmetrics()
        dot_y = meta_y + (ascent - dot_size) // 2 + 2
        draw.ellipse(
            (cursor_x, dot_y, cursor_x + dot_size, dot_y + dot_size),
            fill=lang_color,
        )
        cursor_x += dot_size + 12
        draw.text((cursor_x, meta_y), language, fill=GITHUB_FG, font=meta_font)
        cursor_x += int(td.textlength(language, font=meta_font)) + 40

    if stars:
        stars_text = f"{_format_count(stars)} stars"
        draw.text((cursor_x, meta_y), stars_text, fill=GITHUB_FG, font=meta_font)
        cursor_x += int(td.textlength(stars_text, font=meta_font)) + 40
    if forks:
        forks_text = f"{_format_count(forks)} forks"
        draw.text((cursor_x, meta_y), forks_text, fill=DARK_MUTED, font=meta_font)

    # Разделитель + GitHub
    sep_y = height - PADDING - footer_h - 10
    draw.line([(PADDING, sep_y), (WIDTH - PADDING, sep_y)], fill=DARK_BORDER, width=2)
    draw.text(
        (PADDING, height - PADDING - footer_h + 8),
        "GitHub", fill=GITHUB_FG, font=brand_font,
    )

    _flatten_and_save(img, dest_path, bg=DARK_BG)
    return dest_path


# ============================================================
#                       Сохранение
# ============================================================
def _flatten_and_save(img: Image.Image, dest_path: str, *, bg) -> None:
    """RGBA → RGB на фоне, скруглённые углы, оптимизированный PNG."""
    rounded = _round_corners(img, CORNER_RADIUS)
    # Подкладываем фон-страницу (бо́льшего размера с тем же цветом)
    page = Image.new("RGB", img.size, bg)
    page.paste(rounded, (0, 0), rounded)
    page.save(dest_path, format="PNG", optimize=True)


# ============================================================
#                      Файлы скриншотов
# ============================================================
def screenshot_dir() -> Path:
    p = Path("data/screenshots")
    p.mkdir(parents=True, exist_ok=True)
    return p


def screenshot_path_for(draft_id: int) -> str:
    return str(screenshot_dir() / f"draft_{draft_id}.png")


def cleanup_screenshot(path: str | None) -> None:
    if not path or not os.path.isfile(path):
        return
    try:
        os.remove(path)
    except OSError:
        log.warning("Не удалось удалить screenshot %s", path)
