from __future__ import annotations

import xml.etree.ElementTree as ET

from gildranews.adapters.rendering.svg_infographic import render_svg
from gildranews.domain.models import InfographicFact, InfographicSpec


def test_svg_infographic_is_self_contained_and_escapes_content() -> None:
    spec = InfographicSpec(
        kicker="ДАННЫЕ",
        title="OpenAI < Anthropic & другие",
        facts=(
            InfographicFact(value="67.8%", label="SWE-bench"),
            InfographicFact(value="$20", label="за миллион токенов"),
        ),
        source="example.com/report",
    )

    svg = render_svg(spec)
    root = ET.fromstring(svg)

    assert root.tag.endswith("svg")
    assert root.attrib["viewBox"] == "0 0 800 800"
    assert "OpenAI &lt; Anthropic &amp; другие" in svg
    assert "example.com/report" not in svg
    assert "url(#parchment)" in svg
    assert "url(#wood)" in svg
    assert "#5d0d13" in svg
    assert "#22d3ee" not in svg
    assert "manacost" not in svg.lower()
    hrefs = [value for element in root.iter() for key, value in element.attrib.items() if key.endswith("href")]
    assert all(value.startswith(("#", "data:")) for value in hrefs)
    assert "<image" not in svg


def test_svg_infographic_localizes_level_values() -> None:
    spec = InfographicSpec(
        kicker="WOW: FOREVER",
        title="Ограничения уровней на тесте",
        facts=(
            InfographicFact(value="level 20", label="Стартовый предел бета-теста"),
            InfographicFact(value="level 30", label="Предел позднее"),
            InfographicFact(value="level 1", label="Старт на запуске"),
        ),
    )

    svg = render_svg(spec)

    assert "level" not in svg.lower()
    assert "20-й уровень" in svg
    assert "30-й уровень" in svg
    assert "1-й уровень" in svg
