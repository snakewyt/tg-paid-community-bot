"""Shared constants used across bot handlers and the admin panel."""

from __future__ import annotations

# Single source of truth for user-facing payment-channel labels.
PROVIDER_LABELS: dict[str, str] = {
    "stars": "Telegram Stars",
    "crypto": "Crypto (USDT)",
    "stripe": "Stripe",
    "alipay": "支付宝",
    "wechat": "微信支付",
}
