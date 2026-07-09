"""
Entry point for the PokeManager web dashboard.

Run alongside the Discord bot:
    Terminal 1: python bot.py
    Terminal 2: python web_server.py

Requires the same .env that bot.py uses.
DISCORD_TOKEN is technically unused by the web server but is read by config.py
on import; if it is absent from .env the placeholder below keeps imports clean.
"""

import os
import sys

# Set before any import that triggers config.py loading.
os.environ.setdefault("DISCORD_TOKEN", "web-dashboard-placeholder")

# Ensure the pokemaz root directory is on the path so that `import excel_db`
# works regardless of where this script is invoked from.
_root = os.path.dirname(os.path.abspath(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

import uvicorn
from web.app import app  # noqa: F401 — imported so uvicorn can resolve "web.app:app"

if __name__ == "__main__":
    uvicorn.run(
        "web.app:app",
        host=os.getenv("WEB_HOST", "0.0.0.0"),
        port=int(os.getenv("WEB_PORT", "8000")),
        reload=os.getenv("WEB_RELOAD", "false").lower() == "true",
        reload_dirs=[os.path.join(_root, "web")],
        reload_excludes=["*.xlsx", "*.json", "logs/*", "backups/*", "temp_images/*", "debug/*"],
    )
