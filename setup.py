#!/usr/bin/env python3
"""
Bootstrap script — run once from a fresh clone before starting the bot.

What it does
------------
1. Checks Python is 3.10+.
2. Copies .env.example → .env if .env is missing.
3. Installs Python dependencies from requirements.txt.
4. Downloads the Chromium browser used by Playwright.
5. Creates browser_state/, temp_images/, and debug/ directories.

Safe to run multiple times (idempotent).
"""

import shutil
import subprocess
import sys
from pathlib import Path

_MIN_PYTHON = (3, 10)
_ROOT = Path(__file__).resolve().parent


def _check_python() -> None:
    if sys.version_info < _MIN_PYTHON:
        sys.exit(
            f"Python {_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+ is required. "
            f"You are running {sys.version.split()[0]}."
        )
    print(f"  Python {sys.version.split()[0]}")


def _copy_env() -> None:
    env_path = _ROOT / ".env"
    example  = _ROOT / ".env.example"
    if env_path.exists():
        print("  .env already exists — skipping copy.")
    else:
        shutil.copy(example, env_path)
        print(
            "  Created .env from .env.example.\n"
            "  -> Open .env and set DISCORD_TOKEN (and optionally GEMINI_API_KEY)."
        )


def _pip_install() -> None:
    print("  Installing Python dependencies…")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(_ROOT / "requirements.txt")],
        stdout=subprocess.DEVNULL,
    )
    print("  Dependencies installed.")


def _playwright_install() -> None:
    print("  Downloading Chromium for Playwright…")
    subprocess.check_call(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("  Chromium ready.")


def _make_dirs() -> None:
    for name in ("browser_state", "temp_images", "debug"):
        (_ROOT / name).mkdir(exist_ok=True)
        print(f"  {name}/ ready.")


def main() -> None:
    print("\n=== Pokemaz setup ===\n")
    print("[1/5] Checking Python version…")
    _check_python()
    print("[2/5] Preparing .env…")
    _copy_env()
    print("[3/5] Installing dependencies…")
    _pip_install()
    print("[4/5] Installing Chromium…")
    _playwright_install()
    print("[5/5] Creating directories…")
    _make_dirs()
    print(
        "\nSetup complete!\n"
        "  Edit .env with your credentials, then start the bot:\n"
        "    python bot.py\n"
    )


if __name__ == "__main__":
    main()
