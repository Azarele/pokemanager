# PokeManager — Pokémon TCG Inventory & Reselling Platform

A Discord bot that tracks your Pokémon TCG card inventory in an Excel file, scrapes live prices from PriceCharting, and automates eBay UK listings via a headless browser. Add a card with a URL and purchase price, get a live margin estimate instantly, then cross-list it on eBay with a single slash command and a photo upload.

## Prerequisites

- **Python 3.10+**
- **A Discord bot token** — [create one here](https://discord.com/developers/applications) (enable the *Message Content* intent)
- **Optional:** A [Gemini API key](https://ai.google.dev/) for AI-generated listing titles and descriptions

## Setup

```bash
git clone <repo-url>
cd pokemaz
python setup.py
```

`setup.py` will:
- Copy `.env.example` → `.env`
- Install all Python dependencies
- Download the Chromium browser used by Playwright
- Create `browser_state/`, `temp_images/`, and `debug/` directories

Then open `.env` and fill in at minimum:

```
DISCORD_TOKEN=your-bot-token-here
```

Start the bot:

```bash
python bot.py
```

## Configuration (`.env`)

| Variable | Default | Description |
|---|---|---|
| `DISCORD_TOKEN` | *(required)* | Your Discord bot token |
| `EXCEL_FILE` | `inventory.xlsx` | Path to the inventory spreadsheet |
| `PRICE_UPDATE_CHANNEL_ID` | `0` (disabled) | Channel ID for scheduled price-update embeds |
| `UPDATE_INTERVAL_HOURS` | `12` | Hours between background price refreshes |
| `GEMINI_API_KEY` | *(optional)* | Enables AI listing-title generation |
| `TEST_GUILD_ID` | `0` (global) | Server ID for instant slash-command sync during development |

## Slash commands

| Command | Parameters | Description |
|---|---|---|
| `/add` | `pc_url`, `condition`, `purchase_price` | Add a card via PriceCharting URL; scrapes name and live price automatically |
| `/sell` | `item_id`, `sell_price` | Record a sale and calculate profit |
| `/list` | `item_id`, `image1`–`image5`, `price_override` | Automate an eBay UK listing with uploaded photos |
| `/delist` | `item_id` | End an active eBay listing and clear it from inventory |
| `/update` | — | Manually refresh live prices for all inventory items |
| `/stock` | — | Table view of all cards currently in inventory |
| `/remove` | `item_id`, `confirm` | Remove an accidentally-added item from inventory (two-step confirmation; cannot remove Sold items) |
| `/summary` | — | Aggregate stats: profit/loss, cost in stock, potential market value |

## eBay automation

The `/list` command uses the eBay Developer API to publish listings directly — no browser, no cookies, no Playwright.

### First-time setup

1. Create a free account at [developer.ebay.com](https://developer.ebay.com) and create a production application.
2. Copy your App ID, Dev ID, and Cert ID into `.env` as `EBAY_APP_ID`, `EBAY_DEV_ID`, `EBAY_CERT_ID`.
3. Add `https://localhost` as a redirect URI in your eBay developer app settings (under "User Tokens").
4. Run `python generate_ebay_token.py` and follow the prompts to obtain your OAuth refresh token.
5. Add the refresh token to `.env` as `EBAY_REFRESH_TOKEN`. It is valid for 18 months.
6. Find your business policy IDs and add them to `.env`:
   ```
   python -c "import asyncio, lister_ebay_api; asyncio.run(lister_ebay_api.print_policies())"
   ```
   If you have no business policies yet, create them at ebay.co.uk → Account → Business policies.

### After setup

`/list item_id:<X> image1:<photo>` — the bot fetches the card from inventory, generates an AI title and description, uploads your photos to eBay's image hosting service, and publishes the listing at a fixed price. Returns the live `ebay.co.uk` URL.

## Notes on graded prices

PriceCharting's public product pages expose a single generic *graded* price (not per-PSA-grade). Conditions `PSA 10` through `PSA 1` all resolve to the same `graded_price` container. Per-grade pricing requires a PriceCharting paid API subscription.
