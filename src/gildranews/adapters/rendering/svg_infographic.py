from __future__ import annotations

import html
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path

from gildranews.domain.models import InfographicSpec


def _lines(value: str, width: int, limit: int) -> list[str]:
    lines = textwrap.wrap(value.strip(), width=width, break_long_words=False) or [""]
    return lines[:limit]


def render_svg(spec: InfographicSpec) -> str:
    if not 2 <= len(spec.facts) <= 4:
        raise ValueError("Инфографика должна содержать от 2 до 4 фактов")

    title_lines = _lines(spec.title, 25, 3)
    title = "".join(
        f'<tspan x="90" dy="{0 if index == 0 else 70}">{html.escape(line)}</tspan>'
        for index, line in enumerate(title_lines)
    )
    cards: list[str] = []
    card_width = 430
    card_height = 220
    for index, fact in enumerate(spec.facts):
        column = index % 2
        row = index // 2
        x = 90 + column * 470
        y = 520 + row * 245
        label_lines = _lines(fact.label, 29, 2)
        labels = "".join(
            f'<tspan x="{x + 32}" dy="{0 if line_index == 0 else 30}">{html.escape(line)}</tspan>'
            for line_index, line in enumerate(label_lines)
        )
        cards.append(
            f'<g><rect x="{x}" y="{y}" width="{card_width}" height="{card_height}" '
            'rx="28" fill="#111827" stroke="#334155" stroke-width="2"/>'
            f'<text x="{x + 32}" y="{y + 86}" fill="#67e8f9" font-size="54" '
            f'font-weight="700" font-family="DejaVu Sans, sans-serif">{html.escape(fact.value)}</text>'
            f'<text x="{x + 32}" y="{y + 142}" fill="#cbd5e1" font-size="25" '
            f'font-family="DejaVu Sans, sans-serif">{labels}</text></g>'
        )
    source = html.escape(spec.source.strip())
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1080" role="img" '
        f'aria-label="{html.escape(spec.title, quote=True)}">'
        '<defs><linearGradient id="panel" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#0f172a"/><stop offset="1" stop-color="#020617"/>'
        '</linearGradient></defs>'
        '<rect x="25" y="25" width="1030" height="1030" rx="48" fill="url(#panel)" '
        'stroke="#22d3ee" stroke-width="3"/>'
        f'<text x="90" y="115" fill="#67e8f9" font-size="23" letter-spacing="4" '
        f'font-weight="700" font-family="DejaVu Sans, sans-serif">{html.escape(spec.kicker.upper())}</text>'
        f'<text x="90" y="215" fill="#f8fafc" font-size="60" font-weight="700" '
        f'font-family="DejaVu Sans, sans-serif">{title}</text>'
        + "".join(cards)
        + f'<text x="90" y="1010" fill="#64748b" font-size="20" '
        f'font-family="DejaVu Sans, sans-serif">{source}</text></svg>'
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
