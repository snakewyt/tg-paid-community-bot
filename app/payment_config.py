"""Persist updates to JSON and update in-memory settings."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("data/payment_config.json")

PAYMENT_KEYS = {
    "stars_enabled", "stars_provider_token",
    "crypto_enabled", "crypto_api_token",
    "stripe_enabled", "stripe_secret_key", "stripe_webhook_secret",
    "stripe_success_url", "stripe_cancel_url",
    "alipay_backend", "wechat_backend",
    "epay_enabled", "epay_api_url", "epay_pid", "epay_key", "epay_notify_url", "epay_return_url",
    "hupijiao_enabled", "hupijiao_api_url", "hupijiao_appid", "hupijiao_appsecret", "hupijiao_notify_url", "hupijiao_return_url",
}

FIELD_META = {
    "stars_enabled":           ("Telegram Stars", "启用", "", "bool"),
    "stars_provider_token":    ("Telegram Stars", "Provider Token", "留空则使用 Bot Token", "password"),
    "crypto_enabled":          ("CryptoBot (USDT)", "启用", "", "bool"),
    "crypto_api_token":        ("CryptoBot (USDT)", "API Token", "从 @CryptoBot 获取", "password"),
    "stripe_enabled":          ("Stripe (信用卡)", "启用", "", "bool"),
    "stripe_secret_key":       ("Stripe (信用卡)", "Secret Key", "sk_live_...", "password"),
    "stripe_webhook_secret":   ("Stripe (信用卡)", "Webhook Secret", "whsec_...", "password"),
    "stripe_success_url":      ("Stripe (信用卡)", "支付成功跳转", "https://t.me/your_bot", "text"),
    "stripe_cancel_url":       ("Stripe (信用卡)", "取消支付跳转", "https://t.me/your_bot", "text"),
    "alipay_backend":          ("支付宝路由", "支付后台", "epay | hupijiao | 留空禁用", "select_epay_hupijiao"),
    "wechat_backend":          ("微信支付路由", "支付后台", "epay | hupijiao | 留空禁用", "select_epay_hupijiao"),
    "epay_enabled":            ("易支付 (Epay)", "启用", "", "bool"),
    "epay_api_url":            ("易支付 (Epay)", "API 地址", "https://pay.example.com", "text"),
    "epay_pid":                ("易支付 (Epay)", "商户 ID (PID)", "", "text"),
    "epay_key":                ("易支付 (Epay)", "商户密钥 (KEY)", "", "password"),
    "epay_notify_url":         ("易支付 (Epay)", "回调地址", "https://your-domain.com/webhook/epay", "text"),
    "epay_return_url":         ("易支付 (Epay)", "支付完成跳转", "用户付完款后跳转的地址", "text"),
    "hupijiao_enabled":        ("虎皮椒 (HuPiJiao)", "启用", "", "bool"),
    "hupijiao_api_url":        ("虎皮椒 (HuPiJiao)", "API 地址", "https://api.xunhupay.com", "text"),
    "hupijiao_appid":          ("虎皮椒 (HuPiJiao)", "APPID", "", "text"),
    "hupijiao_appsecret":      ("虎皮椒 (HuPiJiao)", "APPSECRET", "", "password"),
    "hupijiao_notify_url":     ("虎皮椒 (HuPiJiao)", "回调地址", "https://your-domain.com/webhook/hupijiao", "text"),
    "hupijiao_return_url":     ("虎皮椒 (HuPiJiao)", "支付完成跳转", "用户付完款后跳转的地址", "text"),
}

GROUPS = [
    "Telegram Stars",
    "CryptoBot (USDT)",
    "Stripe (信用卡)",
    "支付宝路由",
    "微信支付路由",
    "易支付 (Epay)",
    "虎皮椒 (HuPiJiao)",
]

ROUTING_GROUPS = frozenset({"支付宝路由", "微信支付路由"})
BACKEND_SECTION_IDS = {
    "易支付 (Epay)": "backend-section-epay",
    "虎皮椒 (HuPiJiao)": "backend-section-hupijiao",
}
BACKEND_BY_ROUTING_VALUE = {
    "epay": "backend-section-epay",
    "hupijiao": "backend-section-hupijiao",
}

_config_cache: dict = {}


def init_config() -> None:
    global _config_cache
    _config_cache = {k: getattr(settings, k) for k in PAYMENT_KEYS}
    if CONFIG_PATH.exists():
        try:
            overrides = json.loads(CONFIG_PATH.read_text())
            for k, v in overrides.items():
                if k in PAYMENT_KEYS:
                    _config_cache[k] = v
                    setattr(settings, k, v)
            logger.info("Loaded %d payment config overrides from %s", len(overrides), CONFIG_PATH)
        except Exception:
            logger.warning("Failed to load %s, using .env defaults", CONFIG_PATH)


def get_config() -> dict:
    return deepcopy(_config_cache)


def save_config(data: dict) -> None:
    """Persist updates; merge bools and keep secrets when fields left blank."""
    global _config_cache
    merged = deepcopy(_config_cache)
    bool_keys = {k for k, m in FIELD_META.items() if m[3] == "bool"}

    for k in bool_keys:
        raw = data.get(k, "off")
        merged[k] = str(raw).lower() in ("true", "on", "1", "yes")

    for k, v in data.items():
        if k not in PAYMENT_KEYS or k in bool_keys:
            continue
        meta = FIELD_META.get(k)
        if meta and meta[3] == "password" and not str(v).strip():
            continue
        merged[k] = v

    for k, v in merged.items():
        _config_cache[k] = v
        setattr(settings, k, v)

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass
    logger.info("Payment config saved to %s", CONFIG_PATH)
