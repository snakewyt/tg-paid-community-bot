"""Shared base for payment channels that route to an epay/hupijiao backend.

Alipay and WeChat are user-facing channels with identical logic; they only
differ in which settings key selects the backend and which payment-type string
is passed to it. Subclasses set three class attributes.
"""

from __future__ import annotations

from app.config import settings
from app.models.models import Order, Plan
from app.payments.base import BasePaymentProvider, CallbackData, PaymentResult


class BackendRoutedProvider(BasePaymentProvider):
    name = "base-routed"
    payment_type = ""       # "alipay" | "wechat" — passed to the backend
    backend_setting = ""    # settings attr selecting the backend ("alipay_backend" ...)

    def _backend(self):
        """Return the active backend module, or None if disabled/unknown."""
        name = getattr(settings, self.backend_setting, "")
        if name == "epay":
            from app.payments import epay
            return epay
        if name == "hupijiao":
            from app.payments import hupijiao
            return hupijiao
        return None

    def is_configured(self) -> bool:
        backend = self._backend()
        return bool(backend and backend.is_configured())

    async def create_payment(self, order: Order, plan: Plan) -> PaymentResult:
        backend = self._backend()
        if backend is None:
            return PaymentResult(success=False)
        return await backend.create_payment_backend(order, plan, self.payment_type)

    async def verify_callback(self, data: CallbackData) -> bool:
        backend = self._backend()
        return bool(backend and backend.verify_backend_callback(data.raw_body))

    async def extract_order_id(self, data: CallbackData) -> str | None:
        backend = self._backend()
        if backend is None:
            return None
        return backend.extract_backend_order_id(data.raw_body)

    async def verify_payment_amount(self, data: CallbackData, order: Order) -> bool:
        if order.amount == 0:
            return True
        backend = self._backend()
        if backend is None:
            return False
        paid = backend.extract_backend_money(data.raw_body)
        if paid is None:
            return False
        return abs(paid - order.amount) < 0.02
