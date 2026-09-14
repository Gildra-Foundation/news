from __future__ import annotations

from gildranews.presentation.telegram.bot import run_bot


async def main() -> None:
    """Start the configured Telegram application."""
    await run_bot()
