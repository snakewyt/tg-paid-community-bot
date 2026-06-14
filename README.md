# Telegram Paid Community Bot

A Telegram bot framework similar to InviteMember for managing paid community groups.

## Features

- Subscription plan management with multi-currency pricing
- Four payment methods: Telegram Stars, CryptoBot (USDT), Stripe, Alipay/WeChat
- Join-request invite links: anyone clicking the link files a join request, and the bot approves only the user with an active subscription — leaked/forwarded links are useless
- Automatic expiry handling (kick + unban on expiry)
- Renewal reminders (3 days, 1 day before expiry)
- Admin commands for plan management, stats, grants, broadcast
- Docker deployment

## Quick Start

### 1. Create a Telegram Bot

Talk to [@BotFather](https://t.me/BotFather), create a bot, and get the token.

### 2. Clone & Configure

```bash
cp .env.example .env
# Edit .env with your bot token and admin IDs
```

Minimal `.env`:
```env
BOT_TOKEN=123456:ABC-DEF1234gh
ADMIN_IDS=123456789
```

### 3. Run

```bash
# With Docker
docker compose up -d

# Or locally
pip install -r requirements.txt
python -m app.main
```

## Payment Setup

### Telegram Stars
Set `STARS_ENABLED=true` in `.env`. No additional keys needed — uses Telegram's native payments.

### CryptoBot (USDT/TON)
1. Open [@CryptoBot](https://t.me/CryptoBot) or [@CryptoTestnetBot](https://t.me/CryptoTestnetBot) for testing
2. Run `/start` → `Crypto Pay` → `Create App` to get API token
3. Set in `.env`:
```env
CRYPTO_ENABLED=true
CRYPTO_API_TOKEN=your_api_token
CRYPTO_WEBHOOK_SECRET=any_random_string
```

### Stripe
1. Get keys from [Stripe Dashboard](https://dashboard.stripe.com/apikeys)
2. Set webhook endpoint to `https://your-server.com/webhook/stripe` (events: `checkout.session.completed`)
3. Set in `.env`:
```env
STRIPE_ENABLED=true
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_SUCCESS_URL=https://t.me/your_bot
STRIPE_CANCEL_URL=https://t.me/your_bot
```

### Epay (易支付)
1. Register with an Epay-compatible aggregator (Vmq, PaysApi, merchant account, etc.)
2. Set notify URL to `https://your-server.com/webhook/epay`
3. Set in `.env`:
```env
EPAY_ENABLED=true
EPAY_API_URL=https://your-epay-gateway.com/
EPAY_PID=your_merchant_id
EPAY_KEY=your_merchant_key
EPAY_NOTIFY_URL=https://your-server.com/webhook/epay
```

### HuPiJiao V3 (虎皮椒)
1. Register at [xunhupay.com](https://www.xunhupay.com)
2. Create a payment channel: 支付渠道管理 → 我的支付渠道 → 申请
3. Get `APPID` and `APPSECRET`
4. Set notify URL to `https://your-server.com/webhook/hupijiao`
5. Set in `.env`:
```env
HUPIJIAO_ENABLED=true
HUPIJIAO_APPID=your_appid
HUPIJIAO_APPSECRET=your_appsecret
HUPIJIAO_NOTIFY_URL=https://your-server.com/webhook/hupijiao
```

Each payment method has an independent on/off switch and per-plan price.
Setting a price to 0 hides that method for that plan.

### Alipay / WeChat Pay routing

A single admin setting controls which backend processes each user-facing channel:

```env
# Choose one backend per channel: "epay" or "hupijiao"
ALIPAY_BACKEND=hupijiao     # 支付宝 → 虎皮椒
WECHAT_BACKEND=epay          # 微信支付 → 易支付
```

This means:
- At most one backend handles 支付宝; at most one handles 微信支付
- Their Configure sections below (易支付, 虎皮椒) only matter when the corresponding `*_BACKEND` points to them
- Users see "支付宝" and "微信支付" buttons — never "易支付" or "虎皮椒"

## Admin Commands

| Command | Description |
|---------|------------|
| `/admin` | Show all admin commands |
| `/addplan name duration_days chat_id [stars] [crypto] [stripe_cents] [cny]` | Create a plan |
| `/editplan <id> <field> <value>` | Edit plan (fields: name, days, chat_id, stars, crypto, stripe, cny) |
| `/delplan <id>` | Deactivate a plan |
| `/plans` | List all plans |
| `/stats` | Revenue & member statistics |
| `/grant <user_id> <plan_id> <days>` | Gift subscription |
| `/setexpiry <user_id> <plan_id> <+N\|-N\|YYYY-MM-DD>` | Adjust a user's expiry date |
| `/active` | List active subscriptions |
| `/broadcast <text>` | Send message to all users |

## Admin Web Panel (Management Backend)

A full management backend served by the same process:

- Dashboard: revenue by currency/provider, active subscribers, recent orders
- Plan management: create / edit (name, duration, prices, chat) / enable-disable
- Member management: search by user ID, adjust expiry (`+7` / `-3` / exact date), revoke & kick
- All purchases still happen inside the bot — the panel is for administration only

1. Set a long random token in `.env`:
```env
ADMIN_PANEL_TOKEN=some-long-random-string
```
2. Visit `http://your-server:8000/admin?token=<value>` (token is then remembered via cookie).

Leave `ADMIN_PANEL_TOKEN` empty to disable the panel entirely (returns 404).

## Architecture

```
User → Bot (/start) → Select Plan → Select Payment
  ├── Stars: Native invoice → Telegram handles payment → Bot gets callback
  ├── CryptoBot: API creates invoice → User pays in CryptoBot → Webhook callback
  ├── Stripe: Checkout Session → User pays on Stripe page → Webhook callback
  └── CN Pay: Epay redirect → User pays on aggregator page → Webhook callback
                         ↓
           Payment callback → Fulfill order → Create subscription
                         ↓
           Bot sends one-time invite link → User joins group
                         ↓
           Scheduler: hourly expiry check → kick expired users
                      every 6h → renewal reminders (3d, 1d before)
```

## License

MIT
