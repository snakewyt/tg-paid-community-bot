from __future__ import annotations

from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Bot ---
    bot_token: str = ""
    admin_ids: Annotated[list[int], NoDecode] = []

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, v: object) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [int(x) for x in v]
        return v  # type: ignore[return-value]

    # --- Telegram Stars ---
    stars_enabled: bool = False
    stars_provider_token: str = ""

    # --- CryptoBot (Crypto Pay API) ---
    crypto_enabled: bool = False
    crypto_api_token: str = ""

    # --- Stripe ---
    stripe_enabled: bool = False
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_success_url: str = "https://t.me/your_bot"
    stripe_cancel_url: str = "https://t.me/your_bot"

    # --- Alipay / WeChat backend routing ---
    # Which payment backend processes alipay? "epay" | "hupijiao" | "" (disabled)
    alipay_backend: str = ""
    # Which payment backend processes wechat? "epay" | "hupijiao" | "" (disabled)
    wechat_backend: str = ""

    # --- Epay backend (易支付, Epay protocol) ---
    epay_enabled: bool = False
    epay_api_url: str = ""
    epay_pid: str = ""
    epay_key: str = ""
    epay_notify_url: str = ""
    epay_return_url: str = ""

    # --- HuPiJiao V3 backend (虎皮椒) ---
    hupijiao_enabled: bool = False
    hupijiao_api_url: str = "https://api.xunhupay.com"
    hupijiao_appid: str = ""
    hupijiao_appsecret: str = ""
    hupijiao_notify_url: str = ""
    hupijiao_return_url: str = ""

    # --- Webhook server ---
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8000

    # --- Admin web panel ---
    # Defaults: admin / 123456. Change via the panel on first login.
    admin_panel_username: str = "admin"
    admin_panel_password: str = "123456"
    # Comma-separated IPs allowed to access /admin (empty = allow all)
    admin_panel_allowed_ips: str = ""

    # --- Bot behavior (configurable via admin panel) ---
    admin_usernames: str = ""  # comma-separated TG usernames; grants admin with admin_ids
    welcome_message: str = "欢迎使用VIP会员购买机器人！\n\n请选择您需要的服务："
    order_timeout_minutes: int = 30
    expiry_reminder_days: int = 3
    expiry_reminder_message: str = "您的会员将在 {days} 天后到期，请及时续费！"

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///data/bot.db"


settings = Settings()
