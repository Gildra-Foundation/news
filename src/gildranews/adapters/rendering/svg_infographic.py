from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

from gildranews.domain.models import InfographicSpec


def _lines(value: str, width: int, limit: int) -> list[str]:
    lines = textwrap.wrap(value.strip(), width=width, break_long_words=False) or [""]
    return lines[:limit]


def _localized_value(value: str) -> str:
    cleaned = value.strip()
    level = re.fullmatch(r"level\s+(\d+)", cleaned, flags=re.IGNORECASE)
    return f"{level.group(1)}-й уровень" if level else cleaned


def _defs() -> str:
    return """<defs>
  <linearGradient id="wood" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#6b3f21"/><stop offset="0.35" stop-color="#5f371d"/>
    <stop offset="0.65" stop-color="#472712"/><stop offset="1" stop-color="#2e160b"/>
  </linearGradient>
  <linearGradient id="parchment" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#f7e8bf"/><stop offset="1" stop-color="#ead6a7"/>
  </linearGradient>
  <linearGradient id="goldEdge" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#efc96f"/><stop offset="0.5" stop-color="#d9ab49"/>
    <stop offset="1" stop-color="#a67c2e"/>
  </linearGradient>
  <linearGradient id="bevelTop" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#ffe4aa" stop-opacity="0.55"/>
    <stop offset="0.5" stop-color="#ffe4aa" stop-opacity="0.10"/>
    <stop offset="1" stop-color="#000000" stop-opacity="0.28"/>
  </linearGradient>
  <radialGradient id="vignette" cx="0.5" cy="0.5" r="0.75">
    <stop offset="0.70" stop-color="#30251c" stop-opacity="0"/>
    <stop offset="1" stop-color="#241a12" stop-opacity="0.25"/>
  </radialGradient>
  <filter id="grain" x="0" y="0" width="100%" height="100%">
    <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" result="noise"/>
    <feColorMatrix in="noise" type="matrix"
      values="0 0 0 0 0.45  0 0 0 0 0.36  0 0 0 0 0.26  0 0 0 0.055 0"/>
    <feComposite operator="in" in2="SourceGraphic"/>
  </filter>
  <filter id="soft" x="-8%" y="-8%" width="116%" height="116%">
    <feGaussianBlur stdDeviation="3.2"/>
  </filter>
</defs>"""


def _frame() -> str:
    return """<rect x="14" y="19" width="772" height="770" rx="18"
  fill="#1a120b" opacity="0.30" filter="url(#soft)"/>
<rect x="18" y="18" width="764" height="764" rx="13" fill="url(#parchment)"/>
<rect x="18" y="18" width="764" height="764" rx="13" fill="url(#vignette)"/>
<rect x="18" y="18" width="764" height="764" rx="13" filter="url(#grain)" fill="#fff"/>
<rect x="10" y="10" width="780" height="780" rx="18" fill="none"
  stroke="#1c0d06" stroke-width="3"/>
<rect x="13" y="13" width="774" height="774" rx="16" fill="none"
  stroke="url(#wood)" stroke-width="14"/>
<rect x="21" y="21" width="758" height="758" rx="11" fill="none"
  stroke="url(#goldEdge)" stroke-width="2"/>"""


def _title_block(title: str) -> str:
    title_lines = _lines(title, 34, 2)
    height = 58 if len(title_lines) == 1 else 88
    first_y = 77 if len(title_lines) == 1 else 65
    tail_top = 47
    tail_bottom = 40 + height - 7
    tail_middle = (tail_top + tail_bottom) / 2
    lines = "".join(
        f'<tspan x="400" dy="{0 if index == 0 else 34}">{html.escape(line)}</tspan>'
        for index, line in enumerate(title_lines)
    )
    return (
        f'<polygon points="46,{tail_top} 25,{tail_top} 36,{tail_middle:g} '
        f'25,{tail_bottom} 46,{tail_bottom}" fill="#4a0a0f"/>'
        f'<polygon points="754,{tail_top} 775,{tail_top} 764,{tail_middle:g} '
        f'775,{tail_bottom} 754,{tail_bottom}" fill="#4a0a0f"/>'
        f'<rect x="40" y="40" width="720" height="{height}" rx="7" fill="#5d0d13"/>'
        f'<rect x="40" y="40" width="720" height="{height}" rx="7" '
        'fill="url(#bevelTop)" opacity="0.55"/>'
        f'<rect x="42" y="42" width="716" height="{height - 4}" rx="6" fill="none" '
        'stroke="url(#goldEdge)" stroke-width="1.5"/>'
        f'<text x="400" y="{first_y}" text-anchor="middle" '
        'font-family="DejaVu Serif, Georgia, serif" font-size="27" font-weight="700" '
        'fill="#f9ead0" stroke="#3a080c" stroke-width="2.5" paint-order="stroke">'
        f"{lines}</text>"
    )


def render_svg(spec: InfographicSpec) -> str:
    if not 2 <= len(spec.facts) <= 4:
        raise ValueError("Инфографика должна содержать от 2 до 4 фактов")

    cards: list[str] = []
    count = len(spec.facts)
    card_width = 300
    card_height = 180
    if count == 2:
        positions = ((84, 315), (416, 315))
    elif count == 3:
        positions = ((80, 260), (420, 260), (250, 478))
    else:
        positions = ((80, 260), (420, 260), (80, 478), (420, 478))
    for index, fact in enumerate(spec.facts):
        x, card_y = positions[index]
        label_lines = _lines(fact.label, max(14, card_width // 13), 3)
        labels = "".join(
            f'<tspan x="{x + card_width / 2:.0f}" dy="{0 if line_index == 0 else 22}">'
            f"{html.escape(line)}</tspan>"
            for line_index, line in enumerate(label_lines)
        )
        value = _localized_value(fact.value)
        value_size = 37 if len(value) <= 8 else 29 if len(value) <= 14 else 23
        cards.append(
            f'<g><rect x="{x:.0f}" y="{card_y}" width="{card_width}" height="{card_height}" '
            'rx="16" fill="#5d0d13"/>'
            f'<rect x="{x:.0f}" y="{card_y}" width="{card_width}" height="{card_height}" '
            'rx="16" fill="url(#bevelTop)" opacity="0.55"/>'
            f'<rect x="{x + 1:.0f}" y="{card_y + 1}" width="{card_width - 2}" '
            f'height="{card_height - 2}" rx="15" fill="none" stroke="url(#goldEdge)" '
            'stroke-width="1.7"/>'
            f'<text x="{x + card_width / 2:.0f}" y="{card_y + 73}" text-anchor="middle" '
            f'fill="#f7e8bf" font-size="{value_size}" font-weight="700" '
            f'font-family="DejaVu Serif, Georgia, serif">{html.escape(value)}</text>'
            f'<text x="{x + card_width / 2:.0f}" y="{card_y + 117}" text-anchor="middle" '
            'fill="#d9ab49" font-size="15" font-family="DejaVu Sans, sans-serif">'
            f"{labels}</text></g>"
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800" role="img" '
        f'aria-label="{html.escape(spec.title, quote=True)}">'
        + _defs()
        + _frame()
        + _title_block(spec.title)
        + '<line x1="72" y1="196" x2="270" y2="196" stroke="#735e49" opacity="0.55"/>'
        + f'<text x="400" y="202" text-anchor="middle" fill="#8d171d" font-size="16" '
        f'letter-spacing="2.5" font-weight="700" font-family="DejaVu Sans, sans-serif">'
        f"{html.escape(spec.kicker.upper())}</text>"
        + '<line x1="530" y1="196" x2="728" y2="196" stroke="#735e49" opacity="0.55"/>'
        + "".join(cards)
        + ("<path d=\"M300 590 H375 L400 615 L425 590 H500\"" if count == 2
           else "<path d=\"M300 705 H375 L400 730 L425 705 H500\"")
        + ' fill="none" '
        'stroke="url(#goldEdge)" stroke-width="2" opacity="0.8"/>'
        + ("<circle cx=\"400\" cy=\"615\"" if count == 2
           else "<circle cx=\"400\" cy=\"730\"")
        + ' r="5" fill="#8d171d" stroke="url(#goldEdge)"/>'
        + "</svg>"
    )


def render_png(spec: InfographicSpec, destination: str | Path) -> bool:
    converter = shutil.which("rsvg-convert")
    if converter is None:
        return False
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".svg", encoding="utf-8") as source:
        source.write(render_svg(spec))
        source.flush()
        try:
            subprocess.run(
                [converter, "--width", "1080", "--height", "1080", "--output", str(destination), source.name],
                check=True,
                timeout=20,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return False
    return destination.is_file() and destination.stat().st_size > 0
