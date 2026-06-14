"""Payment provider abstract base and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.models import Order, Plan


@dataclass
class PaymentResult:
    success: bool
    provider_tx_id: str | None = None
    pay_url: str | None = None
    invoice_payload: str | None = None  # for Stars sendInvoice


@dataclass
class CallbackData:
    order_id: str
    raw_body: str
    signature: str | None = None


class BasePaymentProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def create_payment(self, order: Order, plan: Plan) -> PaymentResult:
        ...

    @abstractmethod
    async def verify_callback(self, data: CallbackData) -> bool:
        ...

    @abstractmethod
    async def extract_order_id(self, data: CallbackData) -> str | None:
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        ...


_providers: dict[str, BasePaymentProvider] = {}


def register_provider(p: BasePaymentProvider) -> None:
    _providers[p.name] = p


def get_provider(name: str) -> BasePaymentProvider:
    return _providers[name]


def list_configured_providers() -> list[BasePaymentProvider]:
    return [p for p in _providers.values() if p.is_configured()]
