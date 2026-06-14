"""Alipay payment channel — routes to epay or hupijiao backend."""

from __future__ import annotations

from app.payments.backend_routed import BackendRoutedProvider
from app.payments.base import register_provider


class AlipayProvider(BackendRoutedProvider):
    name = "alipay"
    payment_type = "alipay"
    backend_setting = "alipay_backend"


register_provider(AlipayProvider())
