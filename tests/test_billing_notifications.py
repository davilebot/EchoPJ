import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from service.auth import AuthStore
from service.billing_notifications import BillingEmailDispatcher
from service.saas import SaaSStore


class BillingEmailDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.auth = AuthStore(str(Path(self.tmp.name) / "auth.sqlite"))
        self.auth.bootstrap("owner@example.com", "test-password-long")
        self.owner = self.auth.authenticate("owner@example.com", "test-password-long")
        self.organization_id = self.auth.ensure_initial_organization()
        self.auth.rename_organization(self.owner["id"], self.organization_id, "Cliente Exemplo")
        invitation = self.auth.create_invitation(
            self.owner["id"], self.organization_id, "viewer@example.com", "viewer"
        )
        self.auth.accept_invitation(invitation["token"], "viewer-password-long")
        self.saas = SaaSStore(str(Path(self.tmp.name) / "saas.sqlite"))
        self.saas.ensure_organization(self.organization_id, initial_credits=0)
        order = self.saas.create_billing_order(
            self.organization_id, self.owner["id"], provider="asaas",
            kind="subscription", plan_code="growth", plan_name="Crescimento",
            price_cents=14990, credits=1000, cycle="MONTHLY",
        )
        self.saas.process_billing_event(
            provider="asaas", event_id="evt_email_cycle", event_type="PAYMENT_CONFIRMED",
            payload_digest="e" * 64, external_reference=order["external_reference"],
            provider_payment_id="pay_email_cycle", provider_subscription_id="sub_email_cycle",
            amount_cents=14990, subscription_next_due_date="2026-09-16",
        )
        self.settings = SimpleNamespace(
            app_public_url="https://saas.example.com", smtp_from="billing@example.com"
        )

    def tearDown(self):
        self.saas.close()
        self.auth.close()
        self.tmp.cleanup()

    def test_dispatcher_sends_once_to_administrators_for_each_cycle(self):
        sent = []

        def sender(_settings, **payload):
            sent.append(payload)
            return "sent"

        dispatcher = BillingEmailDispatcher(
            self.settings, self.saas, self.auth, enabled=True, sender=sender
        )
        current = datetime(2026, 9, 9, tzinfo=timezone.utc)
        first = dispatcher.run_once(now=current)
        repeated = dispatcher.run_once(now=current + timedelta(minutes=20))

        self.assertEqual((first["sent"], repeated["sent"]), (1, 0))
        self.assertEqual([item["email"] for item in sent], ["owner@example.com"])
        self.assertEqual(sent[0]["kind"], "renewal")
        self.assertEqual(sent[0]["due_date"], "16/09/2026")
        self.assertIn(f"organization={self.organization_id}&tab=billing", sent[0]["link"])
        audit = self.saas.admin_billing_email_deliveries()
        self.assertEqual(audit["counts"], {"sent": 1})
        self.assertEqual(audit["deliveries"][0]["attempts"], 1)

    def test_failed_delivery_retries_only_after_one_hour(self):
        outcomes = iter(("failed", "sent"))
        dispatcher = BillingEmailDispatcher(
            self.settings,
            self.saas,
            self.auth,
            enabled=True,
            sender=lambda *_args, **_kwargs: next(outcomes),
        )
        current = datetime(2026, 9, 9, tzinfo=timezone.utc)
        self.assertEqual(dispatcher.run_once(now=current)["failed"], 1)
        self.assertEqual(dispatcher.run_once(now=current + timedelta(minutes=59))["sent"], 0)
        self.assertEqual(dispatcher.run_once(now=current + timedelta(hours=1))["sent"], 1)
        delivery = self.saas.admin_billing_email_deliveries()["deliveries"][0]
        self.assertEqual((delivery["status"], delivery["attempts"]), ("sent", 2))


if __name__ == "__main__":
    unittest.main()
