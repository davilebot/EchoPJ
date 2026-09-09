import json
import unittest
from unittest.mock import Mock

from service.payments import AsaasClient, BillingCatalog, PaymentError, normalize_asaas_event


CATALOG = json.dumps([{
    "code": "growth",
    "name": "Crescimento",
    "kind": "subscription",
    "price_cents": 14990,
    "credits": 1000,
    "cycle": "MONTHLY",
    "description": "Créditos mensais para prospecção recorrente.",
    "features": ["1.000 créditos por ciclo", "Equipe no mesmo workspace"],
    "highlighted": True,
}])


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.payload).encode()


class PaymentTests(unittest.TestCase):
    def test_catalog_validates_and_exposes_only_commercial_fields(self):
        offer = BillingCatalog(CATALOG).get("GROWTH")
        self.assertEqual(offer.price_cents, 14990)
        self.assertEqual(offer.public()["cycle"], "MONTHLY")
        with self.assertRaises(PaymentError):
            BillingCatalog('[{"code":"bad","name":"Bad","kind":"subscription","price_cents":0,"credits":1,"cycle":"MONTHLY","description":"Bad","features":["x"]}]')

    def test_asaas_checkout_keeps_payment_data_on_hosted_page(self):
        opener = Mock(return_value=Response({"id": "chk_1", "link": "https://sandbox.asaas.com/checkout/1"}))
        client = AsaasClient("https://api-sandbox.asaas.com", "test-key", opener=opener)
        result = client.create_checkout(
            BillingCatalog(CATALOG).get("growth"),
            external_reference="echopjs-order:one",
            public_url="https://saas.example.com",
        )
        self.assertEqual(result["provider_checkout_id"], "chk_1")
        request = opener.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["billingTypes"], ["PIX", "CREDIT_CARD"])
        self.assertEqual(payload["chargeTypes"], ["RECURRENT"])
        self.assertEqual(payload["externalReference"], "echopjs-order:one")
        self.assertNotIn("creditCard", payload)

    def test_asaas_subscription_cancellation_uses_provider_identifier(self):
        opener = Mock(return_value=Response({"deleted": True, "id": "sub_1"}))
        client = AsaasClient("https://api-sandbox.asaas.com", "test-key", opener=opener)
        result = client.cancel_subscription("sub_1")
        self.assertTrue(result["canceled"])
        request = opener.call_args.args[0]
        self.assertEqual(request.method, "DELETE")
        self.assertEqual(request.full_url, "https://api-sandbox.asaas.com/v3/subscriptions/sub_1")
        self.assertIsNone(request.data)

    def test_webhook_normalization_uses_reconciliation_fields(self):
        event = normalize_asaas_event({
            "id": "evt_1", "event": "payment_received",
            "payment": {"id": "pay_1", "subscription": "sub_1", "externalReference": "order", "value": 149.9},
            "newProviderField": {"ignored": True},
        })
        self.assertEqual(event["event_type"], "PAYMENT_RECEIVED")
        self.assertEqual(event["provider_payment_id"], "pay_1")
        self.assertEqual(event["amount_cents"], 14990)

    def test_webhook_normalization_keeps_cycle_status_without_private_payload(self):
        event = normalize_asaas_event({
            "id": "evt_2", "event": "SUBSCRIPTION_UPDATED",
            "subscription": {
                "id": "sub_2", "externalReference": "order", "status": "ACTIVE",
                "nextDueDate": "2026-10-09", "customer": "cus_private",
            },
        })
        self.assertEqual(event["subscription_status"], "ACTIVE")
        self.assertEqual(event["subscription_next_due_date"], "2026-10-09")
        self.assertNotIn("customer", event)

        checkout_event = normalize_asaas_event({
            "id": "evt_3", "event": "CHECKOUT_PAID",
            "checkout": {"id": "chk_3", "subscription": {"id": "sub_nested"}},
        })
        self.assertEqual(checkout_event["provider_subscription_id"], "sub_nested")


if __name__ == "__main__":
    unittest.main()
