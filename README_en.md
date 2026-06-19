# Telegram Paid Community Bot

**Language / 语言:** [简体中文](README.md) · [English](README_en.md)

A full-stack Telegram paid community system. Users pick plans and pay via the bot; the system handles invite links, join approval, expiry kicks, and renewal reminders automatically.

## Features

- **Plan management** — Multiple plans, per-channel pricing, enable/disable anytime; group chat ID is immutable after creation
- **Five payment methods** — Telegram Stars, CryptoBot (USDT), Stripe, Alipay, WeChat Pay
- **Payment routing** — Route Alipay/WeChat to Epay or HuPiJiao; admin UI shows the right backend sections dynamically
- **Join protection** — Join-request mode; only active members are auto-approved; leaked links are useless without a subscription
- **Payment security** — Webhook signature verification, amount checks, idempotent fulfillment, no duplicate invite links on gateway retries
- **Payment resilience** — Expired pending orders cleaned up; late payments revived; Stripe failure/refund handling
- **Auto expiry** — Hourly kick on expiry (retries if kick fails); renewal reminders with `{days}` / `{expiry_date}`
- **Admin web panel** — Dashboard, plans/members/orders, **grant membership** (ID or `@username`), adjust expiry, bot & payment settings
- **Admin bot commands** — `/grant`, `/setexpiry`, `/broadcast`, etc. (`ADMIN_IDS` or admin usernames from the panel)
- **Join notifications** — DMs all admins when a member is auto-approved into a group
- **Docker deployment** — Nginx + HTTPS guide in [docs/CONFIG_GUIDE.md](docs/CONFIG_GUIDE.md) (Chinese)

## Quick start

```bash
git clone https://github.com/snakewyt/tg-paid-community-bot.git
cd tg-paid-community-bot
cp .env.example .env
# Edit .env: BOT_TOKEN, ADMIN_IDS
docker compose up -d
```

Minimal `.env`:

```env
BOT_TOKEN=123456:ABC-DEF1234gh
ADMIN_IDS=123456789
```

Create your first plan (message the bot on Telegram):

```
/addplan Monthly 30 -1001234567890 150
```

## Payment methods

| Method | Notes |
|--------|--------|
| Telegram Stars | Zero extra setup; set `STARS_ENABLED=true` |
| CryptoBot | USDT; requires API token |
| Stripe | Card payments (prices in **cents**); refund webhook supported |
| Alipay / WeChat | Routed to Epay or HuPiJiao |

Price `0` = channel disabled for that plan. Stripe: `499` = $4.99.

## Admin panel

Open `http://your-server:8000/admin/login`

| Item | Notes |
|------|--------|
| Default login | `admin` / `123456` (forced password change on first login) |
| Dashboard | Stats, plans, **grant member** (ID / `@username`), search/adjust/revoke, orders |
| Bot config | Token, admin usernames, welcome text, order timeout, expiry reminders (leave Token blank = unchanged) |
| Payment settings | Enable channels and keys (leave secrets blank = unchanged) |

Optional hardening (`.env`):

```env
# Restrict /admin to these IPs (comma-separated; empty = allow all)
ADMIN_PANEL_ALLOWED_IPS=your.public.ip
```

Use Nginx + HTTPS in production — see CONFIG_GUIDE section 8.

## Admin commands

| Command | Description |
|---------|-------------|
| `/addplan name days chat_id [prices...]` | Create a plan |
| `/editplan <id> <field> <value>` | Edit plan (**chat_id cannot change**) |
| `/grant <user_id or @username> <plan_id> <days>` | Grant membership (shows link in panel/reply if user can't be DM'd) |
| `/setexpiry <user_id or @username> <plan_id> <+N/-N/date>` | Adjust expiry (past date kicks immediately) |
| `/stats` / `/active` / `/broadcast` | Stats / active subs / broadcast (~25 msg/s, max 5000 users) |

> Admins: `ADMIN_IDS` in `.env` and/or admin usernames in the panel. Member ops accept numeric ID or `@username`.

## Upgrade

```bash
git pull
docker compose down
docker compose up -d --build
# Migrations run on start; manual:
docker compose exec bot alembic upgrade head
```

## Architecture

```
User /start → pick plan → pay → webhook verify + amount check → fulfill → invite link → auto-approve join → notify admins
Cron: hourly expiry kick / 6h renewal reminder / 15min pending-order cleanup
```

Full deployment, domain, payments, and security → [docs/CONFIG_GUIDE.md](docs/CONFIG_GUIDE.md) (Chinese)
