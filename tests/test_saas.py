import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from service.saas import SaaSError, SaaSStore


COMPANY_A = {"cnpj": "11222333000181", "legal_name": "Empresa A", "uf": "SP"}
COMPANY_B = {"cnpj": "19131243000197", "legal_name": "Empresa B", "uf": "RJ"}


class SaaSStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SaaSStore(str(Path(self.tmp.name) / "saas.sqlite"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_internal_organization_has_unlimited_credits(self):
        profile = self.store.ensure_organization(1, unlimited=True)
        self.assertTrue(profile["unlimited_credits"])
        self.assertEqual(profile["plan_code"], "internal")
        company_list = self.store.create_company_list(1, 7, name="Clientes")
        result = self.store.add_companies(1, company_list["id"], 7, [COMPANY_A, COMPANY_B])
        self.assertEqual(result["credits_spent"], 0)
        self.assertEqual(result["total"], 2)
        self.assertEqual(self.store.billing_summary(1)["unlocked_companies"], 2)

    def test_existing_payment_table_is_upgraded_for_provider_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "previous-saas.sqlite"
            connection = sqlite3.connect(path)
            connection.execute("""CREATE TABLE billing_payments (
                provider TEXT NOT NULL,
                provider_payment_id TEXT NOT NULL,
                organization_id INTEGER NOT NULL,
                order_id TEXT NOT NULL,
                provider_subscription_id TEXT,
                amount_cents INTEGER,
                status TEXT NOT NULL,
                provider_status TEXT,
                billing_type TEXT,
                due_date TEXT,
                credits_granted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                paid_at TEXT,
                PRIMARY KEY(provider, provider_payment_id)
            )""")
            connection.commit()
            connection.close()

            upgraded = SaaSStore(str(path))
            columns = {
                row[1] for row in upgraded._connection.execute("PRAGMA table_info(billing_payments)")
            }
            upgraded.close()
            self.assertIn("invoice_url", columns)
            self.assertIn("receipt_url", columns)

    def test_billing_catalog_keeps_draft_separate_and_versions_publications(self):
        first = json.dumps([{"code": "growth", "name": "Crescimento"}])
        draft = self.store.save_billing_catalog_draft(7, first)
        self.assertEqual((draft["revision"], draft["status"]), (1, "draft"))
        self.assertIsNone(self.store.published_billing_catalog_json())
        published = self.store.publish_billing_catalog(8)
        self.assertEqual((published["revision"], published["status"], published["published_by"]), (1, "published", 8))
        self.assertEqual(self.store.published_billing_catalog_json(), first)
        second = json.dumps([{"code": "scale", "name": "Escala"}])
        next_draft = self.store.save_billing_catalog_draft(9, second)
        self.assertEqual(next_draft["revision"], 2)
        self.assertEqual(self.store.published_billing_catalog_json(), first)
        state = self.store.billing_catalog_state()
        self.assertEqual(state["draft"]["catalog_json"], second)
        self.assertEqual(state["published"]["catalog_json"], first)

    def test_trial_credits_are_spent_only_on_first_unlock(self):
        self.store.ensure_organization(2, initial_credits=2)
        first = self.store.create_company_list(2, 9, name="Primeira")
        second = self.store.create_company_list(2, 9, name="Segunda")
        initial = self.store.billing_summary(2)
        self.assertEqual(initial["profile"]["credit_balance"], 2)
        result = self.store.add_companies(2, first["id"], 9, [COMPANY_A])
        self.assertEqual(result["credits_spent"], 1)
        repeat = self.store.add_companies(2, second["id"], 9, [COMPANY_A])
        self.assertEqual(repeat["credits_spent"], 0)
        final = self.store.billing_summary(2)
        self.assertEqual(final["profile"]["credit_balance"], 1)
        self.assertEqual(final["unlocked_companies"], 1)

    def test_insufficient_credits_rolls_back_entire_selection(self):
        self.store.ensure_organization(3, initial_credits=1)
        company_list = self.store.create_company_list(3, 10, name="Alvos")
        with self.assertRaises(SaaSError) as raised:
            self.store.add_companies(3, company_list["id"], 10, [COMPANY_A, COMPANY_B])
        self.assertEqual(raised.exception.status, 402)
        self.assertEqual(self.store.company_list_detail(3, company_list["id"])["companies"], [])
        self.assertEqual(self.store.billing_summary(3)["profile"]["credit_balance"], 1)

    def test_lists_and_saved_searches_are_isolated_by_organization(self):
        self.store.ensure_organization(4)
        self.store.ensure_organization(5)
        saved = self.store.create_saved_search(
            4, 11, name="Autopeças SP", filters={"ufs": ["SP"], "limit": 100}
        )
        company_list = self.store.create_company_list(4, 11, name="Lista privada")
        self.assertIsNone(self.store.saved_search(5, saved["id"]))
        self.assertIsNone(self.store.company_list(5, company_list["id"]))
        self.assertEqual(self.store.list_saved_searches(5), [])
        self.assertEqual(self.store.list_company_lists(5), [])

    def test_company_list_name_and_description_can_be_updated_inside_tenant(self):
        self.store.ensure_organization(34)
        self.store.ensure_organization(35)
        company_list = self.store.create_company_list(
            34, 60, name="Prospects", description="Primeira versão",
        )
        updated = self.store.update_company_list(
            34, company_list["id"], 60,
            name="Prospects prioritários", description="Contatar nesta semana",
        )
        self.assertEqual(updated["name"], "Prospects prioritários")
        self.assertEqual(updated["description"], "Contatar nesta semana")
        with self.assertRaises(SaaSError) as raised:
            self.store.update_company_list(
                35, company_list["id"], 61, name="Outra empresa",
            )
        self.assertEqual(raised.exception.status, 404)

    def test_company_list_detail_supports_search_and_bounded_pages(self):
        self.store.ensure_organization(36, unlimited=True)
        company_list = self.store.create_company_list(36, 62, name="Carteira nacional")
        companies = [
            {
                "cnpj": f"{index:014d}",
                "legal_name": f"Cliente {index:03d}",
                "trade_name": f"Marca {index:03d}",
                "municipality": "Recife" if index % 10 == 0 else "São Paulo",
                "uf": "PE" if index % 10 == 0 else "SP",
            }
            for index in range(1, 76)
        ]
        self.store.add_companies(36, company_list["id"], 62, companies)

        page = self.store.company_list_detail(36, company_list["id"], limit=20, offset=20)
        self.assertEqual(page["company_count"], 75)
        self.assertEqual(page["filtered_count"], 75)
        self.assertEqual(len(page["companies"]), 20)
        self.assertTrue(page["pagination"]["has_previous"])
        self.assertTrue(page["pagination"]["has_next"])

        searched = self.store.company_list_detail(
            36, company_list["id"], query="Cliente 042", limit=20,
        )
        self.assertEqual(searched["filtered_count"], 1)
        self.assertEqual(searched["companies"][0]["cnpj"], "00000000000042")
        last_page = self.store.company_list_detail(
            36, company_list["id"], limit=20, offset=999,
        )
        self.assertEqual(last_page["pagination"]["offset"], 60)
        self.assertEqual(len(last_page["companies"]), 15)

    def test_company_list_memberships_are_scoped_to_the_organization(self):
        self.store.ensure_organization(37, unlimited=True)
        self.store.ensure_organization(38, unlimited=True)
        first = self.store.create_company_list(37, 63, name="Prospecção SP")
        second = self.store.create_company_list(37, 63, name="Contatar hoje")
        foreign = self.store.create_company_list(38, 64, name="Lista de outro cliente")
        company = {"cnpj": "11222333000181", "legal_name": "Empresa Exemplo"}
        self.store.add_companies(37, first["id"], 63, [company])
        self.store.add_companies(37, second["id"], 63, [company])
        self.store.add_companies(38, foreign["id"], 64, [company])

        memberships = self.store.company_list_memberships(37, [company["cnpj"], "00000000000000"])
        self.assertEqual(
            {item["name"] for item in memberships[company["cnpj"]]},
            {"Prospecção SP", "Contatar hoje"},
        )
        self.assertNotIn("00000000000000", memberships)
        self.assertNotIn("Lista de outro cliente", str(memberships))

        self.assertEqual(
            self.store.company_list_cnpjs(37, [first["id"]]),
            {company["cnpj"]},
        )
        self.assertEqual(self.store.company_list_cnpjs(37), {company["cnpj"]})
        self.assertEqual(self.store.company_list_cnpjs(38, [first["id"]]), set())

    def test_saved_search_records_last_run_and_can_be_deleted(self):
        self.store.ensure_organization(6)
        saved = self.store.create_saved_search(
            6, 12, name="Indústrias", filters={"cnaes": ["1091102"], "limit": 250}
        )
        updated = self.store.record_saved_search_run(6, saved["id"], 87)
        self.assertEqual(updated["last_result_count"], 87)
        self.assertIsNotNone(updated["last_run_at"])
        self.assertTrue(self.store.delete_saved_search(6, saved["id"]))
        self.assertFalse(self.store.delete_saved_search(6, saved["id"]))

    def test_saved_search_can_be_renamed_or_updated_without_losing_run_context(self):
        self.store.ensure_organization(32)
        self.store.ensure_organization(33)
        saved = self.store.create_saved_search(
            32, 50, name="Indústrias", filters={"ufs": ["SP"], "limit": 100}, result_count=42,
        )
        renamed = self.store.update_saved_search(
            32, saved["id"], 50, name="Indústrias de SP",
            filters={"ufs": ["SP"], "limit": 100},
        )
        self.assertEqual(renamed["name"], "Indústrias de SP")
        self.assertEqual(renamed["last_result_count"], 42)
        self.assertEqual(renamed["last_run_at"], saved["last_run_at"])

        changed = self.store.update_saved_search(
            32, saved["id"], 50, name="Indústrias do Sudeste",
            filters={"ufs": ["SP", "RJ"], "limit": 250},
        )
        self.assertEqual(changed["filters"]["ufs"], ["SP", "RJ"])
        self.assertIsNone(changed["last_result_count"])
        self.assertIsNone(changed["last_run_at"])
        with self.assertRaises(SaaSError) as raised:
            self.store.update_saved_search(
                33, saved["id"], 51, name="Fora do tenant", filters={"limit": 1},
            )
        self.assertEqual(raised.exception.status, 404)

    def test_credit_grants_are_idempotent(self):
        self.store.ensure_organization(7, initial_credits=0)
        self.store.grant_credits(7, 500, description="Pagamento", idempotency_key="payment:abc")
        self.store.grant_credits(7, 500, description="Pagamento", idempotency_key="payment:abc")
        summary = self.store.billing_summary(7)
        self.assertEqual(summary["profile"]["credit_balance"], 500)
        self.assertEqual(len(summary["ledger"]), 1)

    def test_credit_estimate_and_export_unlock_are_consistent(self):
        self.store.ensure_organization(8, initial_credits=2)
        estimate = self.store.credit_estimate(8, [COMPANY_A["cnpj"], COMPANY_B["cnpj"], COMPANY_A["cnpj"]])
        self.assertEqual(estimate["requested_companies"], 2)
        self.assertEqual(estimate["credits_required"], 2)
        self.assertTrue(estimate["can_complete"])
        first = self.store.unlock_companies(
            8, 13, [COMPANY_A["cnpj"], COMPANY_B["cnpj"]],
            kind="company_export", description="Exportação",
        )
        self.assertEqual(first["credits_spent"], 2)
        repeat = self.store.unlock_companies(
            8, 13, [COMPANY_A["cnpj"]], kind="company_export", description="Nova exportação",
        )
        self.assertEqual(repeat["credits_spent"], 0)
        self.assertEqual(self.store.credit_estimate(8, [COMPANY_A["cnpj"]])["credits_required"], 0)

    def test_export_unlock_rolls_back_when_balance_is_insufficient(self):
        self.store.ensure_organization(9, initial_credits=1)
        with self.assertRaises(SaaSError) as raised:
            self.store.unlock_companies(
                9, 14, [COMPANY_A["cnpj"], COMPANY_B["cnpj"]],
                kind="company_export", description="Exportação",
            )
        self.assertEqual(raised.exception.status, 402)
        summary = self.store.billing_summary(9)
        self.assertEqual(summary["profile"]["credit_balance"], 1)
        self.assertEqual(summary["unlocked_companies"], 0)

    def test_dashboard_counts_resources_and_recent_items(self):
        self.store.ensure_organization(10, initial_credits=3)
        company_list = self.store.create_company_list(10, 15, name="Clientes")
        self.store.add_companies(10, company_list["id"], 15, [COMPANY_A])
        self.store.create_saved_search(10, 15, name="Indústrias", filters={"cnaes": ["1091102"]})
        dashboard = self.store.dashboard_summary(10)
        self.assertEqual(dashboard["list_count"], 1)
        self.assertEqual(dashboard["saved_search_count"], 1)
        self.assertEqual(dashboard["unlocked_companies"], 1)
        self.assertEqual(dashboard["recent_lists"][0]["company_count"], 1)
        self.assertEqual(dashboard["usage"]["period_days"], 30)
        self.assertEqual(len(dashboard["usage"]["daily"]), 30)

    def test_usage_insights_reports_recent_activity_without_crossing_tenants(self):
        self.store.ensure_organization(30, initial_credits=5)
        self.store.ensure_organization(31, initial_credits=5)
        company_list = self.store.create_company_list(30, 40, name="Contas prioritárias")
        self.store.add_companies(30, company_list["id"], 40, [COMPANY_A, COMPANY_B])
        self.store.record_product_event(
            30, 40, "search.executed", occurred_at="2026-09-08T14:00:00+00:00",
        )
        self.store.record_product_event(
            30, 40, "search.executed", occurred_at="2026-08-10T14:00:00+00:00",
        )
        self.store.record_product_event(
            31, 41, "search.executed", occurred_at="2026-09-08T15:00:00+00:00",
        )
        with self.store._connection:
            self.store._connection.execute(
                "UPDATE company_unlocks SET unlocked_at='2026-08-20T10:00:00+00:00' WHERE organization_id=30"
            )
            self.store._connection.execute(
                "UPDATE credit_ledger SET created_at='2026-08-20T10:00:00+00:00' WHERE organization_id=30 AND delta < 0"
            )
            self.store._connection.execute(
                "UPDATE company_lists SET created_at='2026-08-21T10:00:00+00:00',updated_at='2026-08-21T10:00:00+00:00' WHERE organization_id=30"
            )
            self.store._connection.execute(
                "UPDATE company_list_items SET added_at='2026-08-21T10:00:00+00:00' WHERE organization_id=30"
            )
            self.store._connection.execute(
                """UPDATE product_events SET occurred_at='2026-08-21T10:00:00+00:00'
                   WHERE organization_id=30 AND event_name IN ('company_list.created','company_list.companies_added')"""
            )

        usage = self.store.usage_insights(
            30, now=datetime(2026, 9, 9, 12, tzinfo=timezone.utc),
        )
        self.assertEqual((usage["start_date"], usage["end_date"]), ("2026-08-11", "2026-09-09"))
        self.assertEqual(usage["unlocked_companies"], 2)
        self.assertEqual(usage["credits_spent"], 2)
        self.assertEqual(usage["active_days"], 3)
        self.assertEqual(len(usage["daily"]), 30)
        self.assertEqual(usage["daily"][9], {
            "date": "2026-08-20", "unlocked_companies": 2, "credits_spent": 2,
        })

        other = self.store.usage_insights(
            31, now=datetime(2026, 9, 9, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(other["unlocked_companies"], 0)
        self.assertEqual(other["credits_spent"], 0)
        self.assertEqual(other["active_days"], 1)

    def test_payment_idempotency_key_cannot_cross_organizations(self):
        self.store.ensure_organization(11, initial_credits=0)
        self.store.ensure_organization(12, initial_credits=0)
        self.store.grant_credits(11, 20, description="Pagamento", idempotency_key="payment:shared")
        with self.assertRaises(SaaSError) as raised:
            self.store.grant_credits(12, 20, description="Pagamento", idempotency_key="payment:shared")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(self.store.billing_summary(12)["profile"]["credit_balance"], 0)

    def test_billing_orders_and_webhooks_are_idempotent_and_auditable(self):
        self.store.ensure_organization(22, initial_credits=0)
        order = self.store.create_billing_order(
            22, 7, provider="asaas", kind="subscription", plan_code="growth",
            plan_name="Crescimento", price_cents=14990, credits=1000,
            cycle="MONTHLY", client_key="checkout-click-1",
        )
        repeated = self.store.create_billing_order(
            22, 7, provider="asaas", kind="subscription", plan_code="growth",
            plan_name="Crescimento", price_cents=14990, credits=1000,
            cycle="MONTHLY", client_key="checkout-click-1",
        )
        self.assertEqual(repeated["id"], order["id"])
        with self.assertRaises(SaaSError):
            self.store.create_billing_order(
                22, 7, provider="asaas", kind="subscription", plan_code="scale",
                plan_name="Escala", price_cents=39990, credits=4000,
                cycle="MONTHLY", client_key="checkout-click-1",
            )
        self.store.billing_checkout_created(
            22, order["id"], provider_checkout_id="chk_1", checkout_url="https://sandbox.asaas.com/checkout/1",
        )
        self.assertIsNone(self.store.subscription_purchase_blocker(22, client_key="checkout-click-1"))
        self.assertEqual(
            self.store.subscription_purchase_blocker(22, client_key="another-click")["kind"],
            "checkout",
        )
        checkout_event = self.store.process_billing_event(
            provider="asaas", event_id="evt_checkout", event_type="CHECKOUT_PAID",
            payload_digest="a" * 64, external_reference=order["external_reference"],
            provider_checkout_id="chk_1",
        )
        self.assertTrue(checkout_event["matched"])
        payment = dict(
            provider="asaas", event_type="PAYMENT_RECEIVED", payload_digest="b" * 64,
            external_reference=order["external_reference"], provider_payment_id="pay_1",
            provider_subscription_id="sub_1", amount_cents=14990,
            payment_invoice_url="https://sandbox.asaas.com/i/pay_1",
            payment_receipt_url="https://www.asaas.com/comprovantes/pay_1",
        )
        self.store.process_billing_event(event_id="evt_payment_received", **payment)
        self.store.process_billing_event(event_id="evt_payment_confirmed", event_type="PAYMENT_CONFIRMED", **{k: v for k, v in payment.items() if k != "event_type"})
        summary = self.store.billing_summary(22)
        self.assertEqual(summary["profile"]["credit_balance"], 1000)
        self.assertEqual(summary["profile"]["plan_code"], "growth")
        self.assertEqual(summary["orders"][0]["status"], "paid")
        self.assertEqual(summary["subscription"]["status"], "active")
        self.assertEqual(summary["payments"][0]["credits_granted"], 1000)
        self.assertEqual(summary["payments"][0]["status"], "received")
        self.assertEqual(summary["payments"][0]["invoice_url"], "https://sandbox.asaas.com/i/pay_1")
        self.assertEqual(summary["payments"][0]["receipt_url"], "https://www.asaas.com/comprovantes/pay_1")
        duplicate = self.store.process_billing_event(event_id="evt_payment_received", **payment)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len(self.store.admin_billing_events()), 3)

    def test_subscription_renewal_overdue_recovery_cancellation_and_reactivation(self):
        self.store.ensure_organization(24, initial_credits=0)
        order = self.store.create_billing_order(
            24, 7, provider="asaas", kind="subscription", plan_code="growth",
            plan_name="Crescimento", price_cents=14990, credits=1000, cycle="MONTHLY",
        )
        common = {
            "provider": "asaas", "payload_digest": "d" * 64,
            "external_reference": order["external_reference"], "provider_subscription_id": "sub_cycle_1",
            "amount_cents": 14990,
        }
        self.store.process_billing_event(
            event_id="evt_first", event_type="PAYMENT_CONFIRMED",
            provider_payment_id="pay_cycle_1", **common,
        )
        self.store.process_billing_event(
            event_id="evt_overdue", event_type="PAYMENT_OVERDUE",
            provider_payment_id="pay_cycle_2", **common,
        )
        overdue = self.store.billing_summary(24)
        self.assertEqual(overdue["profile"]["subscription_status"], "past_due")
        self.assertEqual(overdue["subscription"]["status"], "past_due")
        self.assertEqual(overdue["profile"]["credit_balance"], 1000)
        self.store.process_billing_event(
            event_id="evt_renewed", event_type="PAYMENT_RECEIVED",
            provider_payment_id="pay_cycle_2", **common,
        )
        renewed = self.store.billing_summary(24)
        self.assertEqual(renewed["profile"]["subscription_status"], "active")
        self.assertEqual(renewed["profile"]["credit_balance"], 2000)
        self.assertEqual(len(renewed["payments"]), 2)

        self.store.process_billing_event(
            provider="asaas", event_id="evt_deleted", event_type="SUBSCRIPTION_DELETED",
            payload_digest="e" * 64, provider_subscription_id="sub_cycle_1",
            subscription_status="INACTIVE",
        )
        canceled = self.store.billing_summary(24)
        self.assertEqual(canceled["profile"]["subscription_status"], "canceled")
        self.assertEqual(canceled["subscription"]["status"], "canceled")
        self.store.process_billing_event(
            event_id="evt_late_payment", event_type="PAYMENT_RECEIVED",
            provider_payment_id="pay_cycle_late", **common,
        )
        late = self.store.billing_summary(24)
        self.assertEqual(late["profile"]["subscription_status"], "canceled")
        self.assertEqual(late["profile"]["credit_balance"], 3000)

        new_order = self.store.create_billing_order(
            24, 7, provider="asaas", kind="subscription", plan_code="growth",
            plan_name="Crescimento", price_cents=14990, credits=1000, cycle="MONTHLY",
        )
        self.store.process_billing_event(
            provider="asaas", event_id="evt_reactivated", event_type="PAYMENT_RECEIVED",
            payload_digest="f" * 64, external_reference=new_order["external_reference"],
            provider_payment_id="pay_reactivated", provider_subscription_id="sub_cycle_2",
            amount_cents=14990,
        )
        reactivated = self.store.billing_summary(24)
        self.assertEqual(reactivated["profile"]["subscription_status"], "active")
        self.assertEqual(reactivated["subscription"]["provider_subscription_id"], "sub_cycle_2")
        self.assertEqual(reactivated["profile"]["credit_balance"], 4000)

    def test_subscription_cancellation_action_is_audited_and_keeps_balance(self):
        self.store.ensure_organization(25, initial_credits=12)
        order = self.store.create_billing_order(
            25, 7, provider="asaas", kind="subscription", plan_code="growth",
            plan_name="Crescimento", price_cents=14990, credits=1000, cycle="MONTHLY",
        )
        self.store.process_billing_event(
            provider="asaas", event_id="evt_active", event_type="PAYMENT_RECEIVED",
            payload_digest="a" * 64, external_reference=order["external_reference"],
            provider_payment_id="pay_active", provider_subscription_id="sub_cancel",
            amount_cents=14990,
        )
        action = self.store.begin_subscription_cancellation(25, 7, reason="Não preciso mais")
        subscription = self.store.complete_subscription_cancellation(action["id"])
        self.assertEqual(subscription["status"], "canceled")
        summary = self.store.billing_summary(25)
        self.assertEqual(summary["profile"]["credit_balance"], 1012)
        self.assertEqual(summary["profile"]["subscription_status"], "canceled")
        self.assertEqual(summary["cancellation"]["status"], "completed")
        with self.assertRaises(SaaSError):
            self.store.begin_subscription_cancellation(25, 7)

    def test_refund_is_flagged_without_creating_an_automatic_negative_balance(self):
        self.store.ensure_organization(26, initial_credits=0)
        order = self.store.create_billing_order(
            26, 7, provider="asaas", kind="subscription", plan_code="growth",
            plan_name="Crescimento", price_cents=14990, credits=1000, cycle="MONTHLY",
        )
        common = {
            "provider": "asaas", "payload_digest": "9" * 64,
            "external_reference": order["external_reference"], "provider_payment_id": "pay_refund",
            "provider_subscription_id": "sub_refund", "amount_cents": 14990,
        }
        self.store.process_billing_event(
            event_id="evt_refund_paid", event_type="PAYMENT_RECEIVED", **common,
        )
        refunded = self.store.process_billing_event(
            event_id="evt_refunded", event_type="PAYMENT_REFUNDED", **common,
        )
        self.assertTrue(refunded["review"])
        summary = self.store.billing_summary(26)
        self.assertEqual(summary["profile"]["credit_balance"], 1000)
        self.assertEqual(summary["profile"]["subscription_status"], "past_due")
        self.assertEqual(summary["payments"][0]["status"], "refunded")
        self.assertEqual(summary["orders"][0]["status"], "needs_review")

    def test_billing_webhook_never_grants_wrong_amount(self):
        self.store.ensure_organization(23, initial_credits=0)
        order = self.store.create_billing_order(
            23, 7, provider="asaas", kind="credit_pack", plan_code="pack_500",
            plan_name="Pacote 500", price_cents=9900, credits=500, cycle=None,
        )
        result = self.store.process_billing_event(
            provider="asaas", event_id="evt_wrong", event_type="PAYMENT_RECEIVED",
            payload_digest="c" * 64, external_reference=order["external_reference"],
            provider_payment_id="pay_wrong", amount_cents=9800,
        )
        self.assertTrue(result["review"])
        self.assertEqual(self.store.billing_summary(23)["profile"]["credit_balance"], 0)
        self.assertEqual(self.store.billing_summary(23)["orders"][0]["status"], "needs_review")

    def test_admin_can_update_profile_and_adjust_credits_with_ledger(self):
        self.store.ensure_organization(13, initial_credits=10)
        profile = self.store.update_billing_profile(
            13, plan_code="growth", subscription_status="past_due", unlimited_credits=False,
        )
        self.assertEqual((profile["plan_code"], profile["subscription_status"]), ("growth", "past_due"))
        adjusted = self.store.adjust_credits(13, 25, description="Ajuste comercial", actor_id=7)
        self.assertEqual(adjusted["credit_balance"], 35)
        summary = self.store.billing_summary(13)
        self.assertEqual(summary["ledger"][0]["kind"], "manual_adjustment")
        self.assertEqual(summary["ledger"][0]["delta"], 25)
        with self.assertRaises(SaaSError):
            self.store.adjust_credits(13, -36, description="Ajuste inválido", actor_id=7)
        self.assertEqual(self.store.billing_summary(13)["profile"]["credit_balance"], 35)

    def test_unlimited_profile_rejects_manual_balance_adjustment(self):
        self.store.ensure_organization(14, unlimited=True)
        with self.assertRaises(SaaSError) as raised:
            self.store.adjust_credits(14, 10, description="Não aplicável", actor_id=7)
        self.assertEqual(raised.exception.status, 409)

    def test_admin_metrics_summarize_commercial_operation(self):
        self.store.ensure_organization(15, initial_credits=4)
        self.store.ensure_organization(16, initial_credits=6)
        self.store.update_billing_profile(16, plan_code="trial", subscription_status="suspended", unlimited_credits=False)
        metrics = self.store.admin_metrics()
        self.assertEqual(metrics["configured_organizations"], 2)
        self.assertEqual(metrics["active_organizations"], 1)
        self.assertEqual(metrics["credits_available"], 10)

    def test_product_activity_tracks_activation_and_distinct_usage_days(self):
        self.store.ensure_organization(19, initial_credits=3)
        self.store.record_product_event(
            19, 31, "search.executed", metadata={"returned": 4},
            occurred_at="2026-08-10T10:00:00+00:00",
        )
        self.store.record_product_event(
            19, 31, "search.executed", metadata={"returned": 2},
            occurred_at="2026-08-11T10:00:00+00:00",
        )
        saved = self.store.create_saved_search(19, 31, name="Clientes", filters={"limit": 10})
        company_list = self.store.create_company_list(19, 31, name="Prioridade")
        self.store.add_companies(19, company_list["id"], 31, [COMPANY_A])

        activity = self.store.admin_product_activity()[19]
        self.assertEqual(activity["search_count"], 2)
        self.assertEqual(activity["saved_search_count"], 1)
        self.assertEqual(activity["list_count"], 1)
        self.assertEqual(activity["unlocked_companies"], 1)
        self.assertGreaterEqual(activity["activity_days"], 3)
        self.assertIsNotNone(activity["last_activity_at"])
        self.assertEqual(self.store.saved_search(19, saved["id"])["name"], "Clientes")

    def test_product_event_deduplication_is_scoped_by_organization(self):
        self.store.ensure_organization(20)
        self.store.ensure_organization(21)
        self.assertTrue(self.store.record_product_event(20, 7, "job.created", deduplication_key="job:one"))
        self.assertFalse(self.store.record_product_event(20, 7, "job.created", deduplication_key="job:one"))
        self.assertTrue(self.store.record_product_event(21, 7, "job.created", deduplication_key="job:one"))
        self.assertEqual(self.store.admin_product_activity()[20]["job_count"], 1)

    def test_notifications_are_user_scoped_idempotent_and_episode_based(self):
        self.store.ensure_organization(17, initial_credits=5)
        jobs = [{"id": "job-1", "filename": "clientes.csv", "status": "completed", "processed": 12}]
        self.store.sync_notifications(17, 21, jobs=jobs, low_credit_threshold=10)
        first = self.store.list_notifications(17, 21)
        self.assertEqual(first["unread_count"], 2)
        self.assertEqual({item["kind"] for item in first["notifications"]}, {"job_completed", "low_credit"})

        self.store.sync_notifications(17, 21, jobs=jobs, low_credit_threshold=10)
        self.assertEqual(len(self.store.list_notifications(17, 21)["notifications"]), 2)
        self.assertEqual(self.store.list_notifications(17, 22)["notifications"], [])

        notification_id = first["notifications"][0]["id"]
        self.assertTrue(self.store.mark_notification_read(17, 21, notification_id))
        self.assertFalse(self.store.mark_notification_read(17, 22, notification_id))
        self.assertEqual(self.store.mark_all_notifications_read(17, 21), 1)
        self.assertEqual(self.store.list_notifications(17, 21)["unread_count"], 0)

        self.store.grant_credits(17, 20, description="Recarga", idempotency_key="payment:notification")
        self.store.sync_notifications(17, 21, jobs=[], low_credit_threshold=10)
        self.store.adjust_credits(17, -20, description="Novo ciclo de consumo", actor_id=21)
        self.store.sync_notifications(17, 21, jobs=[], low_credit_threshold=10)
        notices = self.store.list_notifications(17, 21)["notifications"]
        self.assertEqual(sum(item["kind"] == "low_credit" for item in notices), 2)

    def test_billing_status_notifications_follow_each_issue_episode(self):
        self.store.ensure_organization(18, initial_credits=50)
        self.store.update_billing_profile(
            18, plan_code="growth", subscription_status="past_due", unlimited_credits=False,
        )
        self.store.sync_notifications(18, 31, jobs=[], low_credit_threshold=10)
        self.store.sync_notifications(18, 31, jobs=[], low_credit_threshold=10)
        notices = self.store.list_notifications(18, 31)["notifications"]
        self.assertEqual(sum(item["kind"] == "billing_status" for item in notices), 1)
        self.store.update_billing_profile(
            18, plan_code="growth", subscription_status="active", unlimited_credits=False,
        )
        self.store.sync_notifications(18, 31, jobs=[], low_credit_threshold=10)
        self.store.update_billing_profile(
            18, plan_code="growth", subscription_status="past_due", unlimited_credits=False,
        )
        self.store.sync_notifications(18, 31, jobs=[], low_credit_threshold=10)
        notices = self.store.list_notifications(18, 31)["notifications"]
        self.assertEqual(sum(item["kind"] == "billing_status" for item in notices), 2)

    def test_subscription_renewal_notifications_are_deduplicated_by_due_date(self):
        self.store.ensure_organization(19, initial_credits=0)
        order = self.store.create_billing_order(
            19, 7, provider="asaas", kind="subscription", plan_code="growth",
            plan_name="Crescimento", price_cents=14990, credits=1000, cycle="MONTHLY",
        )
        self.store.process_billing_event(
            provider="asaas", event_id="evt_renewal_notice", event_type="PAYMENT_CONFIRMED",
            payload_digest="7" * 64, external_reference=order["external_reference"],
            provider_payment_id="pay_renewal_notice", provider_subscription_id="sub_renewal_notice",
            amount_cents=14990, subscription_next_due_date="2026-09-16",
        )

        self.store.sync_notifications(
            19, 32, jobs=[], low_credit_threshold=10,
            now=datetime(2026, 9, 8, tzinfo=timezone.utc),
        )
        self.assertEqual(self.store.list_notifications(19, 32)["notifications"], [])

        for _ in range(2):
            self.store.sync_notifications(
                19, 32, jobs=[], low_credit_threshold=10,
                now=datetime(2026, 9, 9, tzinfo=timezone.utc),
            )
        notices = self.store.list_notifications(19, 32)["notifications"]
        self.assertEqual(sum(item["kind"] == "billing_renewal" for item in notices), 1)
        self.assertEqual(notices[0]["action_tab"], "billing")

        self.store.sync_notifications(
            19, 32, jobs=[], low_credit_threshold=10,
            now=datetime(2026, 9, 16, tzinfo=timezone.utc),
        )
        notices = self.store.list_notifications(19, 32)["notifications"]
        self.assertEqual(sum(item["kind"] == "billing_due" for item in notices), 1)

        self.store.process_billing_event(
            provider="asaas", event_id="evt_next_renewal", event_type="SUBSCRIPTION_UPDATED",
            payload_digest="8" * 64, provider_subscription_id="sub_renewal_notice",
            subscription_status="ACTIVE", subscription_next_due_date="2026-10-16",
        )
        self.store.sync_notifications(
            19, 32, jobs=[], low_credit_threshold=10,
            now=datetime(2026, 10, 9, tzinfo=timezone.utc),
        )
        notices = self.store.list_notifications(19, 32)["notifications"]
        self.assertEqual(sum(item["kind"] == "billing_renewal" for item in notices), 2)

    def test_support_tickets_are_tenant_scoped_threaded_and_prioritized(self):
        self.store.ensure_organization(30)
        self.store.ensure_organization(31)
        ticket = self.store.create_support_ticket(
            30, 41,
            requester_identifier="cliente@example.com",
            category="technical",
            priority="high",
            subject="Exportação não conclui",
            message="A exportação fica carregando depois que seleciono as empresas.",
            diagnostic={"request_id": "req-123", "dataset_version": "2026-08"},
        )
        self.assertEqual(ticket["status"], "open")
        self.assertEqual(self.store.list_support_tickets(31), [])
        self.assertIsNone(self.store.support_ticket_detail(31, ticket["id"]))
        detail = self.store.support_ticket_detail(30, ticket["id"])
        self.assertEqual(detail["messages"][0]["author_kind"], "customer")
        self.assertNotIn("diagnostic_json", detail)

        updated = self.store.update_support_ticket(
            ticket["id"], status="in_progress", priority="urgent", actor_id=99,
        )
        self.assertEqual((updated["status"], updated["priority"]), ("in_progress", "urgent"))
        answered = self.store.reply_support_ticket(
            30, ticket["id"], 99, author_kind="support",
            message="Recebemos o chamado e estamos verificando a exportação.",
        )
        self.assertEqual(answered["messages"][-1]["author_kind"], "support")
        notification = self.store.list_notifications(30, 41)["notifications"][0]
        self.assertEqual(notification["kind"], "support_reply")
        self.assertEqual(notification["action_tab"], f"support:{ticket['id']}")
        queue = self.store.admin_support_tickets(status="in_progress")
        self.assertEqual(queue["tickets"][0]["id"], ticket["id"])
        self.assertEqual(queue["counts"]["in_progress"], 1)

    def test_customer_reply_reopens_completed_support_ticket(self):
        self.store.ensure_organization(32)
        ticket = self.store.create_support_ticket(
            32, 51,
            requester_identifier="cliente@example.com",
            category="question",
            priority="normal",
            subject="Como salvar o segmento",
            message="Quero repetir os mesmos filtros na próxima semana.",
            diagnostic={},
        )
        self.store.update_support_ticket(ticket["id"], status="resolved", priority="low", actor_id=99)
        reopened = self.store.reply_support_ticket(
            32, ticket["id"], 51, author_kind="customer",
            message="Ainda preciso de ajuda para encontrar o botão.",
        )
        self.assertEqual(reopened["status"], "open")
        self.assertEqual(len(reopened["messages"]), 2)


if __name__ == "__main__":
    unittest.main()
