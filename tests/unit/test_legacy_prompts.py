from gildranews.adapters.ai import gemini


def test_legacy_prompts_use_gildranews_warcraft_editorial_context() -> None:
    prompts = (
        gemini.REWRITE_PROMPT,
        gemini.TRANSLATE_PROMPT,
        gemini.GITHUB_PROMPT,
        gemini.DIGEST_PROMPT,
    )

    assert all("GildraNews" in prompt for prompt in prompts)
    assert all("RuNeuroNews" not in prompt for prompt in prompts)
    assert all("World of Warcraft" in prompt for prompt in prompts)
