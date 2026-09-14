"""Interactive, non-echoing setup for optional scraping API credentials."""

from __future__ import annotations

import argparse
import getpass
import os
import stat
from collections.abc import Mapping, Sequence
from pathlib import Path

from dotenv import set_key

KEY_PROMPTS = (
    ("SCRAPE_DO_TOKEN", "Scrape.do token"),
    ("GETXAPI_KEY", "GetXAPI key"),
    ("REDDITAPIS_KEY", "RedditAPIs key"),
)
PAID_FLAGS = ("SCRAPE_DO_ENABLED", "GETXAPI_ENABLED", "REDDITAPIS_ENABLED")


def _check_secret(value: str) -> None:
    if any(character in value for character in ("\n", "\r", "\0")):
        raise ValueError("API keys must fit on one line")


def _prepare_env_file(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"refusing to store secrets through a symlink: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"environment path is not a regular file: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"environment directory does not exist: {path.parent}")

    flags = os.O_CREAT | os.O_APPEND
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    os.close(descriptor)
    path.chmod(0o600)


def update_env_file(path: Path, values: Mapping[str, str]) -> None:
    """Update selected dotenv values without exposing them in process arguments."""

    path = Path(path)
    _prepare_env_file(path)
    for key, value in values.items():
        _check_secret(value)
        set_key(path, key, value, quote_mode="always")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely store scraping API keys in a local .env file."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="dotenv file to update (default: ./.env)",
    )
    paid = parser.add_mutually_exclusive_group()
    paid.add_argument(
        "--enable-paid",
        action="store_true",
        help="explicitly enable Scrape.do, GetXAPI and RedditAPIs paid routes",
    )
    paid.add_argument(
        "--disable-paid",
        action="store_true",
        help="disable all paid routes without deleting their keys",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    updates: dict[str, str] = {}

    for key, label in KEY_PROMPTS:
        value = getpass.getpass(f"{label} (Enter = keep current): ").strip()
        if value:
            updates[key] = value

    if args.enable_paid or args.disable_paid:
        enabled = "true" if args.enable_paid else "false"
        updates.update({name: enabled for name in PAID_FLAGS})

    if not updates:
        print("No changes.")
        return 0

    update_env_file(args.env_file, updates)
    print(f"Saved {len(updates)} setting(s) to {args.env_file} (mode 0600).")
    if not args.enable_paid and not args.disable_paid:
        print("Paid routes were not changed; use --enable-paid to activate them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
