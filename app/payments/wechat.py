"""WeChat Pay channel — routes to epay or hupijiao backend."""

from __future__ import annotations

from app.payments.backend_routed import BackendRoutedProvider
from app.payments.base import register_provider


class WechatProvider(BackendRoutedProvider):
    name = "wechat"
    payment_type = "wechat"
    backend_setting = "wechat_backend"


register_provider(WechatProvider())
