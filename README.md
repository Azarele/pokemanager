# 🃏 PokeManager

<div align="center">

[![Live](https://img.shields.io/badge/Live-pokemanager.app-brightgreen?style=for-the-badge&logo=railway&logoColor=white)](https://pokemanager.app)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Supabase](https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?style=for-the-badge&logo=supabase&logoColor=white)](https://supabase.com/)
[![Railway](https://img.shields.io/badge/Deployed_on-Railway-0B0D0E?style=for-the-badge&logo=railway&logoColor=white)](https://railway.app/)
[![Stripe](https://img.shields.io/badge/Payments-Stripe-635BFF?style=for-the-badge&logo=stripe&logoColor=white)](https://stripe.com/)

**The all-in-one Pokémon TCG reselling platform for serious collectors and traders.**

PokeManager brings your entire operation into one place — track inventory, list on eBay, scan cards with AI, analyse profits, and manage your team, all from a single dashboard. Built for UK resellers, designed to scale from solo flippers to full storefronts.

</div>

---

```
╔══════════════════════════════════════════════════════════════════════╗
║  📦 Inventory   🏪 eBay Listings   🤖 AI Scan   📊 Analytics        ║
║  👥 Staff Roles  🔔 Push Alerts    💳 Billing   🔒 2FA Security      ║
╚══════════════════════════════════════════════════════════════════════╝
```

---

## 🌟 Features

### 📦 Inventory Management
- **Manual add** — enter card details, purchase price, and condition directly
- **AI card scanning** — photograph a card and let Gemini Vision identify it instantly
- **CSV import** — bulk-import existing stock from a spreadsheet
- **Bundle add** — add multiple cards from a single purchase in one flow
- **Trade-ins** — record trade-in acquisitions with cost tracking
- **Quantity tracking** — manage multiple copies of the same card

### 🏪 eBay Integration
- **One-click listing** — publish to eBay UK with AI-generated titles and descriptions
- **Bulk listing** — list dozens of cards at once
- **Repricing** — adjust live eBay prices from within PokeManager
- **Auto-sync sales** — sold items are automatically marked and profits recorded
- **Quantity sync** — keeps eBay stock counts in sync with your inventory
- **Business policy support** — hooks into your existing eBay fulfilment, payment, and return policies

### 🤖 AI-Powered
- **Gemini Vision card scanning** — point your camera at a card and get the name, set, and condition
- **AI listing descriptions** — automatically generated, SEO-optimised eBay descriptions
- **Managed AI** (Champion tier) — AI descriptions handled entirely by PokeManager

### 📊 Analytics & Reporting
- **Profit & ROI tracking** — per-card and overall margin visibility
- **Sales velocity** — understand which cards move fastest
- **Forecasting** — projected revenue based on current stock
- **HMRC-ready export** — CSV export formatted for UK self-assessment
- **Price history charts** — track market value over time

### 👥 Multi-User Staff System
- **Role-based permissions** — Admin, Manager, and Staff roles
- **Invite by email** — onboard your team with a single link
- **Audit log** — every action is recorded with timestamp and user

### 🔔 Push Notifications
- **Web push** — receive alerts in your browser or on mobile even when the tab is closed
- **Sale alerts** — instant notification when an eBay sale comes in
- **Configurable** — choose which events trigger a notification

### 💳 Subscription Billing
| Plan | Price | Highlights |
|------|-------|------------|
| **Free** | £0/mo | Up to 50 items, basic inventory |
| **Gym Leader** | £7.99/mo | Unlimited items, eBay listing, AI descriptions, accounting export |
| **Champion** | £14.99/mo | Everything above + managed AI, priority support |

> All paid plans include a **7-day free trial** — no card required to start.

### 🔒 Security Hardened
- TOTP-based **two-factor authentication** (authenticator app)
- **AES field encryption** for sensitive data at rest
- **Row-Level Security** (RLS) enforced in Supabase — users only see their own data
- **JWT authentication** with short-lived access tokens and rolling refresh tokens
- **Rate limiting** on all auth endpoints (via SlowAPI)
- **HMAC-signed** eBay webhook payloads
- Input sanitisation and parameterised queries throughout

### 📱 PWA Ready
- Installable on iOS and Android from the browser
- Works offline for read-only inventory views
- Mobile-optimised responsive UI

---

## 📸 Screenshots

> Screenshots coming soon — visit [pokemanager.app](https://pokemanager.app) to see the live app.

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11, FastAPI, Uvicorn |
| **Database** | Supabase (PostgreSQL), Row-Level Security |
| **Auth** | JWT (python-jose), bcrypt, PyOTP (2FA) |
| **AI** | Google Gemini Vision (google-genai SDK) |
| **eBay** | eBay Inventory & Trading APIs |
| **Payments** | Stripe Subscriptions + Webhooks |
| **Email** | Resend |
| **Push** | Web Push (VAPID) |
| **Encryption** | cryptography (AES-256) |
| **Rate limiting** | SlowAPI |
| **Frontend** | Jinja2, Vanilla JS, CSS custom properties |
| **Deployment** | Railway (Nixpacks), Docker-compatible |
| **Bot** | discord.py (optional companion bot) |

---

## 🚀 Quick Start

**Just want to use PokeManager?**

1. Go to [pokemanager.app](https://pokemanager.app)
2. Click **Sign Up** — enter your email and create a password
3. Start a **7-day free trial** of Gym Leader or Champion, or use the Free tier
4. Add your first card and connect your eBay account

No installation needed. Works in any browser.

---

## 💻 Development Setup

### Prerequisites

- Python 3.11+
- A [Supabase](https://supabase.com/) project (free tier works)
- A [Stripe](https://stripe.com/) account (test mode is fine)
- A [Gemini API key](https://ai.google.dev/) (optional, for AI features)
- An [eBay developer account](https://developer.ebay.com/) (optional, for listing)

### 1. Clone the repo

```bash
git clone https://github.com/your-username/pokemaz.git
cd pokemaz
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in at minimum:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_KEY=your-service-role-key
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
```

See the [Environment Variables](#-environment-variables) section for the full reference.

### 4. Apply database migrations

```bash
# Using Supabase CLI
supabase db push

# Or apply migrations manually via the Supabase SQL editor
# Files are in supabase/migrations/
```

### 5. Start the web server

```bash
python web_server.py
```

The dashboard will be available at `http://localhost:8000`.

### 6. (Optional) Start the Discord bot

```bash
python bot.py
```

Requires `DISCORD_TOKEN` in `.env`.

### 7. (Optional) Set up eBay API

```bash
python generate_ebay_token.py
```

Follow the prompts to obtain your OAuth refresh token, then paste it into `.env` as `EBAY_REFRESH_TOKEN`.

---

## 🔒 Security

PokeManager is built with security-first principles throughout:

| Control | Implementation |
|---------|---------------|
| **Authentication** | JWT access tokens (60 min) + rotating refresh tokens (30 days) |
| **Two-Factor Auth** | TOTP via PyOTP — compatible with Google Authenticator, Authy, etc. |
| **Password hashing** | bcrypt via passlib |
| **Data encryption** | AES-256 field encryption for sensitive values at rest |
| **Database security** | Supabase Row-Level Security — all tables have RLS policies |
| **Rate limiting** | SlowAPI on all `/auth/*` and `/api/*` endpoints |
| **Input validation** | Pydantic models + custom sanitisation layer |
| **Webhook validation** | HMAC signature verification on Stripe and eBay webhooks |
| **CORS** | Restricted to known origins in production |
| **Secrets** | All secrets loaded from environment — never hardcoded |

---

## 📊 Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                     Railway                         │
│  ┌─────────────────────────────────────────────┐   │
│  │           FastAPI (web/app.py)               │   │
│  │                                              │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  │   │
│  │  │  Routes  │  │   Auth   │  │ Middleware│  │   │
│  │  │ inventory│  │JWT + 2FA │  │rate limit│  │   │
│  │  │ ebay_sync│  │ bcrypt   │  │CORS / CSP│  │   │
│  │  │ analytics│  │  TOTP    │  │sanitise  │  │   │
│  │  │ billing  │  └──────────┘  └──────────┘  │   │
│  │  │ staff    │                               │   │
│  │  │ scan     │  ┌──────────┐  ┌──────────┐  │   │
│  │  │ exports  │  │Background│  │WebSocket │  │   │
│  │  └──────────┘  │sync tasks│  │real-time │  │   │
│  │                └──────────┘  └──────────┘  │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
          │               │              │
    ┌─────▼─────┐  ┌──────▼──────┐  ┌──▼──────┐
    │ Supabase  │  │  eBay APIs  │  │ Gemini  │
    │PostgreSQL │  │  Inventory  │  │ Vision  │
    │    RLS    │  │  Trading    │  │   AI    │
    └───────────┘  └─────────────┘  └─────────┘
          │               │
    ┌─────▼─────┐  ┌──────▼──────┐
    │  Stripe   │  │   Resend    │
    │  billing  │  │   email     │
    └───────────┘  └─────────────┘
```

**Request flow:** Browser → Railway (FastAPI) → Supabase (RLS-enforced PostgreSQL). Background tasks handle eBay sale polling and price sync on a scheduled basis. WebSockets push real-time inventory updates to connected dashboards.

---

## 🗄️ Database Schema

Key tables in Supabase (PostgreSQL):

| Table | Purpose |
|-------|---------|
| `user_profiles` | User accounts, plan, Stripe IDs, 2FA secret |
| `inventory_items` | Cards in stock — name, condition, cost, quantity, eBay listing ID |
| `sales` | Completed sales with profit, fees, and sale price recorded |
| `staff_members` | Staff→owner relationships and role assignments |
| `push_subscriptions` | VAPID push endpoints per user/device |
| `price_history` | Historical market price snapshots per card |
| `watchlist` | Cards the user wants to monitor but hasn't bought yet |
| `audit_log` | Immutable action log — user, action, timestamp |

All tables have **Row-Level Security** policies ensuring users can only read and write their own data. Service-role access (used by background tasks) bypasses RLS where necessary and is scoped carefully.

---

## 🔧 Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Your Supabase project URL (`https://xxx.supabase.co`) |
| `SUPABASE_ANON_KEY` | Supabase anon/public API key |
| `SUPABASE_SERVICE_KEY` | Supabase service role key (server-side only) |
| `JWT_SECRET` | Random hex string for signing JWTs — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |

### Auth

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh token lifetime |
| `ENCRYPTION_KEY` | — | AES encryption key for sensitive fields |

### eBay API

| Variable | Description |
|----------|-------------|
| `EBAY_APP_ID` | eBay Developer App ID |
| `EBAY_DEV_ID` | eBay Developer Dev ID |
| `EBAY_CERT_ID` | eBay Developer Cert ID |
| `EBAY_REFRESH_TOKEN` | OAuth refresh token (run `generate_ebay_token.py`) |
| `EBAY_FULFILLMENT_POLICY_ID` | eBay business policy — fulfilment |
| `EBAY_PAYMENT_POLICY_ID` | eBay business policy — payment |
| `EBAY_RETURN_POLICY_ID` | eBay business policy — returns |
| `EBAY_CATEGORY_ID` | eBay category ID (default `183454` — Pokémon TCG) |
| `EBAY_FEE_RATE` | `0.1235` | eBay fee rate for profit calculation (12.35%) |
| `EBAY_MIN_PRICE_GBP` | `0.99` | Minimum listing price |
| `AUTO_SYNC_EBAY_PRICES` | `true` | Push price changes to live eBay listings |

### AI

| Variable | Description |
|----------|-------------|
| `GEMINI_API_KEY` | Google Gemini API key — enables card scanning and AI descriptions |

### Stripe

| Variable | Description |
|----------|-------------|
| `STRIPE_SECRET_KEY` | Stripe secret key (`sk_live_…` or `sk_test_…`) |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret (`whsec_…`) |
| `STRIPE_PRICE_GYM_LEADER` | Stripe Price ID for the Gym Leader plan |
| `STRIPE_PRICE_CHAMPION` | Stripe Price ID for the Champion plan |

### Email

| Variable | Description |
|----------|-------------|
| `RESEND_API_KEY` | Resend API key for transactional email |

### Web Push

| Variable | Description |
|----------|-------------|
| `VAPID_PUBLIC_KEY` | VAPID public key for push notifications |
| `VAPID_PRIVATE_KEY` | VAPID private key for push notifications |

### Web Server

| Variable | Default | Description |
|----------|---------|-------------|
| `SITE_URL` | `http://localhost:8000` | Public URL — used for OAuth callbacks |
| `APP_URL` | `http://localhost:8000` | App URL — used for Stripe billing redirects |
| `WEB_HOST` | `0.0.0.0` | Bind address |
| `WEB_PORT` | `8000` | Port |
| `WEB_RELOAD` | `false` | Enable hot-reload (dev only) |

### Discord Bot (optional)

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Discord bot token |
| `BOT_OWNER_USER_ID` | Owner's Supabase user ID for bot→DB sync |
| `TEST_GUILD_ID` | Guild ID for instant slash-command sync during development |
| `PRICE_UPDATE_CHANNEL_ID` | Channel ID for scheduled price update embeds |
| `UPDATE_INTERVAL_HOURS` | `12` | Hours between background price refreshes |

### Instagram (optional)

| Variable | Description |
|----------|-------------|
| `INSTAGRAM_ACCESS_TOKEN` | Instagram Business API access token |
| `INSTAGRAM_BUSINESS_ACCOUNT_ID` | Instagram Business Account ID |
| `INSTAGRAM_APP_ID` | Meta App ID |
| `INSTAGRAM_APP_SECRET` | Meta App secret |

### Misc

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKUP_DIR` | `backups` | Local backup directory |
| `BACKUP_RETENTION_COUNT` | `50` | Number of backups to keep |
| `AUDIT_LOG_DIR` | `logs` | Audit log directory |
| `KOREAN_PRICE_MULTIPLIER` | `0.7` | Price multiplier for Korean prints vs Japanese |

---

## 📦 Deployment (Railway)

PokeManager is deployed on [Railway](https://railway.app/) using Nixpacks (no Dockerfile needed).

### Deploy your own instance

1. **Fork this repo** and push to your GitHub account.

2. **Create a new Railway project** and link it to your fork.

3. **Add environment variables** in Railway's dashboard (Variables tab) — use the table above as a reference.

4. **Set the start command** (already in `railway.toml`):
   ```
   uvicorn web.app:app --host 0.0.0.0 --port $PORT
   ```

5. **Add a custom domain** in Railway → Settings → Networking → Custom Domain.

6. **Set up Supabase**: create a project at supabase.com, apply migrations from `supabase/migrations/`, and paste the keys into Railway.

7. **Set up Stripe webhooks**: in the Stripe Dashboard, add a webhook endpoint pointing to `https://your-domain.com/api/billing/webhook` with these events:
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_succeeded`

Railway automatically redeploys on every push to your linked branch.

---

## 🤝 Contributing

Contributions are welcome! Here's how to get started:

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/your-feature`
3. Make your changes and add tests where appropriate
4. Run the linter: `ruff check .`
5. Commit with a clear message: `git commit -m "feat: add your feature"`
6. Open a pull request

Please open an issue first for significant changes so we can discuss the approach.

---

## 📄 License

This project is proprietary. All rights reserved. Contact the author for licensing enquiries.

---

## 👤 Author

**Csanad Kope**

- 🌐 App: [pokemanager.app](https://pokemanager.app)
- 🛍️ eBay Store: [azaramvault](https://www.ebay.co.uk/str/azaramvault)
- 📧 Email: proname888@gmail.com

---

<div align="center">

Built with ❤️ for the Pokémon TCG community · [pokemanager.app](https://pokemanager.app)

</div>
