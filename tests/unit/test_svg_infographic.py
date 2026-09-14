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
    assert root.attrib["viewBox"] == "0 0 1080 1080"
    assert "OpenAI &lt; Anthropic &amp; другие" in svg
    hrefs = [value for element in root.iter() for key, value in element.attrib.items() if key.endswith("href")]
    assert all(value.startswith(("#", "data:")) for value in hrefs)
    assert "<image" not in svg
