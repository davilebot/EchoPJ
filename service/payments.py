"""Provider-neutral billing catalog and the hosted Asaas checkout adapter."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PaymentError(ValueError):
    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.status = status


@dataclass(frozen=True)
class BillingOffer:
    code: str
    name: str
    kind: str
    price_cents: int
    credits: int
    description: str
    features: tuple[str, ...]
    cycle: str | None = None
    highlighted: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "kind": self.kind,
            "price_cents": self.price_cents,
            "credits": self.credits,
            "description": self.description,
            "features": list(self.features),
            "cycle": self.cycle,
            "highlighted": self.highlighted,
        }


class BillingCatalog:
    """Validated commercial offers loaded from deployment configuration."""

    def __init__(self, raw_json: str = ""):
        self._offers: dict[str, BillingOffer] = {}
        if not raw_json.strip():
            return
        try:
            raw_offers = json.loads(raw_json)
        except json.JSONDecodeError as error:
            raise PaymentError("O catálogo comercial configurado não contém JSON válido.", 500) from error
        if not isinstance(raw_offers, list):
            raise PaymentError("O catálogo comercial precisa ser uma lista de ofertas.", 500)
        for item in raw_offers:
            offer = self._parse_offer(item)
            if offer.code in self._offers:
                raise PaymentError(f"O plano {offer.code} aparece mais de uma vez no catálogo.", 500)
            self._offers[offer.code] = offer

    @staticmethod
    def _parse_offer(item: Any) -> BillingOffer:
        if not isinstance(item, dict):
            raise PaymentError("Cada oferta comercial precisa ser um objeto.", 500)
        code = str(item.get("code", "")).strip().casefold()
        name = " ".join(str(item.get("name", "")).split())
        kind = str(item.get("kind", "")).strip().casefold()
        description = " ".join(str(item.get("description", "")).split())
        features = item.get("features", [])
        cycle = str(item.get("cycle", "")).strip().upper() or None
        try:
            price_cents = int(item.get("price_cents"))
            credits = int(item.get("credits"))
        except (TypeError, ValueError) as error:
            raise PaymentError("Preço e créditos do catálogo precisam ser números inteiros.", 500) from error
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,49}", code):
            raise PaymentError("O catálogo contém um código de plano inválido.", 500)
        if not name or len(name) > 80 or not description or len(description) > 240:
            raise PaymentError(f"O plano {code} precisa de nome e descrição válidos.", 500)
        if kind not in {"subscription", "credit_pack"}:
            raise PaymentError(f"O tipo do plano {code} é inválido.", 500)
        if price_cents <= 0 or credits <= 0:
            raise PaymentError(f"O plano {code} precisa ter preço e créditos positivos.", 500)
        if not isinstance(features, list) or not features or any(not str(value).strip() for value in features):
            raise PaymentError(f"O plano {code} precisa ter ao menos um benefício.", 500)
        if kind == "subscription" and cycle not in {
            "WEEKLY", "BIWEEKLY", "MONTHLY", "BIMONTHLY", "QUARTERLY", "SEMIANNUALLY", "YEARLY",
        }:
            raise PaymentError(f"O ciclo do plano {code} é inválido.", 500)
        if kind == "credit_pack":
            cycle = None
        return BillingOffer(
            code=code,
            name=name,
            kind=kind,
            price_cents=price_cents,
            credits=credits,
            description=description,
            features=tuple(" ".join(str(value).split())[:160] for value in features[:12]),
            cycle=cycle,
            highlighted=bool(item.get("highlighted", False)),
        )

    def offers(self) -> list[BillingOffer]:
        return list(self._offers.values())

    def get(self, code: str) -> BillingOffer:
        offer = self._offers.get(code.strip().casefold())
        if not offer:
            raise PaymentError("Este plano não está disponível.", 404)
        return offer


class AsaasClient:
    """Small HTTPS adapter; card data always stays on the provider-hosted page."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        *,
        timeout: float = 12.0,
        opener: Callable[..., Any] = urlopen,
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._opener = opener

    @property
    def available(self) -> bool:
        return bool(self.api_key.strip())

    def create_checkout(
        self,
        offer: BillingOffer,
        *,
        external_reference: str,
        public_url: str,
    ) -> dict[str, str]:
        if not self.available:
            raise PaymentError("O checkout ainda não foi ativado pela EchoHub.", 503)
        callback_url = f"{public_url.rstrip('/')}/billing/return"
        body: dict[str, Any] = {
            "billingTypes": ["PIX", "CREDIT_CARD"],
            "chargeTypes": ["RECURRENT" if offer.kind == "subscription" else "DETACHED"],
            "minutesToExpire": 60,
            "externalReference": external_reference,
            "callback": {
                "successUrl": f"{callback_url}?status=success",
                "cancelUrl": f"{callback_url}?status=cancel",
                "expiredUrl": f"{callback_url}?status=expired",
            },
            "items": [{
                "name": offer.name,
                "description": offer.description,
                "quantity": 1,
                "value": float((Decimal(offer.price_cents) / Decimal(100)).quantize(Decimal("0.01"))),
            }],
        }
        if offer.kind == "subscription":
            body["subscription"] = {"cycle": offer.cycle, "nextDueDate": date.today().isoformat()}
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.api_url}/v3/checkouts",
            data=payload,
            method="POST",
            headers={
                "access_token": self.api_key,
                "accept": "application/json",
                "content-type": "application/json",
                "user-agent": "EchoPJs/1.0 billing",
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            try:
                provider_error = json.loads(error.read().decode("utf-8"))
                detail = provider_error.get("errors", [{}])[0].get("description")
            except Exception:
                detail = None
            raise PaymentError(detail or "O provedor recusou a criação do checkout.", 502) from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise PaymentError("O checkout está temporariamente indisponível. Tente novamente.", 502) from error
        checkout_id = str(result.get("id", "")).strip()
        checkout_url = str(result.get("link", "")).strip()
        if not checkout_id or not checkout_url.startswith("https://"):
            raise PaymentError("O provedor não devolveu um link de checkout válido.", 502)
        return {"provider_checkout_id": checkout_id, "checkout_url": checkout_url}


def normalize_asaas_event(payload: Any) -> dict[str, Any]:
    """Extract only reconciliation fields and tolerate new provider attributes."""
    if not isinstance(payload, dict):
        raise PaymentError("Evento de cobrança inválido.", 422)
    event_id = str(payload.get("id", "")).strip()
    event_type = str(payload.get("event", "")).strip().upper()
    if not event_id or len(event_id) > 255 or not event_type or len(event_type) > 80:
        raise PaymentError("Evento de cobrança sem identificação válida.", 422)
    checkout = payload.get("checkout") if isinstance(payload.get("checkout"), dict) else {}
    payment = payload.get("payment") if isinstance(payload.get("payment"), dict) else {}
    subscription = payload.get("subscription") if isinstance(payload.get("subscription"), dict) else {}
    amount_cents = None
    if payment.get("value") is not None:
        try:
            amount_cents = int((Decimal(str(payment["value"])) * Decimal(100)).quantize(Decimal("1")))
        except Exception as error:
            raise PaymentError("Evento de cobrança com valor inválido.", 422) from error
    return {
        "event_id": event_id,
        "event_type": event_type,
        "external_reference": next((str(value).strip() for value in (
            checkout.get("externalReference"), payment.get("externalReference"), subscription.get("externalReference")
        ) if value), None),
        "provider_checkout_id": str(checkout.get("id", "")).strip() or None,
        "provider_payment_id": str(payment.get("id", "")).strip() or None,
        "provider_subscription_id": str(
            subscription.get("id") or payment.get("subscription") or checkout.get("subscription") or ""
        ).strip() or None,
        "amount_cents": amount_cents,
    }
